import asyncio
import re
import time
from typing import Any

import aiohttp

# In-memory tag cache: key -> (timestamp, data)
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes
_semaphore = asyncio.Semaphore(2)  # max 2 concurrent Booru requests

# Danbooru's API. safebooru.donmai.us is Danbooru's SFW mirror over the same tag table, and
# by November 2026 its Cloudflare front answered every API client with a 403 challenge, so the
# Safebooru source reads the tag table from the main host. Cloudflare there also refuses
# aiohttp's default User-Agent, hence the fixed, generic one below (no install details).
_DANBOORU = "https://danbooru.donmai.us"
_USER_AGENT = "Crucible (dataset tool)"
_TIMEOUT = aiohttp.ClientTimeout(total=10)


class BooruUnavailable(Exception):
    """A booru request failed: unreachable, timed out, refused or answered with non-JSON.

    Raised rather than swallowed into an empty list, so a caller can tell an outage from a
    search that found nothing. The message is shown to the user as is.
    """


async def _danbooru_get(path: str, params: dict[str, Any]) -> Any:
    """GET a Danbooru JSON endpoint; return the parsed body or raise `BooruUnavailable`.

    Shares the module semaphore with every other booru call. A Cloudflare challenge is an
    HTML page, often with a 403, so a non-JSON body is an outage, never an empty result.
    """
    async with _semaphore:
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _USER_AGENT}) as session:
                async with session.get(f"{_DANBOORU}{path}", params=params, timeout=_TIMEOUT) as resp:
                    if resp.status != 200:
                        raise BooruUnavailable(f"Could not reach Danbooru (HTTP {resp.status})")
                    try:
                        return await resp.json(content_type=None)
                    except ValueError as e:
                        raise BooruUnavailable("Could not reach Danbooru (the reply was not JSON)") from e
        except BooruUnavailable:
            raise
        except asyncio.TimeoutError as e:
            raise BooruUnavailable("Could not reach Danbooru (timed out)") from e
        except aiohttp.ClientError as e:
            raise BooruUnavailable(f"Could not reach Danbooru ({type(e).__name__})") from e


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
    """Tag search for the Safebooru source, read from Danbooru's tag table.

    Raises `BooruUnavailable` on any failure; a search that matched nothing is `[]`.
    """
    cache_key = f"safebooru:{query}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    data = await _danbooru_get("/tags.json", {
        "search[name_matches]": f"*{query}*",
        "search[order]": "count",
        "limit": limit,
    })
    results = [
        {
            "tag": t.get("name", ""),
            "count": t.get("post_count", 0),
            "category": _safebooru_category(t.get("category", 0)),
            "source": "safebooru",
        }
        for t in (data if isinstance(data, list) else [])
    ]
    _cache_set(cache_key, results)
    return results


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


# --- Check tags (#50) --------------------------------------------------------------------

RARE_POSTS = 100    # a tag under this many posts is a candidate for "rare"...
RARE_RATIO = 100    # ...flagged only when a close tag has at least this many times more
MAX_SUGGESTED = 10  # flagged tags that get an autocomplete lookup, one request each
_NAME_CHUNK = 100   # names per name_comma request, keeping the URL well under 8 KB
_WS = re.compile(r"\s+")


def normalize_tag(raw: str) -> str:
    """A caption part as a Danbooru tag name: unescaped parens, lowercase, `_` for spaces."""
    s = raw.strip().replace("\\(", "(").replace("\\)", ")")
    return _WS.sub("_", s.lower())


async def _batch_lookup(
    names: list[str], kind: str, path: str, comma_param: str, params: dict[str, Any],
    key_field: str, value_of,
) -> dict[str, Any]:
    """name -> value for each name, from the cache or one `*_comma` request per chunk.

    A name the reply leaves out is cached as absent (`False`), so a repeat check of the same
    caption makes no request at all. Raises `BooruUnavailable` like `_danbooru_get`.
    """
    out: dict[str, Any] = {}
    todo: list[str] = []
    for n in dict.fromkeys(names):
        cached = _cache_get(f"danbooru:{kind}:{n}")
        if cached is None:
            todo.append(n)
        else:
            out[n] = cached or None
    for i in range(0, len(todo), _NAME_CHUNK):
        chunk = todo[i:i + _NAME_CHUNK]
        data = await _danbooru_get(path, {**params, comma_param: ",".join(chunk), "limit": len(chunk)})
        found = {r.get(key_field): value_of(r) for r in (data if isinstance(data, list) else [])}
        for n in chunk:
            value = found.get(n)
            _cache_set(f"danbooru:{kind}:{n}", value or False)
            out[n] = value or None
    return out


async def _lookup_tags(names: list[str]) -> dict[str, dict | None]:
    """name -> Danbooru tag row (`post_count`, `category`), or None when no such tag."""
    return await _batch_lookup(
        names, "tag", "/tags.json", "search[name_comma]",
        {"only": "name,post_count,category"},
        "name", lambda r: r,
    )


async def _lookup_aliases(names: list[str]) -> dict[str, str | None]:
    """name -> the tag an active Danbooru alias renames it to, or None."""
    return await _batch_lookup(
        names, "alias", "/tag_aliases.json", "search[antecedent_name_comma]",
        {"search[status]": "active", "only": "antecedent_name,consequent_name"},
        "antecedent_name", lambda r: r.get("consequent_name"),
    )


async def _autocomplete(name: str) -> list[tuple[str, int]]:
    """Danbooru's tag autocomplete for `name`: typo corrections and prefix completions."""
    key = f"danbooru:ac:{name}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    data = await _danbooru_get("/autocomplete.json", {
        "search[query]": name,
        "search[type]": "tag_query",
        "only": "value,post_count",
        "limit": 5,
    })
    rows = [
        (str(r.get("value", "")), int(r.get("post_count") or 0))
        for r in (data if isinstance(data, list) else [])
    ]
    _cache_set(key, rows)
    return rows


def _variants(name: str) -> list[str]:
    """Close spellings worth a lookup: each word dropped once, and singular/plural."""
    words = name.split("_")
    out = ["_".join(words[:i] + words[i + 1:]) for i in range(len(words))] if len(words) > 1 else []
    out.append(name[:-1] if name.endswith("s") and len(name) > 2 else name + "s")
    return [v for v in dict.fromkeys(out) if v and v != name]


async def check_tags(parts: list[str]) -> dict:
    """Look a caption's tags up on Danbooru and say which ones it would spell differently.

    Each result is `ok`, `alias` (an active alias renames it), `missing` (no such tag, or no
    posts) or `rare` (under `RARE_POSTS` posts while a close tag has `RARE_RATIO` times more).
    A rare tag with no such neighbour is `ok`: niche but real. The two batch lookups raise
    `BooruUnavailable`; the suggestion step only degrades, to `suggestions_complete: false`,
    so an outage never reads as every tag "not found". A rare tag whose neighbours could not
    be looked up stays `rare` with no suggestion, since it is undecided rather than fine.
    """
    items = [(p, normalize_tag(p)) for p in parts]
    items = [(p, n) for p, n in items if n]
    names = list(dict.fromkeys(n for _, n in items))

    rows: dict[str, dict | None] = await _lookup_tags(names)
    aliases = await _lookup_aliases(names)

    def count(n: str) -> int:
        row = rows.get(n)
        return int(row.get("post_count") or 0) if row else 0

    flagged = [n for n in names if not aliases.get(n) and count(n) < RARE_POSTS]
    to_suggest = flagged[:MAX_SUGGESTED]
    complete = len(flagged) <= MAX_SUGGESTED

    # Candidate counts: alias targets and spelling variants in one batch, autocomplete per tag.
    variants = {n: _variants(n) for n in to_suggest}
    looked_up = True
    try:
        rows.update(await _lookup_tags(
            [a for a in aliases.values() if a] + [v for vs in variants.values() for v in vs]
        ))
    except BooruUnavailable:
        complete = looked_up = False
    completions = await asyncio.gather(*(_autocomplete(n) for n in to_suggest), return_exceptions=True)
    best: dict[str, tuple[str, int]] = {}
    decided: set[str] = set()
    for n, ac in zip(to_suggest, completions):
        candidates = [(v, count(v)) for v in variants[n]]
        if isinstance(ac, BaseException):
            complete = False
        else:
            candidates += ac
            if looked_up:
                decided.add(n)
        candidates = [(c, k) for c, k in candidates if c and c != n and k > 0]
        if candidates:
            best[n] = max(candidates, key=lambda ck: ck[1])

    results = []
    for part, n in items:
        row = rows.get(n)
        k = count(n)
        status, suggestion = "ok", None
        cand = best.get(n)
        if aliases.get(n):
            status = "alias"
            suggestion = {"name": aliases[n], "count": count(aliases[n])}
        elif k == 0:
            status = "missing"
            if cand:
                suggestion = {"name": cand[0], "count": cand[1]}
        elif k < RARE_POSTS:
            if cand and cand[1] >= RARE_RATIO * k:
                status, suggestion = "rare", {"name": cand[0], "count": cand[1]}
            elif n not in decided:
                status = "rare"
        results.append({
            "input": part,
            "name": n,
            "status": status,
            "count": k,
            "category": _safebooru_category(row.get("category", 0)) if row else None,
            "suggestion": suggestion,
        })
    return {"results": results, "suggestions_complete": complete}
