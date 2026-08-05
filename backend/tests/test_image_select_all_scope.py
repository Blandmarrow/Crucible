"""`GET /images/count` and `GET /images/ids` — the whole filtered result set.

These two exist so the gallery can offer "select all N matching filters" instead
of one page at a time, and the only property that makes the offer trustworthy is
that all three endpoints describe *the same* set of images. So every test here is
a comparison against `GET /images/` itself, paged to exhaustion: the count must
equal what the grid would eventually show, and the ids must be that same list in
that same order.

The last test is the structural one. `ImageFilterParams` is shared precisely so a
new filter cannot reach the grid without reaching select-all, and reading it back
out of `app.openapi()` is what turns "we refactored to share it" into something
CI re-checks — a filter re-declared on `list_images` alone fails here rather than
in a user's selection.
"""
import json

from backend.models import Image, ImageLabel, Label
from backend.routers import images as images_router
from backend.tests.conftest import API, api_env, run


def _mk(dataset_id: str, i: int, **kw) -> Image:
    """One image row. Rows are seeded directly rather than uploaded: these tests
    are about query shapes, and 24 real PNG encodes buy nothing."""
    return Image(
        dataset_id=dataset_id,
        filename=f"img_{i:03d}.png",
        original_filename=f"img_{i:03d}.png",
        file_path=f"/tmp/{dataset_id}/img_{i:03d}.png",
        **kw,
    )


async def _seed(env, dataset_id: str) -> list[str]:
    """24 images spanning every filter the tests below exercise.

    Returns the two seeded label ids, because the label filter shapes name ids
    rather than the literal strings every other filter uses — which is why
    `filter_shapes()` is a function of them rather than a module constant.
    """
    rows = []
    for i in range(24):
        rows.append(_mk(
            dataset_id, i,
            # Half captioned, and the caption text carries a searchable token on
            # a third of them.
            caption_text=("a photo of a cat" if i % 3 == 0 else "a caption") if i % 2 == 0 else "",
            subfolder="sub/deep" if i % 4 == 0 else ("sub" if i % 4 == 1 else ""),
            aesthetic_score=float(i % 10),
            # A third carry an explicit license; the rest inherit the dataset's
            # (which is unset here, so they read as "missing").
            license="CC-BY-4.0" if i % 3 == 1 else None,
            source_timestamp_ms=i * 100 if i % 5 else None,
        ))
    # Names are dataset-scoped strings only so a scenario can seed two datasets:
    # the vocabulary itself is global, and `labels.name` is unique app-wide.
    labels = [
        Label(name=f"fx-{dataset_id[:8]}", color="#ef4444"),
        Label(name=f"reject-{dataset_id[:8]}", color="#6b7280"),
    ]
    async with env.Session() as db:
        db.add_all(rows)
        db.add_all(labels)
        await db.flush()
        # Every 3rd image is "fx"; every 6th carries both — enough for `all` to
        # differ from `any`, and enough images to stay unlabelled for
        # `label_missing`.
        db.add_all(
            [ImageLabel(image_id=r.id, label_id=labels[0].id) for r in rows[::3]]
            + [ImageLabel(image_id=r.id, label_id=labels[1].id) for r in rows[::6]]
        )
        await db.commit()
        return [labels[0].id, labels[1].id]


async def _page_all(env, params: dict) -> list[str]:
    """Every id `GET /images/` would show, in order, by paging to exhaustion."""
    ids: list[str] = []
    page = 1
    while True:
        r = await env.client.get(f"{API}/images/", params={**params, "page": page, "limit": 5})
        assert r.status_code == 200, r.text
        batch = r.json()
        ids.extend(i["id"] for i in batch)
        if len(batch) < 5:
            return ids
        page += 1


# The filter shapes the count is checked against. Each is a dict merged over
# `dataset_id`; the names are only there to label a failure.
#
# A function rather than a constant because the label shapes name seeded ids,
# which do not exist until the scenario has run `_seed`.
def filter_shapes(label_ids: list[str]) -> dict[str, dict]:
    return {
        "unfiltered": {},
        "captioned": {"captioned": "true"},
        "uncaptioned": {"captioned": "false"},
        "subfolder": {"subfolder": "sub"},
        "subfolder_leaf": {"subfolder": "sub/deep"},
        "search": {"search": "cat"},
        "license_missing": {"license_missing": "true"},
        "license_filter": {"license_filter": json.dumps(["CC-BY-4.0"])},
        "score_filters": {"score_filters": json.dumps([{"field": "aesthetic_score", "min": 5}])},
        "label_any": {"label_filter": json.dumps(label_ids)},
        "label_all": {"label_filter": json.dumps(label_ids), "label_match": "all"},
        "label_missing": {"label_missing": "true"},
        "combined": {"captioned": "true", "subfolder": "sub/deep", "search": "cat"},
        "matches_nothing": {"search": "no-such-image"},
    }


def test_count_matches_the_grid_paged_to_exhaustion(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            label_ids = await _seed(env, ds["id"])
            shapes = filter_shapes(label_ids)

            for name, extra in shapes.items():
                params = {"dataset_id": ds["id"], **extra}
                expected = await _page_all(env, params)
                r = await env.client.get(f"{API}/images/count", params=params)
                assert r.status_code == 200, r.text
                assert r.json() == {"count": len(expected)}, f"{name}: {r.json()}"

            # The shapes have to actually discriminate, or every assertion above
            # is trivially true against the same 24 rows.
            counts = {}
            for name, extra in shapes.items():
                r = await env.client.get(f"{API}/images/count", params={"dataset_id": ds["id"], **extra})
                counts[name] = r.json()["count"]
            assert counts["unfiltered"] == 24
            assert counts["matches_nothing"] == 0
            assert len(set(counts.values())) >= 5, counts

    run(scenario())


def test_count_is_scoped_to_its_dataset(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            await _seed(env, a["id"])
            await _seed(env, b["id"])
            for ds in (a, b):
                r = await env.client.get(f"{API}/images/count", params={"dataset_id": ds["id"]})
                assert r.json() == {"count": 24}

    run(scenario())


def test_ids_match_the_grid_order_for_several_sorts(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _seed(env, ds["id"])

            sorts = [
                {},                                                   # the default
                {"sort": "filename", "order": "asc"},
                {"sort": "aesthetic_score", "order": "desc"},
                # The nulls-last branch: two thirds of the rows carry a
                # timestamp, the rest are not frames at all.
                {"sort": "source_timestamp_ms", "order": "asc"},
                {"sort": "source_timestamp_ms", "order": "desc"},
                # Coerced to created_at rather than rejected.
                {"sort": "not_a_column", "order": "desc"},
            ]
            for sort in sorts:
                params = {"dataset_id": ds["id"], **sort}
                expected = await _page_all(env, params)
                r = await env.client.get(f"{API}/images/ids", params=params)
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["ids"] == expected, sort
                assert body["count"] == len(expected)
                assert body["truncated"] is False

            # …and under a filter, not just a sort.
            params = {"dataset_id": ds["id"], "subfolder": "sub/deep", "sort": "filename", "order": "asc"}
            body = (await env.client.get(f"{API}/images/ids", params=params)).json()
            assert body["ids"] == await _page_all(env, params)
            assert 0 < body["count"] < 24

    run(scenario())


def test_truncation_reports_the_true_total(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _seed(env, ds["id"])
            params = {"dataset_id": ds["id"], "sort": "filename", "order": "asc"}

            monkeypatch.setattr(images_router, "SELECT_ALL_ID_CAP", 2)
            body = (await env.client.get(f"{API}/images/ids", params=params)).json()
            assert body["truncated"] is True
            assert len(body["ids"]) == 2
            # The trimmed ids are the *first* two of the order asked for, and the
            # count is the whole set — the UI says "the first 2 of 24".
            assert body["ids"] == (await _page_all(env, params))[:2]
            assert body["count"] == 24

            # Exactly at the cap is not truncation: the endpoint fetches cap+1 and
            # only trims when it comes back over.
            monkeypatch.setattr(images_router, "SELECT_ALL_ID_CAP", 24)
            body = (await env.client.get(f"{API}/images/ids", params=params)).json()
            assert body["truncated"] is False
            assert body["count"] == 24 and len(body["ids"]) == 24

    run(scenario())


def test_bad_input_is_rejected_identically_by_all_three(tmp_path):
    """The evidence that the validation really is shared rather than duplicated."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _seed(env, ds["id"])
            bad = [
                {"score_field": "not_a_score"},
                {"quality_flag": "not_a_flag"},
                # A blank entry in the license list is a 400 rather than a silent
                # narrowing — `license_missing` is how "no license" is expressed.
                {"license_filter": json.dumps(["CC-BY-4.0", ""])},
                # The four label shapes, for the same reason: rejected by the
                # shared filter builder, so all three endpoints answer 400.
                {"label_filter": json.dumps(["some-id", ""])},
                {"label_match": "either"},
                {"label_filter": "not-json"},
                {"label_missing": "true", "label_filter": json.dumps(["some-id"])},
            ]
            for path in ("/images/", "/images/count", "/images/ids"):
                for extra in bad:
                    r = await env.client.get(f"{API}{path}", params={"dataset_id": ds["id"], **extra})
                    assert r.status_code == 400, f"{path} {extra}: {r.status_code} {r.text}"

    run(scenario())


def test_the_three_endpoints_declare_one_filter_contract():
    """A filter added to `list_images` alone must fail here, not in a selection.

    `ImageFilterParams` is the shared declaration and the other two models
    subclass it, so the param sets are nested by construction — this reads them
    back out of the generated schema, which is what a re-inlined query param
    would show up in.
    """
    from backend.main import app

    schema = app.openapi()

    def query_params(path: str) -> set[str]:
        return {
            p["name"]
            for p in schema["paths"][path]["get"].get("parameters", [])
            if p["in"] == "query"
        }

    listing = query_params("/api/v1/images/")
    count = query_params("/api/v1/images/count")
    ids = query_params("/api/v1/images/ids")

    # Sanity: the models flattened into real query params. FastAPI unpacks a
    # Pydantic query model only when it is the route's *sole* query parameter —
    # otherwise the whole model degrades to one param named after the argument,
    # and every assertion below would pass against a broken endpoint.
    assert "dataset_id" in count and "f" not in count
    assert "dataset_id" in ids and "f" not in ids
    assert "dataset_id" in listing and "f" not in listing

    assert count <= listing, sorted(count - listing)
    assert ids <= listing, sorted(ids - listing)
    # `/count` is the filters alone; `/ids` adds ordering; `/` adds paging.
    assert ids - count == {"sort", "order"}
    assert listing - ids == {"page", "limit"}
