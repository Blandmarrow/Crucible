"""The prompt library routes, pinned as they behave before the §8c rework.

Global (no dataset or plan): saved prompt texts grouped by a free-text category.
"""
from backend.tests.conftest import API, api_env, run

LIB = f"{API}/comfy/library"


async def _add(env, category, prompts):
    r = await env.client.post(LIB, json={"category": category, "prompts": prompts})
    assert r.status_code == 200, r.text
    return r.json()


async def _all(env) -> list[dict]:
    r = await env.client.get(LIB)
    assert r.status_code == 200, r.text
    return r.json()["prompts"]


def test_add_strips_drops_blanks_and_dedupes(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            out = await _add(env, "  knights  ", ["  A  red   Knight ", "", "   ", "a red knight", "a RED\tknight"])
            assert out == {"created": 1, "skipped": 2}
            rows = await _all(env)
            assert [(p["category"], p["text"]) for p in rows] == [("knights", "A  red   Knight")]

            # Against the stored category: case and whitespace do not matter.
            assert await _add(env, "knights", [" A RED KNIGHT ", "a blue knight"]) == {"created": 1, "skipped": 1}
            # The same text in another category is not a duplicate.
            assert await _add(env, "monks", ["a red knight"]) == {"created": 1, "skipped": 0}
            assert len(await _all(env)) == 3
    run(go())


def test_add_validates_category(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            r = await env.client.post(LIB, json={"category": "   ", "prompts": ["x"]})
            assert r.status_code == 400, r.text
            r = await env.client.post(LIB, json={"category": "c" * 101, "prompts": ["x"]})
            assert r.status_code == 422, r.text
            assert await _all(env) == []
    run(go())


def test_list_orders_by_category_then_created_at(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            await _add(env, "b", ["b1"])
            await _add(env, "a", ["a1"])
            await _add(env, "b", ["b2"])
            await _add(env, "a", ["a2"])
            rows = await _all(env)
            assert [(p["category"], p["text"]) for p in rows] == [
                ("a", "a1"), ("a", "a2"), ("b", "b1"), ("b", "b2"),
            ]
    run(go())


def test_move_merges_duplicates_and_validates(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            await _add(env, "src", ["One", "two", "three"])
            await _add(env, "dst", ["  ONE "])
            by_text = {p["text"]: p["id"] for p in await _all(env) if p["category"] == "src"}

            r = await env.client.post(f"{LIB}/move", json={"ids": list(by_text.values()), "category": " dst "})
            assert r.status_code == 200, r.text
            assert r.json() == {"moved": 2, "merged": 1}
            rows = await _all(env)
            assert sorted((p["category"], p["text"]) for p in rows) == [
                ("dst", "ONE"), ("dst", "three"), ("dst", "two"),
            ]

            r = await env.client.post(f"{LIB}/move", json={"ids": [], "category": "  "})
            assert r.status_code == 400, r.text
    run(go())


def test_delete_removes_listed_ids(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            await _add(env, "c", ["x", "y", "z"])
            rows = await _all(env)
            r = await env.client.post(f"{LIB}/delete", json={"ids": [rows[0]["id"], rows[1]["id"], "nope"]})
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": 2}
            assert [p["id"] for p in await _all(env)] == [rows[2]["id"]]
    run(go())
