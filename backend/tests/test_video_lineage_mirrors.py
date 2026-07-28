"""Frame lineage survives every path that copies, moves, snapshots or restores an Image.

Extraction writes `source_video_id`, `source_timestamp_ms` and
`source_shot_index` once and nothing ever changes them again — which is exactly
why they are easy to lose. Eight code paths rebuild an `Image` field by field
(two in `duplicate_dataset`, `batch_copy_dataset`, `batch_move_dataset`,
`create_snapshot`, the restore write-back, and the `VersionImageState` mirror
each of the last two depends on), every one of them fails **silently** when a
column is missed, and before this file none of them had a test.

`test_every_image_column_is_mirrored_on_version_image_state` is the structural
one: it fails for the *next* column somebody adds to `Image` without mirroring
it, which is the only kind of test that helps here. The rest are behavioural
round-trips through the paths that a structural test cannot reach.

The rule the behavioural tests pin: **a cross-dataset copy or move NULLs
`source_video_id` and keeps the timestamp and shot index.** The id would point
at a video the destination dataset does not contain; where in a video a frame
came from is a fact about the frame and travels with it.
"""

from pathlib import Path

from sqlalchemy import select

from backend.models import Image, Video
from backend.models.versioning import VersionImageState
from backend.services import version_service
from backend.tests.conftest import API, api_env, run, upload_image, upload_video, wait_for_job

LINEAGE = ("source_video_id", "source_timestamp_ms", "source_shot_index")

# Columns that live on `Image` and deliberately have no `VersionImageState`
# counterpart. Every entry needs a reason, because the default answer for a new
# column is "mirror it" — a snapshot restore writes back exactly what the mirror
# holds, so an unmirrored column is silently blanked by any restore.
# (`Image.id` is absent because it *is* mirrored, as `VersionImageState.image_id`
# alongside that table's own surrogate `id`.)
NOT_MIRRORED = {
    "dataset_id",            # a restore can target a different dataset
    "thumbnail_path",        # re-derived from file_path on restore
    "is_auto_named",         # a naming provenance flag, not image content
    "created_at",            # the state row carries the version's timestamp
    "updated_at",
    "phash",                 # recomputed from the restored file
    "nsfw_score",            # scored, not authored; recomputed on demand
    "saturation_score",
    "clip_embedding",        # blobs: megabytes per row, recomputed on demand
    "dino_embedding",
    "dino_layer_embeddings",
    "caption_token_count",   # derived from caption_text by the ORM listener
    "caption_style",         # captioning bookkeeping, not caption content
    "captioned_by",
    "captioned_at",
}


def _columns(model) -> set[str]:
    return {c.key for c in model.__table__.columns}


def test_every_image_column_is_mirrored_on_version_image_state():
    """The structural guard. This fails for the *next* column added to `Image`
    without a mirror, which is the whole reason it exists — every failure mode
    downstream of a missing mirror is silent."""
    missing = _columns(Image) - _columns(VersionImageState) - NOT_MIRRORED
    assert not missing, (
        f"{sorted(missing)} exist on Image but not on VersionImageState. "
        "Mirror them (and copy them in create_snapshot and the restore "
        "write-back), or add them to NOT_MIRRORED with a reason."
    )


def test_not_mirrored_has_no_stale_entries():
    """The allowlist must be exactly the unmirrored set, not merely a superset.

    An entry naming a column that was since dropped, or one that has since
    *gained* a mirror, is a reason nobody will read again — and either would
    silently absorb a genuinely missing mirror if that name came back.
    """
    stale = NOT_MIRRORED - (_columns(Image) - _columns(VersionImageState))
    assert not stale, (
        f"NOT_MIRRORED entries that are no longer needed: {sorted(stale)} "
        "(the column was dropped, or it is mirrored after all)"
    )


def test_lineage_is_mirrored_and_snapshotted_but_not_diffed():
    """Lineage is immutable per image, so it can never differ between two
    snapshots — it is stored and restored, just not *compared*."""
    assert set(LINEAGE) <= _columns(VersionImageState)
    selected = {c.key for c in version_service._DIFF_COLS}
    assert selected.isdisjoint(LINEAGE)
    # The existing invariant, extended rather than duplicated: everything the
    # comparison loop reads has to be selected, or the diff reports "unchanged"
    # for a value that changed.
    assert set(version_service._DIFF_COMPARE_FIELDS) <= selected


def test_version_image_state_does_not_carry_a_video_foreign_key():
    """Matching `image_id`, which is FK-free because a restore can target
    another dataset. A snapshot must also survive its source video's deletion —
    which is precisely when the lineage record is worth the most."""
    fks = {fk.parent.key for fk in VersionImageState.__table__.foreign_keys}
    assert "source_video_id" not in fks


def test_images_source_video_id_is_set_null_on_delete():
    """Belt-and-braces behind the explicit UPDATE in `DELETE /videos/{id}`.
    Asserted against the DDL because the test harness builds its schema with
    `create_all` and never gets the `PRAGMA foreign_keys=ON` that
    backend/database.py installs on the app engine."""
    fk = next(
        fk for fk in Image.__table__.foreign_keys if fk.parent.key == "source_video_id"
    )
    assert fk.column.table.name == "videos"
    assert fk.ondelete == "SET NULL"


# ---------------------------------------------------------------------------
# Behavioural round-trips
# ---------------------------------------------------------------------------


async def _make_frame(env, dataset_id: str, *, video_id: str, name: str = "frame.png") -> dict:
    """An image standing in for an extracted frame, with lineage written on it.

    Set directly rather than by running an extraction: these tests are about the
    eight paths that *carry* lineage, and routing every one of them through a
    real decode would make them slow and would couple them to the detector.
    """
    img = await upload_image(env, dataset_id, name)
    async with env.Session() as db:
        row = await db.get(Image, img["id"])
        row.source_video_id = video_id
        row.source_timestamp_ms = 4321
        row.source_shot_index = 7
        await db.commit()
    return img


def test_snapshot_and_restore_preserve_lineage(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            frame = await _make_frame(env, ds["id"], video_id=video["id"])

            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{ds['id']}/versions", json={"name": "v1"})
            assert r.status_code in (200, 201, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{ds['id']}/versions")).json()[0]["id"]

            async with env.Session() as db:
                state = (await db.execute(
                    select(VersionImageState).where(VersionImageState.image_id == frame["id"])
                )).scalar_one()
                assert state.source_video_id == video["id"]
                assert state.source_timestamp_ms == 4321
                assert state.source_shot_index == 7

                # Blank it on the live row, so the restore has something to put back.
                row = await db.get(Image, frame["id"])
                row.source_video_id = None
                row.source_timestamp_ms = None
                row.source_shot_index = None
                await db.commit()

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/versions/{version_id}/restore",
                json={"handle_extra_images": "remove"},
            )
            assert r.status_code in (200, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                row = await db.get(Image, frame["id"])
                assert row.source_video_id == video["id"]
                assert row.source_timestamp_ms == 4321
                assert row.source_shot_index == 7

    run(scenario())


def test_cross_dataset_copy_nulls_the_video_id_and_keeps_the_rest(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            video = await upload_video(env, src["id"], "clip.mp4")
            frame = await _make_frame(env, src["id"], video_id=video["id"])

            r = await env.client.post(
                f"{API}/images/batch/copy-dataset",
                json={"image_ids": [frame["id"]], "target_dataset_id": dest["id"], "subfolder": ""},
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id == dest["id"])
                )).scalar_one()
                original = await db.get(Image, frame["id"])

            assert copy.source_video_id is None
            assert copy.source_timestamp_ms == 4321
            assert copy.source_shot_index == 7
            # The original is untouched.
            assert original.source_video_id == video["id"]

    run(scenario())


def test_cross_dataset_move_nulls_the_video_id_and_keeps_the_rest(tmp_path):
    """A move is an UPDATE in place, so lineage survives unless it is explicitly
    cleared — the row would land in the target still pointing at a video the
    target does not contain."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            video = await upload_video(env, src["id"], "clip.mp4")
            frame = await _make_frame(env, src["id"], video_id=video["id"])

            r = await env.client.post(
                f"{API}/images/batch/move-dataset",
                json={"image_ids": [frame["id"]], "target_dataset_id": dest["id"], "subfolder": ""},
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                moved = await db.get(Image, frame["id"])
            assert moved.dataset_id == dest["id"]
            assert moved.source_video_id is None
            assert moved.source_timestamp_ms == 4321
            assert moved.source_shot_index == 7

    run(scenario())


def test_duplicate_dataset_from_disk_nulls_the_video_id_and_keeps_the_rest(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            video = await upload_video(env, src["id"], "clip.mp4")
            await _make_frame(env, src["id"], video_id=video["id"])

            r = await env.client.post(f"{API}/datasets/{src['id']}/duplicate", json={"new_name": "copy"})
            assert r.status_code in (200, 202), r.text
            await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                new_ds_id = (await db.execute(
                    select(Image.dataset_id).where(Image.dataset_id != src["id"])
                )).scalars().first()
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id == new_ds_id)
                )).scalar_one()

            assert copy.source_video_id is None
            assert copy.source_timestamp_ms == 4321
            assert copy.source_shot_index == 7
            # duplicate_dataset copies Image rows only, so the new dataset has
            # no videos for an id to point at in the first place.
            async with env.Session() as db:
                videos = (await db.execute(
                    select(Video).where(Video.dataset_id == new_ds_id)
                )).scalars().all()
            assert videos == []

    run(scenario())


def test_duplicate_dataset_from_a_snapshot_nulls_the_video_id_and_keeps_the_rest(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            video = await upload_video(env, src["id"], "clip.mp4")
            await _make_frame(env, src["id"], video_id=video["id"])

            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{src['id']}/versions", json={"name": "v1"})
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{src['id']}/versions")).json()[0]["id"]

            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate",
                json={"new_name": "from-snapshot", "source_version_id": version_id},
            )
            assert r.status_code in (200, 202), r.text
            await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id != src["id"])
                )).scalars().one()

            assert copy.source_video_id is None
            assert copy.source_timestamp_ms == 4321
            assert copy.source_shot_index == 7

    run(scenario())


def test_deleting_a_video_leaves_its_frames_with_null_lineage_and_intact_files(tmp_path):
    """Frames are curated data. Deleting a source must not destroy them, and the
    timestamp and shot index survive — a frame keeps knowing where in a video it
    came from even once the video is gone."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            frame = await _make_frame(env, ds["id"], video_id=video["id"])

            r = await env.client.delete(f"{API}/videos/{video['id']}")
            assert r.status_code == 204, r.text

            async with env.Session() as db:
                row = await db.get(Image, frame["id"])
                await db.refresh(row)
            assert row is not None
            assert row.source_video_id is None
            assert row.source_timestamp_ms == 4321
            assert row.source_shot_index == 7
            assert Path(row.file_path).exists()

    run(scenario())


def test_a_derivative_of_a_frame_has_no_lineage(tmp_path):
    """`copy_provenance` returns the five provenance keys and nothing else, so
    crop/upscale/LUT/detection-crop derivatives inherit no lineage — the pixels
    are no longer the extracted frame.

    The hazard this does *not* cover is the **replace** mode of those same
    operations, which mutates the row in place and therefore keeps its lineage.
    Any re-extraction pass must skip or warn on a frame with a non-empty
    `processing_history`.
    """
    from backend.licenses import copy_provenance

    class _Frame:
        source_name = "Flickr"
        source_url = "https://flickr.test/p/1"
        license = "CC-BY-4.0"
        attribution = "Jane Doe"
        source_meta = {"post_id": 1}
        source_video_id = "vid-1"
        source_timestamp_ms = 4321
        source_shot_index = 7

    copied = copy_provenance(_Frame())
    assert set(copied).isdisjoint(LINEAGE)
    assert copied["license"] == "CC-BY-4.0"
