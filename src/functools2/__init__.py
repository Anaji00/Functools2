# Re-export the decorator so users can: from functools2 import async_lru_cache
from functools2.async_lru import async_lru_cache

# Limit what appears on star-imports
__all__ = ["async_lru_cache"]
