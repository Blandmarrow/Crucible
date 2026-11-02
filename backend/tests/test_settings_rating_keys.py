"""The five rebindable keep/cut rating keys on `/settings/thresholds`.

They share the label hotkeys' `[a-z0-9]` charset, `""` unbinds one, and no two
may share a key — checked on the merged row, because a one-key PATCH can collide
with a key already saved.
"""
from backend.tests.conftest import API, api_env, run

_DEFAULT = {
    "rating_key_keep": "4",
    "rating_key_probably": "3",
    "rating_key_probably_not": "2",
    "rating_key_cut": "1",
    "rating_key_clear": "0",
}


def _keys(body: dict) -> dict:
    return {k: body[k] for k in _DEFAULT}


def test_the_defaults_are_the_digits_the_rating_shipped_with(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/settings/thresholds")
            assert r.status_code == 200, r.text
            assert _keys(r.json()) == _DEFAULT
    run(scenario())


def test_a_key_can_be_rebound_and_unbound(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(
                f"{API}/settings/thresholds",
                json={"rating_key_keep": "k", "rating_key_clear": ""},
            )
            assert r.status_code == 200, r.text
            got = _keys((await env.client.get(f"{API}/settings/thresholds")).json())
            assert got == {**_DEFAULT, "rating_key_keep": "k", "rating_key_clear": ""}
    run(scenario())


def test_a_key_outside_the_charset_is_refused(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            for bad in ("K", "ab", " ", "ArrowLeft", "-", "4\n", "\n"):
                r = await env.client.patch(
                    f"{API}/settings/thresholds", json={"rating_key_keep": bad}
                )
                assert r.status_code == 422, (bad, r.text)
    run(scenario())


def test_a_key_already_bound_to_another_rating_is_refused_and_nothing_saves(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            # "3" is Probably's: a one-field PATCH that only collides once merged.
            r = await env.client.patch(
                f"{API}/settings/thresholds",
                json={"rating_key_keep": "3", "blur_threshold": 42.0},
            )
            assert r.status_code == 422, r.text
            body = (await env.client.get(f"{API}/settings/thresholds")).json()
            assert _keys(body) == _DEFAULT
            assert body["blur_threshold"] != 42.0
    run(scenario())


def test_two_unbound_keys_do_not_collide(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(
                f"{API}/settings/thresholds",
                json={"rating_key_keep": "", "rating_key_cut": ""},
            )
            assert r.status_code == 200, r.text
    run(scenario())
