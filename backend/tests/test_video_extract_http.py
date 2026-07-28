"""Frame extraction at request level: `POST /videos/{id}/probe` and `/videos/extract`.

The claim this whole phase rests on is that a video becomes *ordinary* `Image`
rows — so that dedup, scoring, captioning, export and versioning stay entirely
media-unaware. These tests pin the boundary from the outside: what the endpoints
accept, what lands in the database, and what is on disk afterwards.

Two tests here are worth more than the rest.

`test_decode_fixups_are_written_by_the_endpoint_not_the_job` reads the `Video`
row immediately after the 202, before the job has finished. That is the only way
to distinguish "the endpoint wrote the crop" from "the job did", and the
difference matters: the values have to survive a cancelled or failed run,
because "add to the existing subfolder" reads them back off the row.

`test_replace_keeps_a_pre_existing_snapshot_restorable` proves the replace-mode
delete went through `mark_image_deleted_in_versions` and not a raw unlink. A
replace destroys the previous extraction; it is only acceptable at all because
versioning can still bring it back.
"""

import asyncio
import shutil
from collections import namedtuple
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.models import BackgroundJob, Image, Video
from backend.services import video_extract
from backend.tests.conftest import (
    API,
    api_env,
    frame_colour,
    mp4_bytes,
    mp4_shots_bytes,
    run,
    upload_video,
    wait_for_job,
)
from backend.utils import DISK_FLOOR_BYTES

pytestmark = pytest.mark.skipif(
    not video_extract.capabilities()["shot_detection"],
    reason="scenedetect is not installed",
)

SHOTS_MP4 = mp4_shots_bytes()

GB = 2 ** 30
_Usage = namedtuple("_Usage", "total used free")


def _usage(free_bytes: int):
    """A fake shutil.disk_usage reporting `free_bytes` free — `test_disk_preflight`'s."""
    def fake(_path):
        return _Usage(100 * GB, 100 * GB - free_bytes, free_bytes)
    return fake


async def _extract(env, video_ids, **kwargs):
    body = {"video_ids": video_ids, **kwargs}
    return await env.client.post(f"{API}/videos/extract", json=body)


async def _extract_and_wait(env, video_ids, **kwargs):
    r = await _extract(env, video_ids, **kwargs)
    assert r.status_code == 200, r.text
    body = r.json()
    jobs = [await wait_for_job(env, j["job_id"], timeout=120) for j in body["jobs"]]
    return body, jobs


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def test_probe_returns_data_urls_that_actually_decode(tmp_path):
    """A data URL, not a temp file: a file would need a serving endpoint, a
    cleanup sweep and a traversal guard on an unauthenticated server."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            r = await env.client.post(f"{API}/videos/{video['id']}/probe", json={"samples": 5})
            assert r.status_code == 200, r.text
            body = r.json()

            assert len(body["samples"]) == 5
            assert body["capabilities"]["shot_detection"] is True
            assert body["width"] == 320 and body["height"] == 240

            import base64
            import io

            from PIL import Image as PilImage

            for sample in body["samples"]:
                prefix, _, payload = sample["data_url"].partition(",")
                assert prefix == "data:image/jpeg;base64"
                with PilImage.open(io.BytesIO(base64.b64decode(payload))) as img:
                    assert img.format == "JPEG"
                    assert max(img.size) <= 640

            # Ascending, and inset from both ends — frame 0 is very often a
            # black leader and the last frame very often a fade.
            stamps = [s["timestamp_ms"] for s in body["samples"]]
            assert stamps == sorted(stamps)
            assert stamps[0] > 0

    run(scenario())


def test_probe_backfills_a_missing_duration(tmp_path):
    """The probe is the one place that can correct a header the container could
    not supply. Everything downstream — percentage, tail trim, sample positions
    — needs a real number."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                row.duration_ms = None
                await db.commit()

            r = await env.client.post(f"{API}/videos/{video['id']}/probe", json={"samples": 3})
            assert r.status_code == 200, r.text
            assert r.json()["duration_source"] == "measured"
            assert r.json()["duration_ms"] > 3000

            async with env.Session() as db:
                assert (await db.get(Video, video["id"])).duration_ms > 3000

    run(scenario())


def test_probe_does_not_write_crop_or_trims(tmp_path):
    """The modal re-probes on every trim-handle drag. A preview must not commit."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            r = await env.client.post(
                f"{API}/videos/{video['id']}/probe",
                json={"samples": 3, "trim_start_ms": 500, "trim_end_ms": 200},
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                assert row.trim_start_ms == 0 and row.trim_end_ms == 0
                assert row.crop_x is None and row.crop_w is None

    run(scenario())


def test_probe_404s_for_an_unknown_video(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.create_dataset("d")
            r = await env.client.post(f"{API}/videos/nope/probe", json={})
            assert r.status_code == 404

    run(scenario())


def test_probe_rejects_an_out_of_range_sample_count(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            r = await env.client.post(f"{API}/videos/{video['id']}/probe", json={"samples": 99})
            assert r.status_code == 422

    run(scenario())


# ---------------------------------------------------------------------------
# Extract — the happy path
# ---------------------------------------------------------------------------


def test_extraction_produces_ordinary_image_rows_with_lineage(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            body, jobs = await _extract_and_wait(env, [video["id"]])
            assert [j["status"] for j in jobs] == ["completed"], jobs
            subfolder = body["jobs"][0]["subfolder"]
            assert subfolder == "clip"

            async with env.Session() as db:
                images = (await db.execute(select(Image).order_by(Image.filename))).scalars().all()

            assert len(images) == 3, [i.filename for i in images]
            import re

            for img in images:
                assert re.fullmatch(r"clip_s\d{4}_\d{2}\.jpg", img.filename), img.filename
                assert img.subfolder == subfolder
                assert img.source_video_id == video["id"]
                assert img.source_timestamp_ms is not None
                assert img.source_shot_index is not None
                assert img.width and img.height
                assert img.is_auto_named is True
                # Flat in images/, thumbnails flat in thumbnails/ — `subfolder`
                # is a DB grouping, never a directory.
                assert Path(img.file_path).parent.name == "images"
                assert Path(img.file_path).exists()
                assert Path(img.thumbnail_path).exists()

            # Shot indices ascend with timestamps, and the three frames come
            # from three visibly different shots.
            assert [i.source_shot_index for i in images] == [0, 1, 2]
            colours = [frame_colour(i.file_path) for i in images]
            assert len({c for c in colours}) == 3, colours

            r = await env.client.get(f"{API}/datasets/{ds['id']}")
            assert r.json()["image_count"] == 3

    run(scenario())


def test_frames_per_shot_writes_that_many_per_shot(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            _body, jobs = await _extract_and_wait(env, [video["id"]], frames_per_shot=2)
            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["written"] == 6

            async with env.Session() as db:
                images = (await db.execute(select(Image))).scalars().all()
            assert len(images) == 6
            assert sorted(i.source_shot_index for i in images) == [0, 0, 1, 1, 2, 2]

    run(scenario())


def test_a_video_with_no_cuts_still_yields_frames(tmp_path):
    """`get_scene_list()` returns `[]` — not one scene — when nothing was
    detected. Naive code writes zero frames and reports success."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            flat = mp4_bytes(frames=40, size=(320, 240))
            video = await upload_video(env, ds["id"], "flat.mp4", flat)

            _body, jobs = await _extract_and_wait(env, [video["id"]])
            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["written"] >= 1

    run(scenario())


def test_long_edge_bounds_the_written_frame(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            _body, jobs = await _extract_and_wait(env, [video["id"]], long_edge=160)
            assert jobs[0]["status"] == "completed", jobs

            async with env.Session() as db:
                images = (await db.execute(select(Image))).scalars().all()
            for img in images:
                assert max(img.width, img.height) == 160

    run(scenario())


# ---------------------------------------------------------------------------
# Extract — the endpoint's own contract
# ---------------------------------------------------------------------------


def test_decode_fixups_are_written_by_the_endpoint_not_the_job(tmp_path):
    """Read the row *before* the job finishes. This is the only way to tell the
    endpoint wrote the crop from the job having written it, and the difference
    matters: the values must survive a cancelled or failed run."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            r = await _extract(
                env,
                [video["id"]],
                crop={"x": 20, "y": 20, "w": 280, "h": 200},
                trim_start_ms=100,
                trim_end_ms=50,
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (20, 20, 280, 200)
                assert row.trim_start_ms == 100
                assert row.trim_end_ms == 50

            for job in r.json()["jobs"]:
                await wait_for_job(env, job["job_id"], timeout=120)

    run(scenario())


def test_clear_crop_removes_a_stored_rect(tmp_path):
    """`crop: None` alone is ambiguous between "leave it alone" and "the user
    cleared it", and a re-extraction would replay the stale rect forever."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            r = await _extract(env, [video["id"]], crop={"x": 20, "y": 20, "w": 280, "h": 200})
            for job in r.json()["jobs"]:
                await wait_for_job(env, job["job_id"], timeout=120)

            r = await _extract(env, [video["id"]], clear_crop=True)
            assert r.status_code == 200, r.text
            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                assert row.crop_x is None and row.crop_w is None
            for job in r.json()["jobs"]:
                await wait_for_job(env, job["job_id"], timeout=120)

    run(scenario())


def test_a_crop_outside_the_frame_is_a_400(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            r = await _extract(env, [video["id"]], crop={"x": 300, "y": 0, "w": 200, "h": 100})
            assert r.status_code == 400
            assert "does not fit" in r.json()["detail"]

            async with env.Session() as db:
                assert (await db.get(Video, video["id"])).crop_x is None

    run(scenario())


def test_a_full_frame_crop_is_stored_as_no_crop(tmp_path):
    """`clamp_crop` returns None for a rect covering the whole frame — that is
    "no crop", not an error. Rejecting it (which comparing clamp output against
    the request did) 400s a rect that plainly fits."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            r = await _extract(env, [video["id"]], crop={"x": 0, "y": 0, "w": 320, "h": 240})
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (None, None, None, None)

            for job in r.json()["jobs"]:
                await wait_for_job(env, job["job_id"], timeout=120)

    run(scenario())


def test_an_odd_crop_is_stored_snapped_not_rejected(tmp_path):
    """The stored rect is the rect that will be applied, so Phase 4 replays what
    pass 1 used: `clamp_crop`'s even-snap happens once, here."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            r = await _extract(env, [video["id"]], crop={"x": 1, "y": 1, "w": 101, "h": 101})
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (0, 0, 100, 100)

            for job in r.json()["jobs"]:
                await wait_for_job(env, job["job_id"], timeout=120)

    run(scenario())


def test_one_crop_across_differently_sized_videos_is_a_400(tmp_path):
    """A series where half the episodes are letterboxed and half are not is a
    silently inconsistent dataset, so this names the videos rather than skipping
    some of them."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            big = await upload_video(env, ds["id"], "big.mp4", SHOTS_MP4)
            small = await upload_video(env, ds["id"], "small.mp4", mp4_bytes(size=(64, 48)))

            r = await _extract(
                env, [big["id"], small["id"]], crop={"x": 0, "y": 10, "w": 64, "h": 28}
            )
            assert r.status_code == 400
            assert "big.mp4" in r.json()["detail"] and "small.mp4" in r.json()["detail"]

    run(scenario())


def test_deinterlace_without_ffmpeg_is_a_503(tmp_path, monkeypatch):
    """A 503 with actionable text, not a job that dies with an ImportError five
    minutes after the user pressed the button."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            monkeypatch.setattr(
                video_extract, "capabilities",
                lambda: {"shot_detection": True, "deinterlace": False,
                         "scenedetect_version": "0.7.1", "ffmpeg_version": None},
            )
            r = await _extract(env, [video["id"]], deinterlace="bwdif")
            assert r.status_code == 503
            assert "imageio-ffmpeg" in r.json()["detail"]

    run(scenario())


def test_a_stored_deinterlace_is_a_503_even_when_the_body_omits_it(tmp_path, monkeypatch):
    """The job reads `Video.deinterlace`, so the gate has to test the *effective*
    value. Testing `body.deinterlace` alone lets a row carrying a filter from an
    earlier run through, and `require_deinterlace` then raises inside every shot
    until the circuit breaker reports a missing package as a decode fault."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                row.deinterlace = "bwdif"
                await db.commit()

            monkeypatch.setattr(
                video_extract, "capabilities",
                lambda: {"shot_detection": True, "deinterlace": False,
                         "scenedetect_version": "0.7.1", "ffmpeg_version": None},
            )
            r = await _extract(env, [video["id"]])
            assert r.status_code == 503, r.text
            assert "imageio-ffmpeg" in r.json()["detail"]

    run(scenario())


def test_a_full_disk_is_a_507_before_any_job_exists(tmp_path, monkeypatch):
    """A full volume must be one 507 the user sees immediately, not N jobs that
    each die minutes later. Patched on the `shutil` module, not the router's
    symbol — `require_free_space` resolves it through the module attribute."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            # After the upload: ingesting the video needs a working disk check.
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            monkeypatch.setattr(shutil, "disk_usage", _usage(DISK_FLOOR_BYTES // 4))
            r = await _extract(env, [video["id"]])
            assert r.status_code == 507, r.text
            assert "disk space" in r.json()["detail"]

            async with env.Session() as db:
                assert (await db.execute(select(BackgroundJob))).scalars().all() == []

    run(scenario())


def test_a_second_extraction_for_the_same_video_is_skipped(tmp_path):
    """`rescan_folder`'s precedent: the duplicate is named and the rest of the
    batch still enqueues."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_video(env, ds["id"], "a.mp4", SHOTS_MP4)
            b = await upload_video(env, ds["id"], "b.mp4", SHOTS_MP4)

            first = await _extract(env, [a["id"]])
            assert first.status_code == 200, first.text

            second = await _extract(env, [a["id"], b["id"]])
            assert second.status_code == 200, second.text
            body = second.json()
            assert [s["video_id"] for s in body["skipped"]] == [a["id"]]
            assert [j["video_id"] for j in body["jobs"]] == [b["id"]]

            for r in (first, second):
                for job in r.json()["jobs"]:
                    await wait_for_job(env, job["job_id"], timeout=120)

    run(scenario())


def test_a_skipped_video_keeps_its_stored_fixups(tmp_path):
    """The dedupe runs before the fixup write, so a request that extracts nothing
    mutates nothing: the crop the in-flight job is using stays the crop on the row."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            first = await _extract(env, [video["id"]], crop={"x": 20, "y": 20, "w": 280, "h": 200})
            assert first.status_code == 200, first.text

            second = await _extract(env, [video["id"]], crop={"x": 0, "y": 0, "w": 100, "h": 100})
            assert second.status_code == 200, second.text
            assert second.json()["jobs"] == []
            assert [s["video_id"] for s in second.json()["skipped"]] == [video["id"]]

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (20, 20, 280, 200)

            for job in first.json()["jobs"]:
                await wait_for_job(env, job["job_id"], timeout=120)

    run(scenario())


def test_extract_404s_when_no_video_matches(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.create_dataset("d")
            r = await _extract(env, ["missing"])
            assert r.status_code == 404

    run(scenario())


# ---------------------------------------------------------------------------
# Subfolder modes
# ---------------------------------------------------------------------------


def test_new_subfolder_steps_past_a_name_another_video_already_uses(tmp_path):
    """Stepping against *this* video's history alone would let video B's "new"
    subfolder land inside one video A already fills. The names are asked for
    explicitly here because upload already disambiguates video *filenames* —
    `clip.mkv` next to `clip.mp4` becomes `clip_001.mkv` — so two videos never
    arrive at the same default slug on their own."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            b = await upload_video(env, ds["id"], "other.mp4", SHOTS_MP4)

            body_a, _ = await _extract_and_wait(env, [a["id"]], subfolder="frames")
            assert body_a["jobs"][0]["subfolder"] == "frames"

            body_b, _ = await _extract_and_wait(env, [b["id"]], subfolder="frames")
            assert body_b["jobs"][0]["subfolder"] == "frames_2"

            async with env.Session() as db:
                rows = (await db.execute(select(Image.subfolder).distinct())).scalars().all()
            assert set(rows) == {"frames", "frames_2"}

    run(scenario())


def test_re_extracting_one_video_steps_to_a_fresh_subfolder(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            first, _ = await _extract_and_wait(env, [video["id"]])
            second, jobs = await _extract_and_wait(env, [video["id"]])
            assert jobs[0]["status"] == "completed", jobs
            assert first["jobs"][0]["subfolder"] == "clip"
            assert second["jobs"][0]["subfolder"] == "clip_2"

    run(scenario())


def test_two_videos_in_one_batch_do_not_share_a_new_subfolder(tmp_path):
    """Claimed names are tracked within the request too, not only against the DB
    — nothing either job wrote is visible to the other at enqueue time."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            b = await upload_video(env, ds["id"], "other.mp4", SHOTS_MP4)

            body, jobs = await _extract_and_wait(env, [a["id"], b["id"]], subfolder="frames")
            assert [j["status"] for j in jobs] == ["completed", "completed"], jobs
            assert {j["subfolder"] for j in body["jobs"]} == {"frames", "frames_2"}

    run(scenario())


def test_add_mode_reuses_the_most_recent_subfolder(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            first, _ = await _extract_and_wait(env, [video["id"]])
            second, jobs = await _extract_and_wait(env, [video["id"]], mode="add")
            assert jobs[0]["status"] == "completed", jobs
            assert second["jobs"][0]["subfolder"] == first["jobs"][0]["subfolder"]

            async with env.Session() as db:
                images = (await db.execute(select(Image))).scalars().all()
            assert len(images) == 6
            assert {i.subfolder for i in images} == {"clip"}

    run(scenario())


def test_replace_removes_the_previous_frames_rows_files_and_thumbnails(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            await _extract_and_wait(env, [video["id"]])
            async with env.Session() as db:
                before = (await db.execute(select(Image))).scalars().all()
                images_dir = Path(before[0].file_path).parent
                thumbs_dir = Path(before[0].thumbnail_path).parent

            _body, jobs = await _extract_and_wait(env, [video["id"]], mode="replace")
            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["replaced"] == 3

            async with env.Session() as db:
                after = (await db.execute(select(Image))).scalars().all()
            assert len(after) == 3
            assert {i.id for i in after}.isdisjoint({i.id for i in before})
            # Nothing accumulated on disk. The replacements reuse the same
            # filenames — deliberately, since the delete runs first and the
            # uniquifier then sees them free — so counting the directory is what
            # proves the old files went away rather than piling up beside the new
            # ones. `_001` suffixes here would mean the delete did not happen.
            assert sorted(p.name for p in images_dir.glob("*.jpg")) == [
                i.filename for i in sorted(after, key=lambda x: x.filename)
            ]
            assert len(list(thumbs_dir.glob("*.webp"))) == 3

    run(scenario())


def test_replace_only_touches_this_videos_frames(tmp_path):
    """Scoped to `source_video_id` within the subfolder, so a subfolder the user
    also hand-filled does not lose the hand-filled part."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            body, _ = await _extract_and_wait(env, [video["id"]])
            subfolder = body["jobs"][0]["subfolder"]

            from backend.tests.conftest import upload_image

            stranger = await upload_image(env, ds["id"], "hand.png")
            r = await env.client.post(
                f"{API}/images/batch/move-subfolder",
                json={"image_ids": [stranger["id"]], "subfolder": subfolder},
            )
            assert r.status_code == 200, r.text

            _b, jobs = await _extract_and_wait(env, [video["id"]], mode="replace")
            assert jobs[0]["status"] == "completed", jobs

            async with env.Session() as db:
                survivor = await db.get(Image, stranger["id"])
            assert survivor is not None
            assert Path(survivor.file_path).exists()

    run(scenario())


def test_replace_keeps_a_pre_existing_snapshot_restorable(tmp_path):
    """Proves the delete went through `mark_image_deleted_in_versions` rather
    than a raw unlink. Backing the content into the object store first is what
    makes destroying the previous extraction acceptable at all."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            await _extract_and_wait(env, [video["id"]])

            # Snapshots need versioning switched on; it defaults to off.
            r = await env.client.patch(
                f"{API}/settings/thresholds", json={"versioning_mode": "manual"}
            )
            assert r.status_code == 200, r.text
            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/versions", json={"name": "before-replace"}
            )
            assert r.status_code in (200, 201, 202), r.text
            payload = r.json()
            if "job_id" in payload:
                await wait_for_job(env, payload["job_id"], timeout=60)
            listing = (await env.client.get(f"{API}/datasets/{ds['id']}/versions")).json()
            version_id = listing[0]["id"]

            async with env.Session() as db:
                originals = {
                    i.filename for i in (await db.execute(select(Image))).scalars().all()
                }

            _b, jobs = await _extract_and_wait(env, [video["id"]], mode="replace")
            assert jobs[0]["status"] == "completed", jobs

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/versions/{version_id}/restore",
                json={"handle_extra_images": "remove"},
            )
            assert r.status_code in (200, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                restored = (await db.execute(select(Image))).scalars().all()
            assert {i.filename for i in restored} == originals
            for img in restored:
                assert Path(img.file_path).exists()
                # The lineage columns are mirrored on VersionImageState, so a
                # restore brings them back rather than silently blanking them.
                assert img.source_video_id == video["id"]
                assert img.source_shot_index is not None

    run(scenario())


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_a_job_that_cannot_decode_fails_rather_than_reporting_success(tmp_path):
    """`detect_shots` refuses the uniform fallback for a file that will not open.
    Falling back would slice a nonexistent stream into windows and finish
    "completed" with zero frames."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                Path(row.file_path).write_bytes(b"not a video any more")

            _body, jobs = await _extract_and_wait(env, [video["id"]])
            assert jobs[0]["status"] == "failed", jobs
            assert "decode" in (jobs[0]["error_msg"] or "").lower()

    run(scenario())


def test_a_frame_whose_info_cannot_be_read_is_dropped_not_stored(tmp_path):
    """`get_image_info` swallows every exception, so `{}` means the file will not
    re-open. A NULL-dimension row silently breaks grid layout, the dimension
    filters, dedup and the detection remap."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            import backend.routers.videos as videos_router

            real = videos_router.get_image_info
            calls = {"n": 0}

            def flaky(path):
                calls["n"] += 1
                return {} if calls["n"] == 1 else real(path)

            videos_router.get_image_info = flaky
            try:
                _body, jobs = await _extract_and_wait(env, [video["id"]])
            finally:
                videos_router.get_image_info = real

            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["failed"] == 1
            assert jobs[0]["result_data"]["written"] == 2

            async with env.Session() as db:
                images = (await db.execute(select(Image))).scalars().all()
            assert len(images) == 2
            assert all(i.width and i.height for i in images)

    run(scenario())


def test_cancelling_keeps_the_frames_already_written_and_counted(tmp_path, monkeypatch):
    """The cancel is made deterministic — the first shot's render blocks until it
    has been posted, so the loop always breaks at shot two. Sleeping until the
    first `Image` appears instead lets a three-shot file finish outright, and the
    job then reports `completed`: the interesting half of this test, that
    `refresh_stats` still ran despite `raise_if_cancelled` raising past step 8,
    silently stops being exercised."""
    import threading

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            rendering = threading.Event()
            resume = threading.Event()
            real_render = video_extract.render_shot

            def blocking_render(*args, **kwargs):
                out = real_render(*args, **kwargs)
                if not rendering.is_set():
                    rendering.set()
                    resume.wait(60)
                return out

            monkeypatch.setattr(video_extract, "render_shot", blocking_render)

            r = await _extract(env, [video["id"]], frames_per_shot=1)
            job_id = r.json()["jobs"][0]["job_id"]
            for _ in range(600):
                if rendering.is_set():
                    break
                await asyncio.sleep(0.05)
            assert rendering.is_set(), "the first shot never rendered"
            # DELETE, not POST .../cancel: there is no such route, and a 405 that
            # nobody asserts on cancels nothing.
            cancel = await env.client.delete(f"{API}/jobs/{job_id}")
            assert cancel.status_code == 204, cancel.text
            resume.set()

            job = await wait_for_job(env, job_id, timeout=120)
            assert job["status"] == "cancelled", job

            async with env.Session() as db:
                images = (await db.execute(select(Image))).scalars().all()
            assert images, "the shot that had already rendered was not kept"
            for img in images:
                assert Path(img.file_path).exists()
                assert img.source_video_id == video["id"]

            # `raise_if_cancelled` skips the job's own final `refresh_stats`, so
            # the counters are only right because the runner is wrapped. Without
            # the wrapper the dataset card undercounts the frames it kept until
            # some unrelated write happens to refresh it.
            r = await env.client.get(f"{API}/datasets/{ds['id']}")
            assert r.json()["image_count"] == len(images)

    run(scenario())


def test_the_job_label_names_the_video_and_the_frame_count(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            r = await _extract(env, [video["id"]], frames_per_shot=1)
            job_id = r.json()["jobs"][0]["job_id"]
            async with env.Session() as db:
                job = await db.get(BackgroundJob, job_id)
            assert job.label == "Extract: clip — 1 frame/shot"
            assert len(job.label) <= 200

            r2 = await _extract(env, [video["id"]], label="custom")
            assert r2.json()["skipped"] or r2.json()["jobs"]

            await wait_for_job(env, job_id, timeout=120)

    run(scenario())
