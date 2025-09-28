# Export MemoryBackend as the default in-process LRU store
from .memory import MemoryBackend

# Keep __all__ tight for predictable imports
__all__ = ["MemoryBackend"]
