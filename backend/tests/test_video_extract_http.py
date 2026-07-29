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
    upload_image,
    upload_video,
    wait_for_job,
)
from backend.utils import DISK_FLOOR_BYTES, InsufficientDiskSpaceError

pytestmark = pytest.mark.skipif(
    not video_extract.capabilities()["shot_detection"],
    reason="scenedetect is not installed",
)

# `pytestmark` is evaluated after the module body, so it cannot protect the line
# below: without cv2 this module errors at *collection* rather than skipping.
pytest.importorskip("cv2", reason="opencv is not installed")

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
                    # Exact, not `<= PROBE_PREVIEW_EDGE`: the fixture is already
                    # under the default cap, so a bound would stay green with the
                    # downscale deleted. This pins no-upscale and no aspect
                    # distortion at once.
                    assert img.size == (320, 240)

            # Ascending, and inset from both ends — frame 0 is very often a
            # black leader and the last frame very often a fade.
            stamps = [s["timestamp_ms"] for s in body["samples"]]
            assert stamps == sorted(stamps)
            assert stamps[0] > 0

            # And a cap below the source really downscales, aspect kept.
            r = await env.client.post(
                f"{API}/videos/{video['id']}/probe", json={"samples": 2, "max_edge": 160}
            )
            assert r.status_code == 200, r.text
            for sample in r.json()["samples"]:
                _prefix, _, payload = sample["data_url"].partition(",")
                with PilImage.open(io.BytesIO(base64.b64decode(payload))) as img:
                    assert img.size == (160, 120)

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


def test_a_resolution_change_mid_probe_costs_one_sample_not_the_run(tmp_path):
    """A concatenated or variable-resolution source hands the probe frames of
    two sizes. `merge_profiles` refuses to accumulate mismatched shapes, and that
    ValueError escaped the per-sample `try` and surfaced as a raw 422 quoting
    numpy shapes — "profile shape changed mid-run: (240,) vs (120,)". Same rule
    as the failed seek: one bad sample costs one sample."""
    async def scenario():
        import cv2

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            real_capture = cv2.VideoCapture

            class _ShrinksOneFrame:
                """Delegates to a real capture, but hands back the second frame
                at half size — the shape mismatch, with everything else intact."""

                def __init__(self, path):
                    self._inner = real_capture(path)
                    self._reads = 0

                def read(self):
                    ok, frame = self._inner.read()
                    self._reads += 1
                    if ok and frame is not None and self._reads == 2:
                        frame = frame[:120, :160].copy()
                    return ok, frame

                def __getattr__(self, name):
                    return getattr(self._inner, name)

            cv2.VideoCapture = _ShrinksOneFrame
            try:
                r = await env.client.post(
                    f"{API}/videos/{video['id']}/probe", json={"samples": 5}
                )
            finally:
                cv2.VideoCapture = real_capture

            assert r.status_code == 200, r.text
            body = r.json()
            assert body["samples_failed"] >= 1
            assert len(body["samples"]) == 4
            # The rejected sample left no dimensions behind.
            assert body["width"] == 320 and body["height"] == 240

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


def test_a_probe_that_takes_too_long_is_a_504(tmp_path, monkeypatch):
    """A probe runs in the *request*, so a video on slow storage would otherwise
    hold a worker until the client gives up. Both halves are patched — the
    constant (read at call time) and the sampler — because the alternative, a
    genuinely slow decode, would be the flakiest test in the repo."""
    async def scenario():
        from backend.routers import videos as videos_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            real = video_extract.probe_samples

            def slow(*a, **kw):
                import time as _time
                _time.sleep(0.5)
                return real(*a, **kw)

            monkeypatch.setattr(videos_router, "PROBE_TIMEOUT_SECONDS", 0.01)
            monkeypatch.setattr(video_extract, "probe_samples", slow)

            r = await env.client.post(f"{API}/videos/{video['id']}/probe", json={"samples": 3})
            assert r.status_code == 504, r.text
            assert "too long" in r.json()["detail"]

    run(scenario())


def test_capabilities_has_its_own_route_and_is_not_shadowed(tmp_path):
    """The route exists so a video that will not probe can still be configured.

    The shadowing half is the real risk: `/capabilities` is a literal segment
    competing with `GET /videos/{video_id}`, and FastAPI matches in declaration
    order — moved below it, this returns a 404 "Video not found" instead.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/videos/capabilities")
            assert r.status_code == 200, r.text
            body = r.json()
            assert set(body) == set(video_extract.capabilities())
            assert isinstance(body["shot_detection"], bool)
            assert isinstance(body["deinterlace"], bool)

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
            written = jobs[0]["result_data"]["written"]
            assert written >= 1

            # The count is what the job reports; the rows are what the gallery
            # shows. Cross-check them, as every neighbouring test does.
            async with env.Session() as db:
                images = (await db.execute(select(Image))).scalars().all()
            assert len(images) == written
            for img in images:
                assert img.source_video_id == video["id"]
                assert img.source_timestamp_ms is not None
                assert Path(img.file_path).exists()

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
                # All four: a clear that left `crop_y`/`crop_h` behind is exactly
                # the stale-rect replay this endpoint exists to prevent.
                assert (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == (None,) * 4
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


def test_a_disk_that_fills_mid_run_fails_the_job_and_keeps_what_it_committed(
    tmp_path, monkeypatch
):
    """The other half of the disk story: the request path 507s, the *mid-run*
    path fails the job. `EXTRACT_DISK_RECHECK_EVERY` is 100 in production, so
    nothing else here ever reaches the in-loop check.

    Not patched on `shutil.disk_usage` like the 507 test above: that fake would
    also trip the request-path preflight and both pre-loop preflights, and
    counting calls to time it is brittle. Gating the router's own symbol on "has
    any frame rendered yet" puts the failure exactly where it belongs — both
    pre-loop checks run before any render and pass, and the in-loop check is the
    first call afterwards.
    """
    import threading

    async def scenario():
        from backend.routers import videos as videos_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            rendered = threading.Event()
            real_render = video_extract.render_shot
            real_require = videos_router.require_free_space

            def render_spy(*a, **kw):
                try:
                    return real_render(*a, **kw)
                finally:
                    rendered.set()

            def require_spy(target_dir, needed_bytes=0, **kw):
                if rendered.is_set():
                    raise InsufficientDiskSpaceError(
                        "Not enough disk space on the destination volume."
                    )
                return real_require(target_dir, needed_bytes, **kw)

            monkeypatch.setattr(video_extract, "render_shot", render_spy)
            monkeypatch.setattr(videos_router, "require_free_space", require_spy)
            monkeypatch.setattr(videos_router, "EXTRACT_DISK_RECHECK_EVERY", 1)

            r = await _extract(env, [video["id"]])
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["jobs"][0]["job_id"], timeout=120)

            # 1. The distinction this test exists for.
            assert job["status"] == "failed", job
            assert "disk space" in (job["error_msg"] or "")

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            assert rows, "the check fired before anything was written, proving nothing"

            # 2. The invariant the in-loop comment claims: the commit happens
            #    *before* the check, so no file exists without a row.
            images_dir = Path(rows[0].file_path).parent
            assert len(list(images_dir.glob("*.jpg"))) == len(rows)

            # 3. Frames already written are kept.
            for img in rows:
                assert Path(img.file_path).exists()

            # 4. `_run_with_stats` refreshed the counters on the raise path.
            detail = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            assert detail["image_count"] == len(rows)

    run(scenario())


def test_extract_409s_while_the_dataset_is_busy(tmp_path):
    """The versioning guard, asserted through its contract (the 409) rather than
    `dataset_busy._busy`. The empty job table is half the point: a guard that ran
    after the enqueue would leave a job to rewrite the very files a restore is
    reading."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import dataset_busy

            ds = await env.create_dataset("d")
            # Before the flag: ingesting the video needs an unbusy dataset.
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            with dataset_busy.busy(ds["id"], "versioning"):
                r = await _extract(env, [video["id"]])
            assert r.status_code == 409, r.text

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


def test_an_id_that_no_longer_resolves_is_reported_not_fatal(tmp_path):
    """One deleted video in a fifty-video batch must not cost the other
    forty-nine their run — the re-extract resolver's contract, mirrored here. The
    404 above still covers "nothing at all resolved", where there is no run to
    report on."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            r = await _extract(env, [video["id"], "missing"])
            assert r.status_code == 200, r.text
            body = r.json()
            assert [j["video_id"] for j in body["jobs"]] == [video["id"]]
            assert body["skipped"] == [
                {"video_id": "missing", "filename": "", "reason": "no longer exists"}
            ]

            for job in body["jobs"]:
                await wait_for_job(env, job["job_id"], timeout=120)

    run(scenario())


def test_trims_that_leave_no_window_are_refused_and_stored_nowhere(tmp_path):
    """The stored trim is sticky — it is replayed by every later extraction and
    read by `generate_poster` too — so a pair covering the whole duration has to
    be a 400 at the endpoint. Committed, it collapses detection to one zero-width
    window, every render fails, and the breaker reports it as a decode fault."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            async with env.Session() as db:
                duration = (await db.get(Video, video["id"])).duration_ms
            assert duration, "the fixture must have a known duration for this"

            r = await _extract(env, [video["id"]], trim_start_ms=duration, trim_end_ms=0)
            assert r.status_code == 400, r.text
            assert "clip.mp4" in r.json()["detail"]

            # Split across the two fields — the gate is on their sum.
            r = await _extract(
                env, [video["id"]],
                trim_start_ms=duration // 2 + 1, trim_end_ms=duration // 2 + 1,
            )
            assert r.status_code == 400, r.text

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                assert (row.trim_start_ms, row.trim_end_ms) == (0, 0)

            async with env.Session() as db:
                assert (await db.execute(select(BackgroundJob))).scalars().all() == []

    run(scenario())


def test_a_stored_trim_is_gated_even_when_the_body_omits_it(tmp_path):
    """The *effective* value, following the deinterlace gate's precedent: the row
    carries the trim the job will replay, so a request that omits the field is
    still governed by it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                row.trim_start_ms = row.duration_ms
                await db.commit()

            r = await _extract(env, [video["id"]])
            assert r.status_code == 400, r.text
            assert "clip.mp4" in r.json()["detail"]

    run(scenario())


def test_an_absurd_trim_is_a_422_not_an_overflowing_500(tmp_path):
    """`10**30` is a valid `ge=0` int and reaches `commit()`, where SQLite raises
    `OverflowError: Python int too large to convert to SQLite INTEGER` — an
    unhandled 500. `TRIM_MAX_MS` is the bound; both fields and both schemas carry
    it, so the probe endpoint is checked here as well."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            for field in ("trim_start_ms", "trim_end_ms"):
                r = await _extract(env, [video["id"]], **{field: 10 ** 30})
                assert r.status_code == 422, (field, r.text)
                r = await env.client.post(
                    f"{API}/videos/{video['id']}/probe", json={field: 10 ** 30}
                )
                assert r.status_code == 422, (field, r.text)

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                assert (row.trim_start_ms, row.trim_end_ms) == (0, 0)

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


def test_a_declared_but_empty_subfolder_still_blocks_the_default_name(tmp_path):
    """`_existing_subfolders` unions the *declared* names onto the occupied ones,
    and the test above cannot reach that half — it seeds every name with images.
    A subfolder a user declared and has not filled yet is theirs; an extraction
    must step past it rather than pour frames into it.

    No job needs to finish: the resolved name is in the 200 body.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/subfolders", json={"path": "clip"}
            )
            assert r.status_code == 201, r.text

            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            r = await _extract(env, [video["id"]])
            assert r.status_code == 200, r.text
            assert r.json()["jobs"][0]["subfolder"] == "clip_2"

            for job in r.json()["jobs"]:
                await wait_for_job(env, job["job_id"], timeout=120)

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


def test_the_replace_delete_is_chunked(tmp_path, monkeypatch):
    """The one unbounded `IN (...)` this file used to hold. A triage subfolder is
    exactly where an id list runs past SQLite's `SQLITE_MAX_VARIABLE_NUMBER` and
    the delete dies with `OperationalError: too many SQL variables`.

    The call is pinned rather than the crash: the limit is a compile-time option,
    and the Debian SQLite these tests run on is built with a far higher one, so no
    id list this suite could build would ever reach it. The spy returns one id per
    chunk, which also proves the caller loops rather than using `chunked` for show.
    """
    async def scenario():
        from backend.routers import videos as videos_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            await _extract_and_wait(env, [video["id"]])
            async with env.Session() as db:
                before = (await db.execute(select(Image))).scalars().all()
            old_ids = {i.id for i in before}
            old_files = [Path(i.file_path) for i in before]
            old_thumbs = [Path(i.thumbnail_path) for i in before]
            images_dir = old_files[0].parent
            thumbs_dir = old_thumbs[0].parent
            assert len(old_ids) == 3

            calls: list[list[str]] = []

            def spy(seq, size=10_000):
                items = list(seq)
                calls.append(items)
                return [[item] for item in items]

            monkeypatch.setattr(videos_router, "chunked", spy)
            _body, jobs = await _extract_and_wait(env, [video["id"]], mode="replace")
            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["replaced"] == 3

            assert calls, "the delete did not go through chunked"
            assert set(calls[0]) == old_ids

            # And a chunked delete is still a complete one: every row went, and
            # nothing accumulated on disk. Counting the directories is what proves
            # it — the replacements deliberately reuse the same filenames, since
            # the delete runs first and the uniquifier then sees them free, so the
            # old paths existing says nothing. `_001` suffixes here, or six files,
            # would mean a chunk was skipped.
            async with env.Session() as db:
                after = (await db.execute(select(Image))).scalars().all()
            assert {i.id for i in after}.isdisjoint(old_ids)
            assert sorted(p.name for p in images_dir.glob("*.jpg")) == sorted(
                p.name for p in old_files
            )
            assert sorted(p.name for p in thumbs_dir.glob("*.webp")) == sorted(
                p.name for p in old_thumbs
            )

    run(scenario())


def test_the_replace_step_holds_the_dataset_busy_flag(tmp_path, monkeypatch):
    """Extraction as a whole does *not* take the busy flag — the replace step
    does, and only that step. It deletes N rows, N files and N thumbnails, which
    is exactly the class a versioning restore must not race, and nothing else
    observes that it is held.

    Observed through `ensure_not_busy` from inside the delete loop rather than by
    reading `dataset_busy._busy`: the private dict is not the contract, the 409
    is. A real HTTP request from in here would be worse than useless — the job's
    session holds an uncommitted transaction at that point, so a second writer on
    the same SQLite file is a deadlock waiting to happen.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import dataset_busy, version_service

            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            await _extract_and_wait(env, [video["id"]])

            real = version_service.mark_image_deleted_in_versions
            seen: list[str | None] = []

            async def spy(image_id, file_path, session):
                try:
                    dataset_busy.ensure_not_busy(ds["id"])
                except Exception as exc:
                    seen.append(getattr(exc, "detail", str(exc)))
                else:
                    seen.append(None)
                return await real(image_id, file_path, session)

            monkeypatch.setattr(version_service, "mark_image_deleted_in_versions", spy)

            _body, jobs = await _extract_and_wait(env, [video["id"]], mode="replace")
            assert jobs[0]["status"] == "completed", jobs

            assert seen, "the replace step deleted nothing, so it proved nothing"
            assert all(s and "Replacing extracted frames" in s for s in seen), seen

            # And it is released again — held for the delete step, not the job.
            dataset_busy.ensure_not_busy(ds["id"])

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


def test_the_failure_breaker_counts_shots_not_frames(tmp_path, monkeypatch):
    """The breaker's sensitivity must not depend on a user-facing tuning knob.

    Counted in frames, one exception from `render_shot` added `frames_per_shot` at
    once, so any legal `frames_per_shot >= 10` tripped the ten-failure threshold on
    the very first shot and ended a twenty-minute job over one transient failure.
    Counted in shots, ten shots have to write nothing — which is what the render
    call count pins here.
    """
    async def scenario():
        from backend.routers import videos as videos_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            # More shots than the breaker's threshold and fewer than the rate
            # breaker's 20, so only the consecutive rule can be what fires.
            shots = [
                video_extract.Shot(index=i, start_ms=i * 100, end_ms=i * 100 + 50)
                for i in range(12)
            ]

            def fake_detect(*_args, **_kwargs):
                return shots, "adaptive"

            calls = {"n": 0}

            def always_fails(*_args, **_kwargs):
                calls["n"] += 1
                raise RuntimeError("this shot will not decode")

            monkeypatch.setattr(video_extract, "detect_shots", fake_detect)
            monkeypatch.setattr(video_extract, "render_shot", always_fails)

            _body, jobs = await _extract_and_wait(env, [video["id"]], frames_per_shot=10)
            assert jobs[0]["status"] == "failed", jobs
            assert "failed to decode" in (jobs[0]["error_msg"] or "")
            assert calls["n"] == videos_router.EXTRACT_MAX_CONSECUTIVE_FAILURES, calls

    run(scenario())


def test_a_zero_width_fallback_shot_writes_one_frame_not_n_identical_ones(tmp_path, monkeypatch):
    """`detect_shots`' no-duration fallback is a single zero-width window, so every
    pick inside it resolves to the same candidate position. Left alone,
    `frames_per_shot > 1` writes byte-identical files carrying identical
    `source_timestamp_ms` and `source_shot_index` — a synthetic duplicate cluster
    for the user to dedup by hand."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            def fake_detect(*_args, **_kwargs):
                return [video_extract.Shot(index=0, start_ms=0, end_ms=0)], "uniform"

            monkeypatch.setattr(video_extract, "detect_shots", fake_detect)

            _body, jobs = await _extract_and_wait(env, [video["id"]], frames_per_shot=3)
            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["written"] == 1, jobs[0]["result_data"]
            # The clamp lands before the totals are derived, so the bar is not
            # sized for two frames that will never arrive.
            assert jobs[0]["total_items"] == 1

            async with env.Session() as db:
                images = (await db.execute(select(Image))).scalars().all()
            assert len(images) == 1

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

            # The override, on a *second* video: re-targeting the first would be
            # skipped by the in-flight dedupe, and the request's `label` never
            # reach a job row.
            other = await upload_video(env, ds["id"], "other.mp4", SHOTS_MP4)
            r2 = await _extract(env, [other["id"]], label="custom")
            assert r2.status_code == 200, r2.text
            assert r2.json()["skipped"] == [], r2.text
            other_job_id = r2.json()["jobs"][0]["job_id"]

            # Both jobs are drained before the label is read: nothing rewrites
            # `label`, and a failing assertion with a job still in flight hangs
            # the harness rather than reporting red.
            await wait_for_job(env, job_id, timeout=120)
            await wait_for_job(env, other_job_id, timeout=120)
            async with env.Session() as db:
                assert (await db.get(BackgroundJob, other_job_id)).label == "custom"

    run(scenario())


# ---------------------------------------------------------------------------
# frames-summary, lineage on the image schemas, and the SSE video_id
# ---------------------------------------------------------------------------


def test_frames_summary_groups_by_subfolder_newest_extraction_first(tmp_path):
    """Rows are written directly rather than extracted: the ordering claim is
    about `MAX(created_at)` per group, and only an explicit timestamp pins it —
    three real extractions land within the same second."""
    async def scenario():
        from datetime import datetime, timedelta

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            other = await upload_video(env, ds["id"], "other.mp4", SHOTS_MP4)

            base = datetime(2026, 1, 1, 12, 0, 0)
            async with env.Session() as db:
                for subfolder, n, offset in (("clip", 2, 0), ("", 1, 60), ("clip_2", 3, 120)):
                    for i in range(n):
                        db.add(Image(
                            dataset_id=ds["id"],
                            filename=f"{subfolder or 'root'}_{i}.jpg",
                            subfolder=subfolder,
                            file_path=f"/tmp/{subfolder or 'root'}_{i}.jpg",
                            source_video_id=video["id"],
                            created_at=base + timedelta(seconds=offset + i),
                        ))
                # Another video's frames must not appear in this video's summary.
                db.add(Image(
                    dataset_id=ds["id"], filename="other_0.jpg", subfolder="other",
                    file_path="/tmp/other_0.jpg", source_video_id=other["id"],
                    created_at=base + timedelta(seconds=999),
                ))
                await db.commit()

            r = await env.client.get(f"{API}/videos/{video['id']}/frames-summary")
            assert r.status_code == 200, r.text
            body = r.json()

            assert body["total"] == 6
            # Newest extraction leads, and "" is a real group (the dataset root),
            # never folded into "no subfolder".
            assert [g["subfolder"] for g in body["groups"]] == ["clip_2", "", "clip"]
            assert [g["count"] for g in body["groups"]] == [3, 1, 2]
            assert body["groups"][0]["last_extracted_at"].startswith("2026-01-01T12:02:02")

    run(scenario())


def test_frames_summary_is_zero_for_a_video_that_has_never_been_extracted(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            r = await env.client.get(f"{API}/videos/{video['id']}/frames-summary")
            assert r.status_code == 200, r.text
            assert r.json() == {"total": 0, "groups": []}

    run(scenario())


def test_frames_summary_404s_for_an_unknown_video(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.create_dataset("d")
            r = await env.client.get(f"{API}/videos/nope/frames-summary")
            assert r.status_code == 404, r.text

    run(scenario())


def test_frames_summary_reflects_a_real_extraction(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            body, jobs = await _extract_and_wait(env, [video["id"]])
            assert [j["status"] for j in jobs] == ["completed"], jobs

            r = await env.client.get(f"{API}/videos/{video['id']}/frames-summary")
            assert r.json() == {
                "total": 3,
                "groups": [{
                    "subfolder": body["jobs"][0]["subfolder"],
                    "count": 3,
                    "last_extracted_at": r.json()["groups"][0]["last_extracted_at"],
                }],
            }
            assert r.json()["groups"][0]["last_extracted_at"].endswith("+00:00")

    run(scenario())


def test_lineage_is_visible_from_the_frame_side(tmp_path):
    """Without this a frame moved out of its subfolder can no longer say where it
    came from. The list payload carries the marker only — no timestamps, because
    it is paid per row on every gallery page."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            uploaded = await upload_image(env, ds["id"], "plain.png")

            _body, jobs = await _extract_and_wait(env, [video["id"]])
            assert [j["status"] for j in jobs] == ["completed"], jobs

            listing = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            frames = [i for i in listing if i["filename"] != "plain.png"]
            assert len(frames) == 3
            assert {i["source_video_id"] for i in frames} == {video["id"]}
            assert "source_timestamp_ms" not in frames[0]

            r = await env.client.get(f"{API}/images/{frames[0]['id']}")
            assert r.status_code == 200, r.text
            detail = r.json()
            assert detail["source_video_id"] == video["id"]
            assert detail["source_timestamp_ms"] is not None
            assert detail["source_shot_index"] is not None

            # An ordinary upload has no lineage at all, on either payload.
            assert next(i for i in listing if i["id"] == uploaded["id"])["source_video_id"] is None
            plain = (await env.client.get(f"{API}/images/{uploaded['id']}")).json()
            assert plain["source_video_id"] is None
            assert plain["source_timestamp_ms"] is None
            assert plain["source_shot_index"] is None

    run(scenario())


def test_every_progress_payload_names_its_video(tmp_path, monkeypatch):
    """A batch runs one job per video, so a frontend holding every event in one
    store cannot route a payload without this key. Asserted across the whole run
    rather than on `_emit` in isolation — the risk is a call site that forgot.

    Two runs, the second in replace mode, so the `replacing` phase is covered as
    well: it is a phase that emits, and it was the one that omitted `done`/`total`.
    """
    async def scenario():
        from backend.workers.progress import broadcaster

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            seen: list[dict] = []
            real_emit = broadcaster.emit

            async def capturing(job_id, payload):
                seen.append(payload)
                return await real_emit(job_id, payload)

            monkeypatch.setattr(broadcaster, "emit", capturing)

            _body, jobs = await _extract_and_wait(env, [video["id"]])
            assert [j["status"] for j in jobs] == ["completed"], jobs
            _body2, jobs2 = await _extract_and_wait(env, [video["id"]], mode="replace")
            assert [j["status"] for j in jobs2] == ["completed"], jobs2

            # `phase` is what distinguishes the job's own payloads from the
            # queue's generic lifecycle events (pending/running/completed), which
            # carry no video_id and are not expected to — `jobStore` merges
            # partials by job id, so the key survives onto the terminal event.
            progress = [
                p for p in seen
                if p.get("job_type") == "video_extract" and "phase" in p
            ]
            assert progress, seen
            assert all(p.get("video_id") == video["id"] for p in progress), progress
            assert {p["phase"] for p in progress} >= {"detecting", "replacing", "extracting"}

            # One meaning for the whole job: `done` counts frames a gallery
            # refetch would actually see. Detection writes none, so it must not
            # report any — and the count can never go backwards, which is what
            # `TopBar`'s per-job high-water-mark live gate depends on. Pinned as
            # an invariant rather than on the field names, because the defect
            # this guards was two phases each counting something different.
            assert not [p for p in progress if p["phase"] == "detecting" and p.get("done")], progress
            assert not [p for p in progress if p["phase"] == "replacing" and p.get("done")], progress
            # No phase may *omit* either key either. The merge is by job id, so an
            # absent `done` silently inherits whatever the client last held —
            # which, mid-replace, is the previous run's final frame count.
            assert all("done" in p and "total" in p for p in progress), progress

            by_job: dict[str, list[dict]] = {}
            for p in progress:
                by_job.setdefault(p["job_id"], []).append(p)
            assert len(by_job) == 2, by_job
            for payloads in by_job.values():
                counts = [p["done"] for p in payloads]
                assert counts == sorted(counts), payloads
            assert all(
                p["done"] <= p["total"] for p in progress if p["phase"] == "extracting"
            ), progress

    run(scenario())

def test_the_commit_interval_makes_frames_visible_before_the_job_ends(tmp_path, monkeypatch):
    """`EXTRACT_COMMIT_EVERY` never fires under any other test here — the fixture
    writes six frames and the shipped value is 25 — yet it is the whole reason
    the gallery fills live instead of staying empty for the length of a job.

    `done` is `counts["written"] - written_since_commit`, so it steps once per
    commit and not once per shot. With the interval at 1 the emitted sequence is
    [2, 4, 6]; the control half below shows the same run reports [0, 0, 0]
    unpatched, which records the constant's *purpose* rather than its plumbing.
    """
    async def capture(env, video_id, monkeypatch, *, commit_every=None):
        from backend.routers import videos as videos_router
        from backend.workers.progress import broadcaster

        seen: list[dict] = []
        real_emit = broadcaster.emit

        async def capturing(job_id, payload):
            seen.append(payload)
            return await real_emit(job_id, payload)

        monkeypatch.setattr(broadcaster, "emit", capturing)
        if commit_every is not None:
            monkeypatch.setattr(videos_router, "EXTRACT_COMMIT_EVERY", commit_every)

        _body, jobs = await _extract_and_wait(
            env, [video_id], frames_per_shot=2, mode="replace",
        )
        assert jobs[0]["status"] == "completed", jobs
        monkeypatch.undo()
        return [
            p["done"] for p in seen
            if p.get("job_type") == "video_extract" and p.get("phase") == "extracting"
        ]

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            stepped = await capture(env, video["id"], monkeypatch, commit_every=1)
            assert stepped == [2, 4, 6], stepped

            # Control: the same run with the shipped interval commits only at the
            # end, so every payload reports nothing committed yet.
            flat = await capture(env, video["id"], monkeypatch)
            assert flat == [0, 0, 0], flat

            # Either way the frames are all there once the job is done.
            async with env.Session() as db:
                assert len((await db.execute(select(Image))).scalars().all()) == 6

    run(scenario())
