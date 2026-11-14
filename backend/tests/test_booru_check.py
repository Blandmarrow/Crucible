"""Check tags (#50): `POST /booru/check` and `booru_service.check_tags`.

Danbooru is faked at `booru_service._danbooru_get`, the one function that leaves the
process, so every request the check would send is recorded and none is real. `_cache` is
replaced per test, so a cached answer never leaks between tests; the cache tests seed it on
purpose.

The fake tag table mirrors what Danbooru answered on 2026-11-14: `yellow_hair` exists with
0 posts and an active alias to `blonde_hair`, `standing_up` has 78 posts beside `standing`'s
1.9M, and autocomplete corrects `schol_uniform` to `school_uniform`.
"""
import pytest

import backend.services.booru_service as bs
from backend.tests.conftest import API, api_env, run

TAGS = {
    "blonde_hair": 1_600_000,
    "yellow_hair": 0,
    "school_uniform": 1_087_767,
    "standing": 1_900_000,
    "standing_up": 78,
    "up": 900,
    "cat_ears": 420_512,
    "niche_tag": 40,
    "niche": 3000,  # under 100x niche_tag's 40, so niche_tag is fine
    "artoria_pendragon_(fate)": 90_000,
}
ALIASES = {"yellow_hair": "blonde_hair"}
AUTOCOMPLETE = {"schol_uniform": [("school_uniform", 1_087_767)]}


class FakeDanbooru:
    def __init__(self, fail_paths: tuple[str, ...] = ()):
        self.calls: list[tuple[str, dict]] = []
        self.fail_paths = fail_paths

    async def __call__(self, path, params):
        self.calls.append((path, params))
        if path in self.fail_paths:
            raise bs.BooruUnavailable("Could not reach Danbooru (HTTP 403)")
        if path == "/tags.json":
            names = params["search[name_comma]"].split(",")
            return [{"name": n, "post_count": TAGS[n], "category": 0} for n in names if n in TAGS]
        if path == "/tag_aliases.json":
            names = params["search[antecedent_name_comma]"].split(",")
            return [{"antecedent_name": n, "consequent_name": ALIASES[n]} for n in names if n in ALIASES]
        if path == "/autocomplete.json":
            q = params["search[query]"]
            return [{"value": v, "post_count": c} for v, c in AUTOCOMPLETE.get(q, [])]
        raise AssertionError(f"unexpected path {path}")


@pytest.fixture
def danbooru(monkeypatch):
    monkeypatch.setattr(bs, "_cache", {})
    fake = FakeDanbooru()
    monkeypatch.setattr(bs, "_danbooru_get", fake)
    return fake


def _by_input(body):
    return {r["input"]: r for r in body["results"]}


def test_check_classifies_each_tag(tmp_path, danbooru):
    caption = ["yellow hair", "schol uniform", "standing up", "cat_ears", "niche tag"]

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{API}/booru/check", json={"tags": caption})
            assert r.status_code == 200, r.text
            return r.json()

    body = run(scenario())
    res = _by_input(body)
    assert body["suggestions_complete"] is True
    assert [r["input"] for r in body["results"]] == caption  # order kept, input as written

    assert res["yellow hair"]["status"] == "alias"
    assert res["yellow hair"]["suggestion"] == {"name": "blonde_hair", "count": 1_600_000}

    assert res["schol uniform"]["status"] == "missing"
    assert res["schol uniform"]["suggestion"] == {"name": "school_uniform", "count": 1_087_767}

    # 78 posts, and "standing" (a word dropped) has far more than 100x that.
    assert res["standing up"]["status"] == "rare"
    assert res["standing up"]["count"] == 78
    assert res["standing up"]["suggestion"] == {"name": "standing", "count": 1_900_000}

    assert res["cat_ears"] == {
        "input": "cat_ears", "name": "cat_ears", "status": "ok", "count": 420_512,
        "category": "general", "suggestion": None,
    }
    # Under 100 posts, but no neighbour reaches 100x: niche but real, so fine.
    assert res["niche tag"]["status"] == "ok"
    assert res["niche tag"]["suggestion"] is None


def test_normalisation_matches_danbooru_names(danbooru):
    assert bs.normalize_tag("  Artoria Pendragon \\(Fate\\) ") == "artoria_pendragon_(fate)"
    assert bs.normalize_tag("cat_ears") == "cat_ears"
    assert bs.normalize_tag("blue   sky") == "blue_sky"

    body = run(bs.check_tags(["artoria pendragon \\(fate\\)", " ", ""]))
    assert [r["status"] for r in body["results"]] == ["ok"]  # blank parts are dropped


def test_two_batch_requests_cover_every_ok_tag(danbooru):
    """The lookups are batched: fine tags cost two requests in all, not one each."""
    run(bs.check_tags(["cat ears", "school uniform", "blonde hair", "standing"]))
    assert [path for path, _ in danbooru.calls] == ["/tags.json", "/tag_aliases.json"]
    assert danbooru.calls[0][1]["search[name_comma]"] == "cat_ears,school_uniform,blonde_hair,standing"


def test_a_repeat_check_is_served_from_the_cache(danbooru):
    caption = ["yellow hair", "schol uniform", "standing up", "cat ears"]
    first = run(bs.check_tags(caption))
    sent = len(danbooru.calls)
    assert sent > 0
    assert run(bs.check_tags(caption)) == first
    assert len(danbooru.calls) == sent  # nothing new left the process


def test_unreachable_danbooru_is_a_502_not_every_tag_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "_cache", {})
    monkeypatch.setattr(bs, "_danbooru_get", FakeDanbooru(fail_paths=("/tags.json",)))

    async def scenario():
        async with api_env(tmp_path) as env:
            return await env.client.post(f"{API}/booru/check", json={"tags": ["cat ears"]})

    r = run(scenario())
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == "Could not reach Danbooru (HTTP 403)"


def test_a_failed_suggestion_step_degrades_instead_of_failing(monkeypatch):
    monkeypatch.setattr(bs, "_cache", {})
    monkeypatch.setattr(bs, "_danbooru_get", FakeDanbooru(fail_paths=("/autocomplete.json",)))

    body = run(bs.check_tags(["schol uniform", "standing up", "niche tag", "cat ears"]))
    res = _by_input(body)
    assert body["suggestions_complete"] is False
    assert res["schol uniform"]["status"] == "missing"  # a real miss, still reported
    assert res["schol uniform"]["suggestion"] is None
    # The variant batch still worked, so standing_up has its neighbour.
    assert res["standing up"]["suggestion"] == {"name": "standing", "count": 1_900_000}
    # niche_tag could not be decided, so it stays flagged rather than passing as fine.
    assert res["niche tag"]["status"] == "rare"
    assert res["cat ears"]["status"] == "ok"


def test_only_the_first_flagged_tags_get_suggestions(danbooru):
    caption = [f"missing tag {i}" for i in range(bs.MAX_SUGGESTED + 3)]
    body = run(bs.check_tags(caption))
    assert body["suggestions_complete"] is False
    assert sum(1 for path, _ in danbooru.calls if path == "/autocomplete.json") == bs.MAX_SUGGESTED
    assert all(r["status"] == "missing" for r in body["results"])


def test_check_rejects_bad_bodies(tmp_path, danbooru):
    async def scenario():
        async with api_env(tmp_path) as env:
            for body in ({"tags": []}, {"tags": ["x"] * 201}, {"tags": ["x" * 201]}, {}):
                r = await env.client.post(f"{API}/booru/check", json=body)
                assert r.status_code == 422, (body, r.text)

    run(scenario())
    assert danbooru.calls == []
