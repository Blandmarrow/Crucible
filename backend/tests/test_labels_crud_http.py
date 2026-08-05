"""`/labels` CRUD over HTTP — the managed vocabulary's own rules.

The three that are not obvious:

- **Case-insensitive name uniqueness.** SQLite's default collation is
  case-sensitive, so the column's `unique=True` would happily hold "Reject" and
  "reject" side by side; the router's `func.lower(...)` check is the real rule.
- **A rename detaches nothing.** That is the behavioural half of storing ids
  rather than names everywhere — a rename means "same concept, new spelling".
- **`{"hotkey": null}` clears the key.** The PATCH uses `model_dump(exclude_unset=True)`
  rather than the `exclude_none=True` that `routers/providers.py` uses, precisely
  so an explicit null is a clear rather than a silently dropped field.
"""
from backend.tests.conftest import API, api_env, run


async def _create(env, **body):
    return await env.client.post(f"{API}/labels/", json=body)


def test_create_assigns_increasing_sort_order(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await _create(env, name="fx")
            b = await _create(env, name="reject", color="#ef4444", hotkey="R")
            assert a.status_code == 201, a.text
            assert b.status_code == 201, b.text
            assert b.json()["sort_order"] > a.json()["sort_order"]
            # The hotkey is normalized to lowercase on the way in.
            assert b.json()["hotkey"] == "r"
            assert a.json()["color"] == "#6b7280"
            assert a.json()["usage_count"] == 0

            listed = (await env.client.get(f"{API}/labels/")).json()
            assert [x["name"] for x in listed] == ["fx", "reject"]

    run(scenario())


def test_duplicate_name_is_409_case_insensitively(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await _create(env, name="Reject")).status_code == 201
            assert (await _create(env, name="Reject")).status_code == 409
            # The one the DB's own unique constraint would let through.
            r = await _create(env, name="reject")
            assert r.status_code == 409, r.text
            # And whitespace does not buy a second one either.
            assert (await _create(env, name="  reject  ")).status_code == 409

    run(scenario())


def test_duplicate_hotkey_is_409(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await _create(env, name="fx", hotkey="f")).status_code == 201
            r = await _create(env, name="flag", hotkey="f")
            assert r.status_code == 409, r.text
            # The message names the owner, so the UI can say which label has it.
            assert "fx" in r.json()["detail"]

    run(scenario())


def test_bad_hotkey_and_blank_name_are_400(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            for bad in ("ff", "!", " ", "Escape"):
                r = await _create(env, name=f"n{bad!r}", hotkey=bad)
                # " " normalizes to None (cleared), not a rejection.
                expected = 201 if bad.strip() == "" else 400
                assert r.status_code == expected, f"{bad!r}: {r.status_code} {r.text}"
            assert (await _create(env, name="")).status_code == 400
            assert (await _create(env, name="   ")).status_code == 400
            assert (await _create(env, name="x" * 65)).status_code == 400

    run(scenario())


def test_a_non_hex_color_is_400(tmp_path):
    """`Label.color` is interpolated straight into inline CSS on every chip and
    card, and `String(16)` is not enforced by SQLite — so `url(http://…)` would be
    one remote fetch per rendered card, from a value the API accepted."""
    async def scenario():
        async with api_env(tmp_path) as env:
            for bad in ("url(http://evil.test/x.png)", "red", "#12", "#gggggg", "#1234567890"):
                r = await _create(env, name=f"n-{bad}", color=bad)
                assert r.status_code == 400, f"{bad!r}: {r.status_code} {r.text}"

            for good in ("#fff", "#6b7280", "#6b7280ff"):
                assert (await _create(env, name=f"ok-{good}", color=good)).status_code == 201

            # Omitted or blank falls back to the default rather than 400 — the
            # Settings form leaves it empty for "whatever you like".
            r = await _create(env, name="bare")
            assert r.status_code == 201, r.text
            assert r.json()["color"] == "#6b7280"

            # …and the PATCH is guarded too, not only the create.
            label_id = r.json()["id"]
            assert (await env.client.patch(
                f"{API}/labels/{label_id}", json={"color": "url(http://evil.test/x.png)"}
            )).status_code == 400
            assert (await env.client.patch(
                f"{API}/labels/{label_id}", json={"color": "#123456"}
            )).status_code == 200

    run(scenario())


def test_rename_detaches_nothing(tmp_path):
    """The behavioural half of ids-not-names: a rename is a spelling change."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            label = (await _create(env, name="fx")).json()
            img = await _seed_image(env, ds["id"])

            assert (await env.client.post(
                f"{API}/labels/assign", json={"image_ids": [img], "add": [label["id"]]}
            )).status_code == 200

            r = await env.client.patch(f"{API}/labels/{label['id']}", json={"name": "effects"})
            assert r.status_code == 200, r.text
            assert r.json()["name"] == "effects"
            assert r.json()["id"] == label["id"]
            assert r.json()["usage_count"] == 1

            detail = (await env.client.get(f"{API}/images/{img}")).json()
            assert detail["label_ids"] == [label["id"]]

    run(scenario())


def test_explicit_null_clears_the_hotkey_and_frees_it(tmp_path):
    """The `exclude_unset` regression: `exclude_none` would drop this field."""
    async def scenario():
        async with api_env(tmp_path) as env:
            a = (await _create(env, name="fx", hotkey="f")).json()
            assert (await _create(env, name="flag", hotkey="f")).status_code == 409

            r = await env.client.patch(f"{API}/labels/{a['id']}", json={"hotkey": None})
            assert r.status_code == 200, r.text
            assert r.json()["hotkey"] is None

            # The key is now free.
            assert (await _create(env, name="flag", hotkey="f")).status_code == 201

            # An omitted field still means "leave it alone".
            b = (await _create(env, name="keep", hotkey="k")).json()
            r = await env.client.patch(f"{API}/labels/{b['id']}", json={"color": "#123456"})
            assert r.json()["hotkey"] == "k"
            assert r.json()["color"] == "#123456"

    run(scenario())


def test_partial_reorder_is_400(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            a = (await _create(env, name="a")).json()
            b = (await _create(env, name="b")).json()
            c = (await _create(env, name="c")).json()

            # Missing one id would leave two labels sharing a sort_order.
            r = await env.client.post(f"{API}/labels/reorder", json={"ordered_ids": [a["id"], b["id"]]})
            assert r.status_code == 400, r.text
            # A duplicate is the same failure from the other direction.
            r = await env.client.post(
                f"{API}/labels/reorder", json={"ordered_ids": [a["id"], a["id"], b["id"]]}
            )
            assert r.status_code == 400, r.text

            r = await env.client.post(
                f"{API}/labels/reorder", json={"ordered_ids": [c["id"], b["id"], a["id"]]}
            )
            assert r.status_code == 200, r.text
            assert [x["name"] for x in (await env.client.get(f"{API}/labels/")).json()] == ["c", "b", "a"]

    run(scenario())


def test_delete_is_204_and_removes_it_from_the_vocabulary(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            label = (await _create(env, name="fx")).json()
            assert (await env.client.delete(f"{API}/labels/{label['id']}")).status_code == 204
            assert (await env.client.get(f"{API}/labels/")).json() == []
            assert (await env.client.delete(f"{API}/labels/{label['id']}")).status_code == 404

    run(scenario())


def test_counts_are_scoped_to_one_dataset(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            label = (await _create(env, name="fx")).json()
            img_a = await _seed_image(env, a["id"], "a.png")
            img_b = await _seed_image(env, b["id"], "b.png")
            await env.client.post(
                f"{API}/labels/assign", json={"image_ids": [img_a, img_b], "add": [label["id"]]}
            )

            counts = (await env.client.get(f"{API}/labels/counts", params={"dataset_id": a["id"]})).json()
            assert counts == {"counts": {label["id"]: 1}}
            # …while `usage_count` on the vocabulary is app-wide.
            assert (await env.client.get(f"{API}/labels/")).json()[0]["usage_count"] == 2

    run(scenario())


async def _seed_image(env, dataset_id: str, name: str = "img.png") -> str:
    """One image row, seeded directly — these tests are about query shapes."""
    from backend.models import Image

    async with env.Session() as db:
        img = Image(
            dataset_id=dataset_id,
            filename=name,
            original_filename=name,
            file_path=f"/tmp/{dataset_id}/{name}",
        )
        db.add(img)
        await db.commit()
        return img.id
