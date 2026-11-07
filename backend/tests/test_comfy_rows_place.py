"""`rows/place` and `rows/bulk`'s folder (M6 §8c): moving rows to a spot in the run order,
optionally re-filing them, in one step; and the pure arithmetic behind it."""
import pytest

from backend.services.comfy_rows import place_rows
from backend.tests import test_blueprint_http as bp
from backend.tests.conftest import API, api_env, run
from backend.tests.test_comfy_subfolder import _add_row, _link_row, _plan, _rows


# ── place_rows ────────────────────────────────────────────────────────────────

def test_place_rows_keeps_the_block_in_run_order():
    order = ["a", "b", "c", "d", "e"]
    # The block keeps `order`'s order, whatever order `moving` lists it in.
    assert place_rows(order, ["d", "b"], anchor="a", position="before") == ["b", "d", "a", "c", "e"]
    assert place_rows(order, ["a"], anchor="c", position="after") == ["b", "c", "a", "d", "e"]
    assert place_rows(order, ["a"], end_after="d") == ["b", "c", "d", "a", "e"]
    assert place_rows(order, ["b"]) == ["a", "c", "d", "e", "b"]
    # An end_after that is itself moving falls back to the end.
    assert place_rows(order, ["b"], end_after="b") == ["a", "c", "d", "e", "b"]
    with pytest.raises(ValueError):
        place_rows(order, ["a", "b"], anchor="b")


# ── rows/place ────────────────────────────────────────────────────────────────

def _url(plan_id):
    return f"{API}/comfy/plans/{plan_id}/rows/place"


def test_place_reorders_and_refiles(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            a1 = (await _add_row(env, plan_id, subfolder="a"))["id"]
            a2 = (await _add_row(env, plan_id, subfolder="a"))["id"]
            b1 = (await _add_row(env, plan_id, subfolder="b"))["id"]
            b2 = (await _add_row(env, plan_id, subfolder="b"))["id"]

            async def order():
                return [(x["id"], x["subfolder"]) for x in await _rows(env, plan_id)]

            # Move up within a folder: before the previous row.
            r = await env.client.post(_url(plan_id), json={"row_ids": [a2], "anchor_row_id": a1})
            assert r.status_code == 200 and r.json() == {"placed": 1, "unlinked": 0, "skipped": 0}
            assert await order() == [(a2, "a"), (a1, "a"), (b1, "b"), (b2, "b")]
            assert [x["sort_order"] for x in await _rows(env, plan_id)] == [0, 1, 2, 3]

            # Dropped between rows of another group: re-filed and placed there.
            r = await env.client.post(_url(plan_id), json={
                "row_ids": [a2], "anchor_row_id": b1, "position": "after", "subfolder": "b"})
            assert r.status_code == 200, r.text
            assert await order() == [(a1, "a"), (b1, "b"), (a2, "b"), (b2, "b")]

            # No anchor with a folder: after that folder's last row.
            r = await env.client.post(_url(plan_id), json={"row_ids": [b2], "subfolder": "a"})
            assert await order() == [(a1, "a"), (b2, "a"), (b1, "b"), (a2, "b")]

            # A folder nobody is in yet: the end of the plan.
            r = await env.client.post(_url(plan_id), json={"row_ids": [a1], "subfolder": "/c/"})
            assert await order() == [(b2, "a"), (b1, "b"), (a2, "b"), (a1, "c")]
    run(scenario())


def test_place_validates_its_ids(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            _, other = await _plan(env, ds_name="other-ds", plan_name="other")
            x = (await _add_row(env, plan_id))["id"]
            y = (await _add_row(env, plan_id))["id"]
            foreign = (await _add_row(env, other))["id"]
            for body in (
                {"row_ids": [foreign]},
                {"row_ids": [x], "anchor_row_id": foreign},
                {"row_ids": [x, y], "anchor_row_id": y},
                {"row_ids": [x], "subfolder": "a/../b"},
            ):
                r = await env.client.post(_url(plan_id), json=body)
                assert r.status_code == 400, (body, r.text)
            r = await env.client.post(_url(plan_id), json={"row_ids": []})
            assert r.status_code == 422, r.text
    run(scenario())


def test_place_applies_the_linked_modes(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            linked = (await _add_row(env, plan_id, subfolder="L"))["id"]
            free = (await _add_row(env, plan_id, subfolder="L"))["id"]
            anchor = (await _add_row(env, plan_id, subfolder="M"))["id"]
            await _link_row(env, plan_id, linked)
            body = {"row_ids": [linked, free], "anchor_row_id": anchor, "position": "after",
                    "subfolder": "M"}

            r = await env.client.post(_url(plan_id), json=body)
            assert r.status_code == 409 and "1 of these rows" in r.json()["detail"]

            r = await env.client.post(_url(plan_id), json={**body, "linked": "skip"})
            assert r.json() == {"placed": 1, "unlinked": 0, "skipped": 1}
            rows = {x["id"]: x for x in await _rows(env, plan_id)}
            # The skipped row neither moved folder nor changed place.
            assert rows[linked]["subfolder"] == "L" and rows[linked]["sort_order"] == 0
            assert rows[free]["subfolder"] == "M" and rows[free]["sort_order"] == 2

            r = await env.client.post(_url(plan_id), json={**body, "linked": "unlink"})
            assert r.json() == {"placed": 2, "unlinked": 1, "skipped": 0}
            rows = {x["id"]: x for x in await _rows(env, plan_id)}
            assert rows[linked]["subfolder"] == "M" and rows[linked]["leaf_id"] is None
    run(scenario())


# ── rows/bulk's folder ────────────────────────────────────────────────────────

def test_bulk_files_new_rows_in_a_folder(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await bp._plan(env)
            url = f"{API}/comfy/plans/{plan_id}/rows/bulk"
            r = await env.client.post(url, json={"lines": ["one", " ", "two"], "subfolder": "/a\\b/"})
            assert r.json() == {"created": 2}
            r = await env.client.post(url, json={"lines": ["three"]})
            got = [(x["values"]["prompt"], x["subfolder"]) for x in await bp._rows(env, plan_id)]
            assert got == [("one", "a/b"), ("two", "a/b"), ("three", "")]
            r = await env.client.post(url, json={"lines": ["x"], "subfolder": "../up"})
            assert r.status_code == 400, r.text
    run(scenario())
