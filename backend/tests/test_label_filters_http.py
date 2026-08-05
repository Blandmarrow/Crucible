"""The `label_filter` / `label_match` / `label_missing` trio on the image endpoints.

The load-bearing test here is `test_a_three_label_image_appears_once`: the filter
is built from correlated `EXISTS` clauses rather than a join precisely because
`GET /images/count` runs the same shared builder over
`select(func.count(Image.id))`. A join to `image_labels` would count a
three-label image three times and return its row three times in the grid — a
regression that looks like nothing at all until someone puts two labels on one
image.
"""
import json

from backend.models import Image, ImageLabel, Label
from backend.tests.conftest import API, api_env, run
from backend.utils import MAX_LABEL_FILTER_IDS


async def _seed(env, dataset_id: str):
    """Nine images: three carry `fx`, three carry `reject`, one carries both,
    and four carry nothing."""
    async with env.Session() as db:
        rows = [
            Image(
                dataset_id=dataset_id,
                filename=f"i{i}.png",
                original_filename=f"i{i}.png",
                file_path=f"/tmp/{dataset_id}/i{i}.png",
            )
            for i in range(9)
        ]
        fx = Label(name="fx", color="#ef4444")
        reject = Label(name="reject", color="#6b7280")
        db.add_all([*rows, fx, reject])
        await db.flush()
        db.add_all([
            ImageLabel(image_id=rows[0].id, label_id=fx.id),
            ImageLabel(image_id=rows[1].id, label_id=fx.id),
            ImageLabel(image_id=rows[2].id, label_id=fx.id),      # also reject
            ImageLabel(image_id=rows[2].id, label_id=reject.id),
            ImageLabel(image_id=rows[3].id, label_id=reject.id),
            ImageLabel(image_id=rows[4].id, label_id=reject.id),
        ])
        await db.commit()
        return {"fx": fx.id, "reject": reject.id, "images": [r.id for r in rows]}


async def _ids(env, dataset_id: str, **params) -> list[str]:
    r = await env.client.get(
        f"{API}/images/", params={"dataset_id": dataset_id, "limit": 100, **params}
    )
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()]


def test_any_all_and_missing(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            seed = await _seed(env, ds["id"])
            both = json.dumps([seed["fx"], seed["reject"]])

            # Any (the default) is the union: 3 fx + 3 reject, one overlapping.
            assert len(await _ids(env, ds["id"], label_filter=both)) == 5
            assert len(await _ids(env, ds["id"], label_filter=both, label_match="any")) == 5
            # All is the intersection: only the one carrying both.
            assert await _ids(env, ds["id"], label_filter=both, label_match="all") == [seed["images"][2]]
            # One label alone reads the same either way.
            single = json.dumps([seed["fx"]])
            assert len(await _ids(env, ds["id"], label_filter=single)) == 3
            assert len(await _ids(env, ds["id"], label_filter=single, label_match="all")) == 3
            # Unlabelled, and its complement.
            assert len(await _ids(env, ds["id"], label_missing="true")) == 4
            assert len(await _ids(env, ds["id"], label_missing="false")) == 5

    run(scenario())


def test_a_three_label_image_appears_once(tmp_path):
    """The row-multiplication regression a `join` would introduce."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            async with env.Session() as db:
                img = Image(
                    dataset_id=ds["id"],
                    filename="one.png",
                    original_filename="one.png",
                    file_path=f"/tmp/{ds['id']}/one.png",
                )
                labels = [Label(name=n) for n in ("a", "b", "c")]
                db.add_all([img, *labels])
                await db.flush()
                db.add_all([ImageLabel(image_id=img.id, label_id=x.id) for x in labels])
                await db.commit()
                all_three = json.dumps([x.id for x in labels])

            for match in ("any", "all"):
                rows = await _ids(env, ds["id"], label_filter=all_three, label_match=match)
                assert rows == [img.id], f"{match}: {rows}"
                count = (await env.client.get(
                    f"{API}/images/count",
                    params={"dataset_id": ds["id"], "label_filter": all_three, "label_match": match},
                )).json()
                assert count == {"count": 1}, f"{match}: {count}"

    run(scenario())


def test_the_three_endpoints_agree(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            seed = await _seed(env, ds["id"])
            shapes = [
                {"label_filter": json.dumps([seed["fx"]])},
                {"label_filter": json.dumps([seed["fx"], seed["reject"]]), "label_match": "all"},
                {"label_missing": "true"},
            ]
            for extra in shapes:
                params = {"dataset_id": ds["id"], **extra}
                grid = await _ids(env, ds["id"], **extra)
                count = (await env.client.get(f"{API}/images/count", params=params)).json()["count"]
                ids = (await env.client.get(f"{API}/images/ids", params=params)).json()
                assert count == len(grid), f"{extra}: {count} vs {len(grid)}"
                assert sorted(ids["ids"]) == sorted(grid), extra
                assert ids["count"] == len(grid)

    run(scenario())


def test_the_four_400_shapes(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            seed = await _seed(env, ds["id"])
            bad = [
                # A blank entry: dropping it silently narrows a mixed list and
                # voids an all-blank one. `label_missing` is how "no labels" is
                # expressed.
                {"label_filter": json.dumps([seed["fx"], ""])},
                {"label_match": "either"},
                {"label_filter": "[not json"},
                # Unsatisfiable: a query that always returns zero rows is
                # indistinguishable from a broken filter.
                {"label_missing": "true", "label_filter": json.dumps([seed["fx"]])},
            ]
            for path in ("/images/", "/images/count", "/images/ids"):
                for extra in bad:
                    r = await env.client.get(f"{API}{path}", params={"dataset_id": ds["id"], **extra})
                    assert r.status_code == 400, f"{path} {extra}: {r.status_code} {r.text}"

    run(scenario())


def test_too_many_label_ids_is_400_not_a_500(tmp_path):
    """`label_match=all` builds one correlated EXISTS **per id**, so the id count
    is an expression-tree depth. SQLite's SQLITE_MAX_EXPR_DEPTH defaults to 1,000,
    and past it the parser raises where nothing catches it — a 500 for a filter
    the client can be told about. The cap is a guard, not a budget: nobody selects
    a hundred chips."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            seed = await _seed(env, ds["id"])

            at_cap = json.dumps([seed["fx"]] + [f"id-{i}" for i in range(MAX_LABEL_FILTER_IDS - 1)])
            over = json.dumps([f"id-{i}" for i in range(MAX_LABEL_FILTER_IDS + 1)])
            for path in ("/images/", "/images/count", "/images/ids"):
                ok = await env.client.get(
                    f"{API}{path}",
                    params={"dataset_id": ds["id"], "label_filter": at_cap, "label_match": "all"},
                )
                assert ok.status_code == 200, f"{path}: {ok.status_code} {ok.text}"
                r = await env.client.get(
                    f"{API}{path}",
                    params={"dataset_id": ds["id"], "label_filter": over, "label_match": "all"},
                )
                assert r.status_code == 400, f"{path}: {r.status_code} {r.text}"

    run(scenario())


def test_label_ids_ride_on_both_image_payloads(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            seed = await _seed(env, ds["id"])
            both_image = seed["images"][2]

            listing = (await env.client.get(
                f"{API}/images/", params={"dataset_id": ds["id"], "limit": 100}
            )).json()
            by_id = {i["id"]: i for i in listing}
            assert sorted(by_id[both_image]["label_ids"]) == sorted([seed["fx"], seed["reject"]])
            assert by_id[seed["images"][8]]["label_ids"] == []

            detail = (await env.client.get(f"{API}/images/{both_image}")).json()
            assert sorted(detail["label_ids"]) == sorted([seed["fx"], seed["reject"]])

    run(scenario())


def test_the_filter_is_scoped_to_its_dataset(tmp_path):
    """`image_labels` carries no `dataset_id` — the scoping comes from `images`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            seed = await _seed(env, a["id"])
            async with env.Session() as db:
                img = Image(
                    dataset_id=b["id"],
                    filename="b.png",
                    original_filename="b.png",
                    file_path=f"/tmp/{b['id']}/b.png",
                )
                db.add(img)
                await db.flush()
                db.add(ImageLabel(image_id=img.id, label_id=seed["fx"]))
                await db.commit()

            fx = json.dumps([seed["fx"]])
            assert len(await _ids(env, a["id"], label_filter=fx)) == 3
            assert len(await _ids(env, b["id"], label_filter=fx)) == 1

    run(scenario())
