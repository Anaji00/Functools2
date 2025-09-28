# Tests cover: TTL behavior, singleflight under concurrency, SWR refresh, and exception policy.

import asyncio                           # simulate IO delays
import pytest                            # test framework
import anyio                             # handy for concurrent gathers

from functools2 import async_lru_cache   # public API import

@pytest.mark.anyio
async def test_ttl_basic_and_expiry():
    calls = 0

    @async_lru_cache(ttl=0.2, maxsize=8)   # 200ms freshness
    async def double(x: int) -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)          # pretend expensive
        return x * 2

    # First call → miss (compute)
    assert await double(3) == 6
    # Second call (within TTL) → hit (no compute)
    assert await double(3) == 6
    assert calls == 1

    # Let TTL expire
    await asyncio.sleep(0.25)
    # Recompute after expiry
    assert await double(3) == 6
    assert calls == 2

@pytest.mark.anyio
async def test_singleflight_concurrent_miss_only_one_compute():
    calls = 0

    @async_lru_cache(ttl=1.0, maxsize=8)
    async def slow(x: int) -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)          # simulate upstream latency
        return x

    results = []
    async def call():
        results.append(await slow(42))

    # 50 concurrent callers for same key → single compute
    async with anyio.create_task_group() as tg:
        for _ in range(50):
            tg.start_soon(call)

    assert set(results) == {42}
    assert calls == 1                      # proves singleflight

@pytest.mark.anyio
async def test_swr_refresh():
    calls = 0

    @async_lru_cache(ttl=0.1, maxsize=8, swr=True)
    async def val() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)          # simulate IO
        return calls

    # First call computes (calls=1)
    a = await val()
    assert a == 1

    # Wait until stale
    await asyncio.sleep(0.12)

    # SWR: returns stale immediately (1) and triggers background refresh
    b = await val()
    assert b == 1

    # Give background task time to finish
    await asyncio.sleep(0.05)

    # Now value should be refreshed (calls=2)
    c = await val()
    assert c == 2

@pytest.mark.anyio
async def test_no_cache_on_exception_by_default():
    calls = 0

    @async_lru_cache(ttl=1.0, maxsize=8)   # cache_errors defaults to False
    async def boom(n: int) -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        raise RuntimeError("x")

    # First call raises
    with pytest.raises(RuntimeError):
        await boom(1)
    # Second call raises again (not cached)
    with pytest.raises(RuntimeError):
        await boom(1)

    # We computed twice because failures are not cached by default
    assert calls == 2
