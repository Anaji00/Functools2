"""
MemoryBackend: a tiny in-process LRU store guarded by an async lock.

- Uses OrderedDict to maintain recency order.
- get/set move keys to the end (most recently used).
- Evicts the least-recently-used entry when maxsize is exceeded.
"""

from __future__ import annotations
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Hashable, Optional


@dataclass
class BakcendEntry:
    payload: dict

"""Async LRU map: Hashable -> BackendEntry with O(1) get/set/evict."""
class MemoryBackend:
    def __init__(self, maxsize: int = 256) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        self.maxsize = maxsize
        self._lock = asyncio.Lock()
        self._od: "OrderedDict[Hashable, BakcendEntry]" = OrderedDict()


    """Fetch entry and mark it as most-recently used."""
    async def get(self, key:Hashable) -> Optional[BakcendEntry]:    
        async with self._lock:
            ent = self._od.get(key) # O(1) Dict lookup
            if ent is not None:
                self._od.move_to_end(key)
            return ent
    
    """Insert/update entry and evict LRU if capacity exceeded."""
    async def set(self, key: Hashable, payload: dict) -> None:
        async with self._lock:                            # protect map during mutations
            self._od[key] = BakcendEntry(payload=payload) # Upsert
            self._od.move_to_end(key)                     # Mark as most recent
            # Evict untill size <= maxsize (handles burst insert)
            while len(self._od) > self.maxsize:
                self._od.popitem(last=False) # Pop LRU

    async def delete(self, key: Hashable) -> None:        # Delete Key if present
        async with self._lock:
            self._od.pop(key, None)

    async def clear(self) -> None:                        # Clear all entries   
        async with self._lock:
            self._od.clear()