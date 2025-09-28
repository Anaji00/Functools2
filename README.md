 # functools2

 A modern, async-aware LRU cache for Python.

 `functools2` provides `@async_lru_cache`, a powerful and easy-to-use decorator for caching the results of `async` functions. It's designed for performance and reliability, offering advanced features out-of-the-box to handle the complexities of concurrent applications.

 ## Key Features

 *   **Async First**: Built from the ground up for `asyncio`.
 *   **Time-to-Live (TTL)**: Automatically expire cache entries after a set duration.
 *   **Stale-While-Revalidate (SWR)**: Serve stale data for high-speed reads while a background task refreshes the cache, preventing latency spikes.
 *   **Single-Flight Execution**: Protects against the "thundering herd" problem by ensuring that for any given key, a slow function is only executed once, even if called concurrently.
 *   **LRU Eviction**: Uses a Least Recently Used (LRU) policy to keep the cache size bounded.
 *   **Error Caching**: Optionally cache exceptions to prevent slow-running functions from being repeatedly called if they are failing.
 *   **Pluggable Backends**: Defaults to a thread-safe in-memory LRU cache, but can be extended with custom backends (e.g., Redis, Memcached).

 ## Installation

 ```bash
 pip install functools2
 ```

 ## Basic Usage

 Simply apply the `@async_lru_cache` decorator to any `async` function.

 ```python
 import asyncio
 from functools2 import async_lru_cache

 @async_lru_cache(ttl=60) # Cache results for 60 seconds
 async def get_user_data(user_id: int) -> dict:
     print(f"Fetching data for user {user_id} from a slow API...")
     await asyncio.sleep(1) # Simulate a slow network call
     return {"id": user_id, "name": "Jane Doe"}

 async def main():
     # First call: slow (will print the message and wait 1s)
     user1 = await get_user_data(123)
     print(user1)

     # Second call: fast (result is returned from cache instantly)
     user2 = await get_user_data(123)
     print(user2)

 asyncio.run(main())
 ```

 ## Advanced Features

 ### Stale-While-Revalidate (SWR)

 SWR reduces latency by immediately returning a stale (expired) value while simultaneously triggering a background task to refresh it. This is ideal for applications where showing slightly old data is better than making the user wait.

 ```python
 @async_lru_cache(ttl=1, swr=True)
 async def get_live_data():
     # ... slow computation ...
     return "data"

 # First call is computed
 await get_live_data()

 # Wait for the cache to expire
 await asyncio.sleep(1.5)

 # This call returns the stale value instantly
 # and starts a background task to fetch the new value.
 await get_live_data()
 ```

 ### Single-Flight Execution

 If multiple coroutines request the same uncached resource simultaneously, only one will perform the computation. The others will wait for the result, avoiding redundant work. This happens automatically with no extra configuration.

 ```python
 import anyio

 @async_lru_cache(ttl=10)
 async def slow_operation():
     print("Executing slow operation...")
     await asyncio.sleep(1)
     return 42

 async def main():
     # Run 10 concurrent calls
     async with anyio.create_task_group() as tg:
         for _ in range(10):
             tg.start_soon(slow_operation)

     # "Executing slow operation..." will only be printed once!

 asyncio.run(main())
 ```

 ### Error Caching

 By default, exceptions are not cached. If a decorated function raises an exception, subsequent calls will re-execute it. You can change this behavior with `cache_errors=True`.

 ```python
 @async_lru_cache(ttl=10, cache_errors=True)
 async def might_fail():
     raise ValueError("Something went wrong")

 # The first call will raise ValueError.
 # Subsequent calls within the TTL will also immediately raise the cached exception
 # without re-running the function.
 ```

 ## Example with FastAPI

 `async_lru_cache` is a perfect fit for web services, where it can dramatically reduce latency and database load.

 ```python
 from fastapi import FastAPI
 from functools2.async_lru import async_lru_cache
 import random
 import time
 import asyncio

 app = FastAPI()

 @async_lru_cache(ttl=10, maxsize=512, swr=True)
 async def get_price(symbol: str) -> float:
     # Pretend this hits a slow remote API
     await asyncio.sleep(0.2)
     return 100.0 + random.random()

 @app.get("/price/{symbol}")
 async def price(symbol: str):
     t0 = time.perf_counter()
     val = await get_price(symbol)
     dt = time.perf_counter() - t0
     # First hit per symbol waits ~200ms.
     # Subsequent hits within 10s are instant.
     # After 10s, the first caller gets a stale price immediately
     # while a background task fetches the new one.
     return {"symbol": symbol, "price": val, "elapsed_sec": round(dt, 4)}
 
 # Expose cache management functions via an API
 @app.post("/debug/clear_cache")
 async def clear_cache():
     await get_price.cache_clear()
     return {"status": "cache cleared"}
 
 @app.get("/debug/cache_info")
 async def cache_info():
     return await get_price.cache_info()
 ```

 ## API Reference

 `async_lru_cache(ttl, maxsize, swr, cache_errors, key_fn, backend)`

 | Parameter      | Type                               | Default         | Description                                                                                             |
 | -------------- | ---------------------------------- | --------------- | ------------------------------------------------------------------------------------------------------- |
 | `ttl`          | `float`                            | `60.0`          | Time-to-live in seconds. Cached entries are considered stale after this duration.                       |
 | `maxsize`      | `int`                              | `256`           | The maximum number of entries to store in the default `MemoryBackend`.                                  |
 | `swr`          | `bool`                             | `False`         | If `True`, serve stale content while revalidating in the background.                                    |
 | `cache_errors` | `bool`                             | `False`         | If `True`, cache exceptions and re-raise them on subsequent calls.                                      |
 | `key_fn`       | `Callable`                         | `_default_key`  | A function to create a cache key from function arguments.                                               |
 | `backend`      | `Optional[YourBackend]`            | `MemoryBackend` | An instance of a cache backend. Defaults to an in-memory LRU cache.                                     |

 --- Alessio Naji-Sepasgozar - 2025