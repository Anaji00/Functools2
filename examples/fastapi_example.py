from fastapi import FastAPI
from functools2.async_lru import async_lru_cache  # import explicitly
import random
import time
import asyncio

app = FastAPI()

@async_lru_cache(ttl=10, maxsize=512, swr=True)
async def get_price(symbol: str) -> float:
    await asyncio.sleep(0.2)
    return 100.0 + random.random()

@app.get("/price/{symbol}")
async def price(symbol: str):
    t0 = time.perf_counter()
    val = await get_price(symbol)
    dt = (time.perf_counter() - t0)
    return {"symbol": symbol, "price": val, "elapsed_sec": round(dt, 4)}

# NEW: clear the cache
@app.post("/debug/clear")
async def clear():
    await get_price.cache_clear()
    return {"cleared": True}

# NEW: best-effort cache info
@app.get("/debug/info")
async def info():
    return await get_price.cache_info()
