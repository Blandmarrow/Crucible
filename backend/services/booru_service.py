import asyncio
import time
from typing import Any

import aiohttp

# In-memory tag cache: key -> (timestamp, data)
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes
_semaphore = asyncio.Semaphore(2)  # max 2 concurrent Booru requests


def _cache_get(key: str) -> Any | None:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
        del _cache[key]  # evict expired entry so it doesn't accumulate indefinitely
    return None


def _cache_set(key: str, data: Any) -> None:
    _cache[key] = (time.time(), data)


async def search_safebooru(query: str, limit: int = 20) -> list[dict]:
    cache_key = f"safebooru:{query}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    url = "https://safebooru.donmai.us/tags.json"
    params = {
        "search[name_matches]": f"*{query}*",
        "search[order]": "count",
        "limit": limit,
    }
    async with _semaphore:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = [
                            {
                                "tag": t.get("name", ""),
                                "count": t.get("post_count", 0),
                                "category": _safebooru_category(t.get("category", 0)),
                                "source": "safebooru",
                            }
                            for t in data
                        ]
                        _cache_set(cache_key, results)
                        return results
        except Exception:
            pass
    return []


async def search_gelbooru(query: str, limit: int = 20, api_key: str = "", user_id: str = "") -> list[dict]:
    # Namespaced by *whether* the request is authenticated, not by which credentials: a key
    # changes the rate limit, not the response content, so distinguishing key-A from key-B
    # buys nothing, and a raw credential in a process-global dict key would survive into any
    # future debug dump. The flag alone fixes the real symptom — without it, saving a key in
    # Settings served the stale anonymous entry for five minutes and the save looked broken.
    cache_key = f"gelbooru:{int(bool(api_key and user_id))}:{query}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    url = "https://gelbooru.com/index.php"
    params: dict[str, Any] = {
        "page": "dapi",
        "s": "tag",
        "q": "index",
        "json": "1",
        "name_pattern": f"%{query}%",
        "orderby": "count",
        "limit": limit,
    }
    if api_key and user_id:
        params["api_key"] = api_key
        params["user_id"] = user_id

    async with _semaphore:
        await asyncio.sleep(0.5)  # be polite
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        tags = data.get("tag", []) if isinstance(data, dict) else data
                        results = [
                            {
                                "tag": t.get("name", ""),
                                "count": t.get("count", 0),
                                "category": _gelbooru_category(t.get("type", 0)),
                                "source": "gelbooru",
                            }
                            for t in tags
                        ]
                        _cache_set(cache_key, results)
                        return results
        except Exception:
            pass
    return []


def _safebooru_category(cat_id: int) -> str:
    return {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}.get(cat_id, "general")


def _gelbooru_category(cat_id: int) -> str:
    return {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}.get(cat_id, "general")
