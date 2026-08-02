"""Request-level tests for the Booru tag-lookup router.

The router is thin — validate, dispatch to one of two service functions, return
whatever they return — but every one of those pieces had zero coverage, and the
dispatch is exactly the kind of thing that silently inverts: `source=gelbooru`
reaching `search_safebooru` returns a plausible-looking list, so nothing in the
UI would show the mistake.

`search_safebooru`/`search_gelbooru` are patched on **the router module**, not
the service module, because the router imported the names at import time. That
also bypasses `booru_service`'s process-wide TTL cache (`_cache`), so no test
here can poison a later one, and no real HTTP request is ever made.

The gelbooru credentials are the only reason the two branches are not one line,
and dropping them degrades silently to unauthenticated (rate-limited) lookups —
so `test_gelbooru_credentials_*` asserts the forwarded values against **literals**.
That used to be an assertion against `settings.gelbooru_api_key`, i.e. the
router's own source compared to itself, which was tautological even then and is
now actively wrong: the router resolves the credentials per request from the DB
(Settings -> API Keys), falling back to the `.env` chain, so what it forwards is
no longer the singleton's value at all.
"""
from backend.config import settings
from backend.tests.conftest import API, api_env, run

SAFE_ROWS = [
    {"tag": "cat", "count": 120, "category": "general", "source": "safebooru"},
    {"tag": "cat_ears", "count": 90, "category": "general", "source": "safebooru"},
]
GEL_ROWS = [{"tag": "cat", "count": 7, "category": "artist", "source": "gelbooru"}]


def _fakes(monkeypatch):
    """Patch both search functions on the router; return the recorded call log."""
    import backend.routers.booru as booru_router

    calls: list[tuple] = []

    async def fake_safebooru(query, limit=20):
        calls.append(("safebooru", query, limit, {}))
        return SAFE_ROWS

    async def fake_gelbooru(query, limit=20, **kwargs):
        calls.append(("gelbooru", query, limit, kwargs))
        return GEL_ROWS

    monkeypatch.setattr(booru_router, "search_safebooru", fake_safebooru)
    monkeypatch.setattr(booru_router, "search_gelbooru", fake_gelbooru)
    return calls


def test_search_dispatches_to_the_named_source(tmp_path, monkeypatch):
    calls = _fakes(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            # Default source is safebooru — the query string need not name it.
            r = await env.client.get(f"{API}/booru/search", params={"q": "cat"})
            assert r.status_code == 200, r.text
            assert r.json() == SAFE_ROWS

            r = await env.client.get(
                f"{API}/booru/search", params={"q": "cat", "source": "gelbooru", "limit": 5}
            )
            assert r.status_code == 200, r.text
            assert r.json() == GEL_ROWS

    run(scenario())

    assert [c[0] for c in calls] == ["safebooru", "gelbooru"]
    assert calls[0][1:3] == ("cat", 20)  # the Query default reached the service
    assert calls[1][1:3] == ("cat", 5)
    # Credentials are forwarded, and only on the gelbooru branch. Their *values* are
    # asserted in test_gelbooru_credentials_* below.
    assert set(calls[1][3]) == {"api_key", "user_id"}


def test_gelbooru_credentials_come_from_the_env_chain_by_default(tmp_path, monkeypatch):
    """With nothing saved in Settings, the .env values reach the service verbatim."""
    calls = _fakes(monkeypatch)
    monkeypatch.setattr(settings, "gelbooru_api_key", "env-key-aaa")
    monkeypatch.setattr(settings, "gelbooru_user_id", "env-user-111")

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(
                f"{API}/booru/search", params={"q": "cat", "source": "gelbooru"}
            )
            assert r.status_code == 200, r.text

    run(scenario())
    assert calls[0][3] == {"api_key": "env-key-aaa", "user_id": "env-user-111"}


def test_gelbooru_credentials_saved_in_settings_override_env(tmp_path, monkeypatch):
    calls = _fakes(monkeypatch)
    monkeypatch.setattr(settings, "gelbooru_api_key", "env-key-aaa")
    monkeypatch.setattr(settings, "gelbooru_user_id", "env-user-111")

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(
                f"{API}/settings/secrets",
                json={"gelbooru_api_key": "db-key-bbb", "gelbooru_user_id": "db-user-222"},
            )
            assert r.status_code == 200, r.text

            r = await env.client.get(
                f"{API}/booru/search", params={"q": "cat", "source": "gelbooru"}
            )
            assert r.status_code == 200, r.text

    run(scenario())
    assert calls[0][3] == {"api_key": "db-key-bbb", "user_id": "db-user-222"}


def test_gelbooru_credentials_fall_back_after_clearing(tmp_path, monkeypatch):
    """Clearing the override in Settings restores the .env credentials, not blanks."""
    calls = _fakes(monkeypatch)
    monkeypatch.setattr(settings, "gelbooru_api_key", "env-key-aaa")
    monkeypatch.setattr(settings, "gelbooru_user_id", "env-user-111")

    async def scenario():
        async with api_env(tmp_path) as env:
            await env.client.patch(
                f"{API}/settings/secrets",
                json={"gelbooru_api_key": "db-key-bbb", "gelbooru_user_id": "db-user-222"},
            )
            await env.client.patch(
                f"{API}/settings/secrets",
                json={"gelbooru_api_key": "", "gelbooru_user_id": ""},
            )
            r = await env.client.get(
                f"{API}/booru/search", params={"q": "cat", "source": "gelbooru"}
            )
            assert r.status_code == 200, r.text

    run(scenario())
    assert calls[0][3] == {"api_key": "env-key-aaa", "user_id": "env-user-111"}


def test_search_rejects_bad_parameters(tmp_path, monkeypatch):
    calls = _fakes(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            # Unknown source: the pattern on the Query rejects it before dispatch,
            # so an unrecognised name can never fall through to safebooru.
            r = await env.client.get(f"{API}/booru/search", params={"q": "cat", "source": "danbooru"})
            assert r.status_code == 422, r.text

            r = await env.client.get(f"{API}/booru/search", params={"q": ""})
            assert r.status_code == 422, r.text

            r = await env.client.get(f"{API}/booru/search")
            assert r.status_code == 422, r.text

            for limit in (0, 101):
                r = await env.client.get(f"{API}/booru/search", params={"q": "cat", "limit": limit})
                assert r.status_code == 422, r.text

    run(scenario())
    assert calls == []  # nothing reached the network-facing layer


def test_autocomplete_returns_tag_rows(tmp_path, monkeypatch):
    calls = _fakes(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(
                f"{API}/booru/autocomplete", json={"prefix": "ca", "limit": 3}
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert isinstance(body, list) and body == SAFE_ROWS
            assert set(body[0]) == {"tag", "count", "category", "source"}

            r = await env.client.post(
                f"{API}/booru/autocomplete", json={"prefix": "ca", "source": "gelbooru"}
            )
            assert r.status_code == 200, r.text
            assert r.json() == GEL_ROWS

            # `prefix` is required; the body model has no default for it.
            r = await env.client.post(f"{API}/booru/autocomplete", json={"limit": 3})
            assert r.status_code == 422, r.text

    run(scenario())

    assert [c[0] for c in calls] == ["safebooru", "gelbooru"]
    assert calls[0][1:3] == ("ca", 3)
    assert calls[1][1:3] == ("ca", 10)  # AutocompleteRequest.limit default


def test_authenticated_lookup_is_not_served_from_the_anonymous_cache_entry(monkeypatch):
    """The one test here that patches `booru_service` rather than the router.

    `search_gelbooru`'s 5-minute TTL cache used to key on `gelbooru:{query}:{limit}` with no
    credential component, so the first anonymous lookup poisoned the next five minutes: a
    user saved a key in Settings, searched the same tag, and got the unauthenticated result
    back — the save looked broken. The key now carries an authenticated-or-not flag, so the
    two land in different slots. The flag is deliberately not a credential hash: a key
    changes the rate limit, not the response, and a raw credential in a process-global dict
    key would survive into any future debug dump.
    """
    import backend.services.booru_service as bs

    anon_rows = [{"tag": "cat", "count": 1, "category": "general", "source": "gelbooru"}]

    class _NoNetwork:  # any attempt to leave the process is a failure, swallowed as []
        def __init__(self, *a, **k):
            raise RuntimeError("no network in tests")

    monkeypatch.setattr(bs, "_cache", {})  # replaced, so nothing leaks into other tests
    monkeypatch.setattr(bs.aiohttp, "ClientSession", _NoNetwork)
    monkeypatch.setattr(bs, "_CACHE_TTL", 300)
    bs._cache_set("gelbooru:0:cat:20", anon_rows)

    async def scenario():
        # The anonymous caller still hits the seeded entry...
        assert await bs.search_gelbooru("cat", 20) == anon_rows
        # ...while the authenticated one misses, tries the network, and gets nothing.
        assert await bs.search_gelbooru("cat", 20, api_key="k", user_id="u") == []

    run(scenario())
