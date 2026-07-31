"""`GET /images/`'s `sort` param: the allowlist, the coercion, and the two
frame-lineage orderings.

The ordering block reaches `Image` by `getattr`, and `Image` has attributes that
are not columns. `?sort=metadata`, `?sort=dataset` (the relationship) and
`?sort=has_dino_layer_embeddings` (the `@property`) each returned something
SQLAlchemy cannot order by and raised a **500** — the `getattr` default only ever
caught names that do not exist at all. An allowlist plus coercion closes that,
and coercion rather than a 400 is deliberate: `sortIdx` is persisted as an array
index in `gallery-state-${datasetId}`, so a stale one must degrade to a working
gallery rather than break it.

The two lineage sorts need explicit nulls-last handling. The generic branch has
none, so an ASC sort on `source_timestamp_ms` would float every image that never
came from a video to the top of the list — which is most of a typical dataset.

No cv2: lineage is written straight onto the rows. What is under test is the
ordering SQL, not extraction.
"""
from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


def _names(payload) -> list[str]:
    rows = payload["images"] if isinstance(payload, dict) else payload
    return [r["filename"] for r in rows]


async def _frames(env, dataset_id: str, spec: list[tuple[str, int | None, int | None]]) -> None:
    """Upload each `(name, timestamp_ms, shot_index)`; None means "not a frame"."""
    for i, (name, ts, shot) in enumerate(spec):
        await upload_image(env, dataset_id, name, png_bytes((10 * i + 5, 20, 30)))
    async with env.Session() as db:
        for name, ts, shot in spec:
            row = (await db.execute(
                select(Image).where(Image.dataset_id == dataset_id, Image.filename == name)
            )).scalar_one()
            row.source_timestamp_ms = ts
            row.source_shot_index = shot
        await db.commit()


def test_a_non_column_attribute_no_longer_500s(tmp_path):
    """The regression. All three of these are real attributes on `Image` and none
    is orderable, so each reached SQLAlchemy and blew up server-side."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png")
            await upload_image(env, ds["id"], "b.png")

            baseline = _names((await env.client.get(
                f"{API}/images/", params={"dataset_id": ds["id"], "sort": "created_at",
                                          "order": "asc"})).json())

            for bad in ("metadata", "dataset", "has_dino_layer_embeddings"):
                r = await env.client.get(
                    f"{API}/images/",
                    params={"dataset_id": ds["id"], "sort": bad, "order": "asc"},
                )
                assert r.status_code == 200, f"{bad}: {r.status_code} {r.text}"
                assert _names(r.json()) == baseline, bad

    run(scenario())


def test_an_unknown_sort_name_still_falls_back_silently(tmp_path):
    """A stale persisted `sortIdx` must degrade, never 400 — the gallery would be
    unusable until the user found the sort dropdown."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png")

            r = await env.client.get(
                f"{API}/images/",
                params={"dataset_id": ds["id"], "sort": "not_a_column_at_all"},
            )
            assert r.status_code == 200, r.text
            assert _names(r.json()) == ["a.png"]

    run(scenario())


def test_video_timeline_sorts_frames_ascending_with_non_frames_last(tmp_path):
    """The "Video timeline" gallery option. Nulls last is the load-bearing half:
    without it every ordinary upload in the dataset sorts ahead of every frame."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _frames(env, ds["id"], [
                ("late.png", 9000, 2),
                ("plain.png", None, None),
                ("early.png", 1000, 0),
                ("mid.png", 5000, 1),
            ])

            r = await env.client.get(f"{API}/images/", params={
                "dataset_id": ds["id"], "sort": "source_timestamp_ms", "order": "asc",
            })
            assert r.status_code == 200, r.text
            assert _names(r.json()) == ["early.png", "mid.png", "late.png", "plain.png"]

    run(scenario())


def test_shot_order_sorts_ascending_with_non_frames_last(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _frames(env, ds["id"], [
                ("c.png", 3000, 5),
                ("plain.png", None, None),
                ("a.png", 1000, 1),
                ("b.png", 2000, 3),
            ])

            r = await env.client.get(f"{API}/images/", params={
                "dataset_id": ds["id"], "sort": "source_shot_index", "order": "asc",
            })
            assert r.status_code == 200, r.text
            assert _names(r.json()) == ["a.png", "b.png", "c.png", "plain.png"]

    run(scenario())


def test_equal_timestamps_break_ties_by_created_at(tmp_path):
    """Two frames cut from the same held shot share a timestamp. Without the
    tiebreak their relative order is SQLite's scan order, so the gallery reshuffles
    between two identical requests."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _frames(env, ds["id"], [
                ("first.png", 4000, 1),
                ("second.png", 4000, 1),
                ("third.png", 4000, 1),
            ])

            seen = set()
            for _ in range(3):
                r = await env.client.get(f"{API}/images/", params={
                    "dataset_id": ds["id"], "sort": "source_timestamp_ms", "order": "asc",
                })
                assert r.status_code == 200, r.text
                seen.add(tuple(_names(r.json())))
            assert len(seen) == 1, f"unstable ordering across identical requests: {seen}"
            # Upload order is created_at order, which is the declared tiebreak.
            assert seen.pop() == ("first.png", "second.png", "third.png")

    run(scenario())


def test_nulls_stay_last_when_the_lineage_sort_runs_descending(tmp_path):
    """The UI only offers ascending, but the param is free-form. NULL means "not
    a video frame", which belongs at the end whichever way the frames run —
    a plain `.desc()` would put every non-frame first on most backends."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _frames(env, ds["id"], [
                ("early.png", 1000, 0),
                ("plain.png", None, None),
                ("late.png", 9000, 2),
            ])

            r = await env.client.get(f"{API}/images/", params={
                "dataset_id": ds["id"], "sort": "source_timestamp_ms", "order": "desc",
            })
            assert r.status_code == 200, r.text
            assert _names(r.json()) == ["late.png", "early.png", "plain.png"]

    run(scenario())


def test_every_ui_sort_option_is_on_the_allowlist():
    """The structural guard. `SORT_OPTIONS` in
    `frontend/src/constants/galleryOptions.ts` is the client's whole vocabulary;
    a name it offers that the allowlist does not hold is silently coerced to
    `created_at`, so the dropdown entry does nothing at all.
    """
    import re
    from pathlib import Path

    from backend.routers.images import _ALLOWED_SORT_FIELDS

    src = (
        Path(__file__).resolve().parent.parent.parent
        / "frontend" / "src" / "constants" / "galleryOptions.ts"
    ).read_text(encoding="utf-8")
    offered = set(re.findall(r'sort:\s*"([a-z_]+)"', src))
    assert offered, "could not parse SORT_OPTIONS — has the file moved?"
    missing = offered - _ALLOWED_SORT_FIELDS
    assert not missing, (
        f"the gallery offers these sorts but the backend allowlist coerces them "
        f"away: {sorted(missing)}"
    )
