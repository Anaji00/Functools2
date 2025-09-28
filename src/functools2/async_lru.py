"""
Async-aware LRU cache decorator with TTL, SWR, and correct singleflight.

Key improvement over the original backend version:
- Singleflight now uses a PROCESS-LOCAL PER-KEY LOCK MAP that is NOT persisted.
  All callers for the same key acquire the SAME lock object → only one compute.
"""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Awaitable, Callable, Dict, Hashable, Optional, Tuple, TypeVar, ParamSpec

# Import your in-process LRU backend (pluggable)
from functools2.backends.memory import MemoryBackend

P = ParamSpec("P")
R = TypeVar("R")

# ---------- key freezing (deterministic, hashable keys) ----------

def _freeze(value: Any) -> Hashable:
    """Recursively convert containers into hashable canonical forms."""
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    try:
        hash(value)
        return value
    except TypeError as e:
        raise TypeError(f"Unhashable argument for cache key: {value!r}") from e

def _default_key(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Hashable:
    """Default key: (frozen args, frozen kwargs)."""
    return (_freeze(args), _freeze(kwargs))

# ---------- async_lru_cache decorator ----------

def async_lru_cache(
    ttl: float = 60.0,                                            # time a value is considered fresh
    maxsize: int = 256,                                           # capacity for default MemoryBackend
    swr: bool = False,                                            # serve stale; refresh in background
    cache_errors: bool = False,                                   # opt-in: cache exceptions
    key_fn: Callable[[Tuple[Any, ...], Dict[str, Any]], Hashable] = _default_key,
    backend: Optional[MemoryBackend] = None,                      # allow swapping backends (Redis later)
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    Decorator for async functions with TTL, SWR, and singleflight.
    Uses a pluggable backend for storage and a process-local lock map for coordination.
    """
    store = backend or MemoryBackend(maxsize=maxsize)

    # --- PROCESS-LOCAL PER-KEY LOCK MAP ---
    # This is the crucial fix. Locks never get serialized; all callers share these objects.
    _locks: Dict[Hashable, asyncio.Lock] = {}
    _locks_guard = asyncio.Lock()  # protects creation of new per-key locks

    async def _get_lock(key: Hashable) -> asyncio.Lock:
        """Return the singleton lock for this key; create it once if needed."""
        async with _locks_guard:
            lk = _locks.get(key)
            if lk is None:
                lk = asyncio.Lock()
                _locks[key] = lk
            return lk

    async def _load_payload(key: Hashable) -> Optional[dict]:
        """
        Load raw payload from backend. MemoryBackend returns BackendEntry with .payload.
        Payload schema: {"value": ..., "ts": float, "args": tuple, "kwargs": dict, "exc_repr": str|None}
        """
        ent = await store.get(key)
        if ent is None:
            return None
        return ent.payload if hasattr(ent, "payload") else ent  # support simple dict backends

    async def _save_payload(key: Hashable, payload: dict) -> None:
        """Persist payload to backend (LRU eviction handled by backend)."""
        await store.set(key, payload)

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        if not asyncio.iscoroutinefunction(fn):
            raise TypeError("@async_lru_cache requires an async function")

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """
            Execution flow:
              1) compute deterministic key
              2) fast fresh-hit path
              3) SWR: serve stale + spawn background refresh
              4) singleflight: compute under per-key lock
            """
            key = key_fn(args, kwargs)
            now = time.monotonic()

            payload = await _load_payload(key)

            # --- 1) Fast fresh-hit path ---
            if payload is not None:
                ts = payload.get("ts", 0.0)
                exc = payload.get("exc_repr")
                val = payload.get("value")
                if (now - ts) < ttl and exc is None and val is not None:
                    return val  # type: ignore[return-value]

            # --- 2) SWR path: stale but usable value, and swr=True ---
            if swr and payload is not None:
                ts = payload.get("ts", 0.0)
                exc = payload.get("exc_repr")
                val = payload.get("value")
                if (now - ts) >= ttl and exc is None and val is not None:
                    # Serve stale immediately; refresh in background under the same key lock.
                    asyncio.create_task(_refresh_under_lock(fn, key, payload))
                    return val  # type: ignore[return-value]

            # --- 3) Miss or stale without SWR → singleflight compute ---
            # We need a per-key lock that EVERY caller shares.
            lk = await _get_lock(key)
            async with lk:
                # Double-checked locking: someone else may have populated while we waited.
                now2 = time.monotonic()
                latest = await _load_payload(key)
                if latest is not None:
                    ts2 = latest.get("ts", 0.0)
                    exc2 = latest.get("exc_repr")
                    val2 = latest.get("value")
                    if (now2 - ts2) < ttl and exc2 is None and val2 is not None:
                        return val2  # type: ignore[return-value]

                # We are the winner: compute and store.
                try:
                    result: R = await fn(*args, **kwargs)
                    new_payload = {
                        "value": result,
                        "ts": time.monotonic(),
                        "args": args,            # keep last args/kwargs for SWR refresh hygiene
                        "kwargs": kwargs,
                        "exc_repr": None,
                    }
                    await _save_payload(key, new_payload)
                    return result
                except BaseException as e:
                    # Policy: don't cache errors by default. Opt-in caches set exc_repr.
                    if cache_errors:
                        err_payload = {
                            "value": None,
                            "ts": time.monotonic(),
                            "args": args,
                            "kwargs": kwargs,
                            "exc_repr": repr(e),
                        }
                        await _save_payload(key, err_payload)
                    raise

        async def _refresh_under_lock(fn: Callable[P, Awaitable[R]], key: Hashable, payload: dict) -> None:
            """
            Background refresher for SWR.
            Ensures only ONE refresher runs:
              - check the same per-key lock; if it's held, skip
              - otherwise, acquire, recompute, and update payload
            """
            lk = await _get_lock(key)
            # Avoid queueing behind a long compute: if someone is already computing, we can skip.
            if lk.locked():
                return
            async with lk:
                # Reload latest args/kwargs for this key just before refresh
                latest = await _load_payload(key)
                if latest is None:
                    return
                latest_args = tuple(latest.get("args", ()))
                latest_kwargs = dict(latest.get("kwargs", {}))
                try:
                    new_val = await fn(*latest_args, **latest_kwargs)
                    new_payload = {
                        "value": new_val,
                        "ts": time.monotonic(),
                        "args": latest_args,
                        "kwargs": latest_kwargs,
                        "exc_repr": None,
                    }
                    await _save_payload(key, new_payload)
                except BaseException as e:
                    if cache_errors:
                        err_payload = {
                            "value": None,
                            "ts": time.monotonic(),
                            "args": latest_args,
                            "kwargs": latest_kwargs,
                            "exc_repr": repr(e),
                        }
                        await _save_payload(key, err_payload)
                    # If not caching errors, keep old stale value; next request can trigger refresh again.

        # Optional helper methods (introspect / manage cache) -----------------
        async def cache_invalidate(*args_i, **kwargs_i) -> bool:
            """Drop a specific key from the backend, if present."""
            k = key_fn(args_i, kwargs_i)
            delete_coro = getattr(store, "delete", None)
            if delete_coro is None:
                return False
            # MemoryBackend has delete(); for other backends you may need to implement it.
            try:
                await delete_coro(k)
            finally:
                _locks.pop(k, None)  # also drop the per-key lock if any
            return True
        
        async def cache_clear() -> None:
            """Drop all keys from the backend."""
            clear_coro = getattr(store, "clear", None)
            if clear_coro is not None:
                await clear_coro()
            _locks.clear()  # drop all per-key locks

        # inside async_lru_cache decorator (backend version)
        async def cache_info() -> dict:
            info = {"backend": type(store).__name__, "ttl": ttl, "swr": swr,
                    "cache_errors": cache_errors, "currsize": None}
            od = getattr(store, "_od", None)   # MemoryBackend internal
            if od is not None:
                try: info["currsize"] = len(od)
                except Exception: pass
            return info

        wrapper.cache_info = cache_info  # type: ignore[attr-defined]
        wrapper.cache_invalidate = cache_invalidate  # type: ignore[attr-defined]
        wrapper.cache_clear = cache_clear            # type: ignore[attr-defined]
        return wrapper

    return decorator
