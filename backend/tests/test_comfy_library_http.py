"""The prompt library routes. First pinned as they behaved before M6 §8c; §8c made the
category a normalized `/` path of up to 512 characters, and added text edits and
whole-category moves and deletes.

Global (no dataset or plan): saved prompt texts grouped by category.
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
            r = await env.client.post(LIB, json={"category": "c" * 513, "prompts": ["x"]})
            assert r.status_code == 422, r.text
            # §8c: a slash-only category normalizes to nothing.
            r = await env.client.post(LIB, json={"category": " / ", "prompts": ["x"]})
            assert r.status_code == 400, r.text
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


# ── M6 §8c: nested categories, text edits, whole-category moves ───────────────

def test_categories_are_normalized_paths(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            await _add(env, " portraits / studio ", ["a"])
            # The same category spelled differently is the same category: a duplicate.
            assert await _add(env, r"portraits\studio", ["A"]) == {"created": 0, "skipped": 1}
            rows = await _all(env)
            r = await env.client.post(f"{LIB}/move", json={"ids": [rows[0]["id"]], "category": "/x// y /"})
            assert r.status_code == 200, r.text
            assert [p["category"] for p in await _all(env)] == ["x/y"]
            # Over the old 100 characters, under 512.
            long_cat = "/".join(["segment"] * 40)
            assert len(long_cat) > 100
            assert (await _add(env, long_cat, ["deep"]))["created"] == 1
    run(go())


def test_patch_edits_text_and_refuses_a_duplicate(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            await _add(env, "c", ["red knight", "blue knight"])
            await _add(env, "other", ["green knight"])
            by_text = {p["text"]: p for p in await _all(env)}
            red = by_text["red knight"]

            r = await env.client.patch(f"{LIB}/{red['id']}", json={"text": "  crimson knight "})
            assert r.status_code == 200, r.text
            assert r.json()["text"] == "crimson knight" and r.json()["category"] == "c"

            # A duplicate in the same category is refused, not merged.
            r = await env.client.patch(f"{LIB}/{red['id']}", json={"text": "BLUE  knight"})
            assert r.status_code == 409, r.text
            # The same text in another category is fine; so is a case change of itself.
            r = await env.client.patch(f"{LIB}/{red['id']}", json={"text": "green knight"})
            assert r.status_code == 200, r.text
            r = await env.client.patch(f"{LIB}/{red['id']}", json={"text": "Green Knight"})
            assert r.status_code == 200, r.text

            r = await env.client.patch(f"{LIB}/{red['id']}", json={"text": "   "})
            assert r.status_code == 400, r.text
            r = await env.client.patch(f"{LIB}/nope", json={"text": "x"})
            assert r.status_code == 404, r.text
            texts = sorted(p["text"] for p in await _all(env))
            assert texts == ["Green Knight", "blue knight", "green knight"]
    run(go())


def test_category_move_renests_the_subtree_and_merges(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            await _add(env, "a", ["castle"])
            await _add(env, "a/x", ["red knight", "blue knight"])
            await _add(env, "A/x", ["case twin"])        # not below `a`: case matters
            await _add(env, "ab", ["sibling"])           # not below `a` either
            await _add(env, "b/x", ["RED knight"])       # the destination's occupant
            url = f"{LIB}/categories/move"

            r = await env.client.post(url, json={"path": "a", "new_path": " b "})
            assert r.status_code == 200, r.text
            assert r.json() == {"moved": 2, "merged": 1}
            got = sorted((p["category"], p["text"]) for p in await _all(env))
            assert got == [
                ("A/x", "case twin"), ("ab", "sibling"),
                ("b", "castle"), ("b/x", "RED knight"), ("b/x", "blue knight"),
            ]

            # Into its own subtree: refused. Onto itself: a no-op. Unknown: 404.
            r = await env.client.post(url, json={"path": "b", "new_path": "b/inner"})
            assert r.status_code == 400, r.text
            r = await env.client.post(url, json={"path": "b", "new_path": "b"})
            assert r.json() == {"moved": 0, "merged": 0}
            r = await env.client.post(url, json={"path": "nope", "new_path": "x"})
            assert r.status_code == 404, r.text
            r = await env.client.post(url, json={"path": "b", "new_path": " / "})
            assert r.status_code == 400, r.text

            # Un-nesting sends the last segment alone.
            r = await env.client.post(url, json={"path": "b/x", "new_path": "x"})
            assert r.json() == {"moved": 2, "merged": 0}
    run(go())


def test_category_delete_takes_the_subtree_only(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            await _add(env, "a", ["1"])
            await _add(env, "a/b", ["2", "3"])
            await _add(env, "A/b", ["4"])
            await _add(env, "ab", ["5"])
            r = await env.client.post(f"{LIB}/categories/delete", json={"path": "a"})
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": 3}
            assert sorted(p["category"] for p in await _all(env)) == ["A/b", "ab"]
    run(go())
