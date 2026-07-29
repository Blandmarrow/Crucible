"""Pass 2 at request level: `POST /videos/reextract` and `/reextract/preview`.

Pass 2 turns curated triage frames into training data by re-seeking the
timestamp pass 1 recorded. These tests pin it from the outside: what the preview
promises, what the job actually rewrites, and what is on disk and in the object
store afterwards.

Four tests are worth more than the rest.

`test_a_pre_existing_snapshot_still_restores_the_triage_pixels` proves the
overwrite went through `protect_file_before_overwrite` *and* committed — the hook
only flushes the hash backfill, so without the commit a snapshot silently claims
"content unchanged" about the file pass 2 replaced.

`test_a_temp_file_that_will_not_reopen_leaves_the_original_intact` pins the one
ordering pass 2 does differently from upscale and LUT: write a temp, verify it,
*then* swap. Those two overwrite first and discover afterwards.

`test_an_unregistered_file_at_the_target_png_path_is_never_clobbered` is the only
real hazard in the extension change — a file with no DB row to guard it.

`test_a_thumbnail_failure_during_the_png_rename_still_serves_the_frame` carries
the symptom PM-013 was filed for: a fallible step between the swap and the commit
rolled the row back onto a file that no longer existed, so `GET
/images/{id}/file` returned **404** and the gallery card broke.
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
    mp4_shots_bytes,
    png_bytes,
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
    def fake(_path):
        return _Usage(100 * GB, 100 * GB - free_bytes, free_bytes)
    return fake


async def _triage(env, dataset_id, name="clip.mp4", **kwargs):
    """Upload a video and run pass 1 over it. Returns (video, frame rows)."""
    video = await upload_video(env, dataset_id, name, SHOTS_MP4)
    r = await env.client.post(
        f"{API}/videos/extract", json={"video_ids": [video["id"]], **kwargs}
    )
    assert r.status_code == 200, r.text
    job = await wait_for_job(env, r.json()["jobs"][0]["job_id"], timeout=120)
    assert job["status"] == "completed", job
    async with env.Session() as db:
        frames = (await db.execute(
            select(Image).where(Image.source_video_id == video["id"]).order_by(Image.filename)
        )).scalars().all()
    assert frames, "pass 1 wrote nothing"
    return video, frames


async def _preview(env, **body):
    return await env.client.post(f"{API}/videos/reextract/preview", json=body)


async def _reextract(env, **body):
    return await env.client.post(f"{API}/videos/reextract", json=body)


async def _reextract_and_wait(env, **body):
    r = await _reextract(env, **body)
    assert r.status_code == 200, r.text
    payload = r.json()
    jobs = [await wait_for_job(env, g["job_id"], timeout=180) for g in payload["groups"]]
    return payload, jobs


def _reasons(payload) -> dict[str, int]:
    out: dict[str, int] = {}
    for entry in payload["skipped"]:
        out[entry["reason"]] = out.get(entry["reason"], 0) + 1
    return out


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_the_reextract_routes_are_not_shadowed_by_the_video_id_route(tmp_path):
    """Both are literal segments under `/videos/`. FastAPI matches in declaration
    order, and a 404 "Video not found" here would mean `/{video_id}` swallowed
    them — the failure `GET /videos/capabilities` already has a test for."""
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.create_dataset("d")
            for path in ("/videos/reextract", "/videos/reextract/preview"):
                r = await env.client.post(f"{API}{path}", json={"image_ids": ["nope"]})
                assert r.status_code == 200, (path, r.text)
                assert r.json()["skipped"][0]["reason"] == "no longer exists"

    run(scenario())


def test_a_request_must_name_exactly_one_scope(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.create_dataset("d")
            assert (await _preview(env)).status_code == 422
            r = await _preview(env, image_ids=["a"], video_id="b")
            assert r.status_code == 422

    run(scenario())


# ---------------------------------------------------------------------------
# Preview accounting
# ---------------------------------------------------------------------------


def test_preview_names_every_skip_reason_and_agrees_with_the_enqueue(tmp_path):
    """One resolver behind both endpoints — the whole reason the preview exists
    is that its accounting cannot drift from the job's."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"])
            plain = await upload_image(env, ds["id"], "hand.png")

            async with env.Session() as db:
                # A frame already rewritten in place by something else.
                edited = await db.get(Image, frames[0].id)
                edited.processing_history = [{"op": "upscale", "at": "2026-01-01T00:00:00"}]
                # A frame whose timestamp never survived.
                stampless = await db.get(Image, frames[1].id)
                stampless.source_timestamp_ms = None
                await db.commit()

            ids = [f.id for f in frames] + [plain["id"], "ghost"]
            preview = (await _preview(env, image_ids=ids)).json()

            assert preview["total"] == len(ids)
            assert preview["eligible"] == len(frames) - 2
            assert [g["video_id"] for g in preview["groups"]] == [video["id"]]
            assert preview["groups"][0]["job_id"] is None
            assert _reasons(preview) == {
                "already edited in place": 1,
                "no recorded timestamp": 1,
                "not extracted from a video": 1,
                "no longer exists": 1,
            }

            enqueued, jobs = await _reextract_and_wait(env, image_ids=ids)
            assert [j["status"] for j in jobs] == ["completed"], jobs
            assert enqueued["eligible"] == preview["eligible"]
            assert enqueued["total"] == preview["total"]
            assert _reasons(enqueued) == _reasons(preview)
            assert enqueued["groups"][0]["job_id"]

    run(scenario())


def test_an_unknown_video_id_is_a_404_on_both_endpoints(tmp_path):
    """An empty result means "this video has no eligible frames" — a real answer
    the modal renders. Returning it for a video that does not exist makes the two
    indistinguishable. Both endpoints, because they share one resolver."""
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.create_dataset("d")
            for call in (_preview, _reextract):
                r = await call(env, video_id="nope")
                assert r.status_code == 404, r.text

    run(scenario())


def test_the_video_scope_refuses_more_frames_than_one_run_may_cover(tmp_path, monkeypatch):
    """The `image_ids` scope is bounded by the schema's `max_length`; this one
    resolves its ids from the DB and needs the same ceiling applied server-side.
    It bounds the `BackgroundJob.config` id blob, the job's `select(Image)` entity
    load and the preview's `skipped` array at once.

    The cap is monkeypatched rather than approached with 5001 real frames — that
    would be a minutes-long test of pass 1, not of this bound."""
    async def scenario():
        from backend.routers import videos as videos_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            assert len(frames) == 3

            monkeypatch.setattr(videos_router, "REEXTRACT_MAX_FRAMES", 2)
            for call in (_preview, _reextract):
                r = await call(env, video_id=video["id"])
                assert r.status_code == 400, r.text
                assert "3 frames" in r.json()["detail"]
                assert "subfolder" in r.json()["detail"]

            async with env.Session() as db:
                jobs = (await db.execute(
                    select(BackgroundJob).where(BackgroundJob.job_type == "video_reextract")
                )).scalars().all()
            assert jobs == []

            # And the narrower scope the message points at still works.
            r = await env.client.post(
                f"{API}/images/batch/move-subfolder",
                json={"image_ids": [frames[0].id], "subfolder": "narrow"},
            )
            assert r.status_code == 200, r.text
            r = await _preview(env, video_id=video["id"], subfolder="narrow")
            assert r.status_code == 200, r.text
            assert r.json()["eligible"] == 1

    run(scenario())


def test_a_duplicated_image_id_is_counted_once(tmp_path):
    """The `IN` query collapses duplicates on its own, so an id sent twice used to
    report `eligible=2` against one row: a phantom entry in the job config, a
    phantom skip, and a progress bar that topped out at 1 / 2."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            _video, frames = await _triage(env, ds["id"], long_edge=160)
            target = frames[0]

            preview = (await _preview(env, image_ids=[target.id, target.id])).json()
            assert preview["eligible"] == 1
            assert preview["total"] == 1
            assert preview["skipped"] == []
            assert [g["frames"] for g in preview["groups"]] == [1]

            payload, jobs = await _reextract_and_wait(
                env, image_ids=[target.id, target.id]
            )
            assert [j["status"] for j in jobs] == ["completed"], jobs
            assert payload["eligible"] == 1
            assert payload["total"] == 1
            assert jobs[0]["total_items"] == 1
            assert jobs[0]["result_data"]["rewritten"] == 1
            assert jobs[0]["result_data"]["skipped"] == 0

    run(scenario())


def test_a_missing_source_video_file_is_reported_not_attempted(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"])
            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                Path(row.file_path).unlink()

            preview = (await _preview(env, video_id=video["id"])).json()
            assert preview["eligible"] == 0
            assert _reasons(preview) == {"source video file is missing": len(frames)}

    run(scenario())


def test_the_video_scope_can_be_narrowed_to_one_subfolder(tmp_path):
    """The scope the extraction-history rows have to hand — they know a video and
    a subfolder, never a list of ids."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], subfolder="first")

            r = await env.client.post(
                f"{API}/images/batch/move-subfolder",
                json={"image_ids": [frames[0].id], "subfolder": "second"},
            )
            assert r.status_code == 200, r.text

            whole = (await _preview(env, video_id=video["id"])).json()
            assert whole["eligible"] == len(frames)

            narrowed = (await _preview(env, video_id=video["id"], subfolder="second")).json()
            assert narrowed["eligible"] == 1

    run(scenario())


# ---------------------------------------------------------------------------
# The rewrite
# ---------------------------------------------------------------------------


def test_frames_grow_to_native_resolution_in_place(tmp_path):
    """Pass 1 wrote 1024px triage frames (here the fixture is 320px wide, so it
    is capped at 160 to make "native" mean something). The row keeps its id, its
    filename and its lineage; only the pixels and the metadata change."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            assert {f.width for f in frames} == {160}
            before = {f.id: (f.filename, f.file_path, f.phash) for f in frames}
            # pHash is scale-invariant by design, so the same frame at 160px and
            # at 320px usually hashes the same — which makes "the value changed"
            # useless as proof that it was recomputed. Poison it instead.
            async with env.Session() as db:
                for f in frames:
                    (await db.get(Image, f.id)).phash = "0" * 16
                await db.commit()

            payload, jobs = await _reextract_and_wait(env, video_id=video["id"])
            assert [j["status"] for j in jobs] == ["completed"], jobs
            assert jobs[0]["result_data"]["rewritten"] == len(frames)
            assert jobs[0]["result_data"]["failed"] == 0
            assert jobs[0]["result_data"]["video_id"] == video["id"]
            assert "scores" in jobs[0]["result_data"]["note"].lower()
            assert jobs[0]["total_items"] == len(frames)
            assert video["filename"].removesuffix(".mp4") in jobs[0]["label"]
            assert str(len(frames)) in jobs[0]["label"]

            async with env.Session() as db:
                after = (await db.execute(
                    select(Image).where(Image.source_video_id == video["id"])
                )).scalars().all()
            assert {i.id for i in after} == set(before)
            for img in after:
                filename, file_path, phash = before[img.id]
                assert img.filename == filename
                assert img.file_path == file_path
                assert (img.width, img.height) == (320, 240)
                assert img.phash == phash, "phash must be re-derived — dedup depends on it"
                assert Path(img.file_path).exists()
                assert Path(img.thumbnail_path).exists()
                assert img.source_timestamp_ms is not None

    run(scenario())


def test_a_long_edge_cap_bounds_the_output(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, _frames = await _triage(env, ds["id"], long_edge=160)

            _payload, jobs = await _reextract_and_wait(
                env, video_id=video["id"], max_long_edge=200
            )
            assert [j["status"] for j in jobs] == ["completed"], jobs

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            assert {(i.width, i.height) for i in rows} == {(200, 150)}

    run(scenario())


def test_quality_scores_survive_the_rewrite(tmp_path):
    """Matching `batch_upscale`/`batch_lut` replace mode. The job says so in
    `result_data["note"]` rather than silently keeping stale numbers."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            async with env.Session() as db:
                for f in frames:
                    row = await db.get(Image, f.id)
                    row.aesthetic_score = 4.25
                    row.caption_text = "a frame"
                await db.commit()

            _payload, jobs = await _reextract_and_wait(env, video_id=video["id"])
            assert [j["status"] for j in jobs] == ["completed"], jobs

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            assert {r.aesthetic_score for r in rows} == {4.25}
            assert {r.caption_text for r in rows} == {"a frame"}

    run(scenario())


# ---------------------------------------------------------------------------
# processing_history — the skip rule, and pass 2 not skipping itself
# ---------------------------------------------------------------------------


def test_a_second_run_is_not_self_skipped(tmp_path):
    """The `reextract` exclusion in `_edited_in_place` is load-bearing: without
    it pass 2 refuses to run twice, because its own history entry looks like
    third-party editing."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)

            _p1, jobs = await _reextract_and_wait(env, video_id=video["id"])
            assert [j["status"] for j in jobs] == ["completed"], jobs

            async with env.Session() as db:
                row = await db.get(Image, frames[0].id)
                assert [e["op"] for e in row.processing_history] == ["reextract"]
                assert row.processing_history[0]["video_id"] == video["id"]
                assert row.processing_history[0]["format"] == "jpeg"

            preview = (await _preview(env, video_id=video["id"])).json()
            assert preview["eligible"] == len(frames), preview

            _p2, jobs = await _reextract_and_wait(env, video_id=video["id"], format="png")
            assert [j["status"] for j in jobs] == ["completed"], jobs
            async with env.Session() as db:
                row = (await db.execute(
                    select(Image).where(Image.source_video_id == video["id"])
                )).scalars().first()
                assert [e["op"] for e in row.processing_history] == ["reextract", "reextract"]

    run(scenario())


def test_every_in_place_overwrite_marks_the_frame_as_edited(tmp_path):
    """The guard is only as good as what writes `processing_history`.

    Upscale, LUT and detection-crop always recorded an entry; the crop and resize
    paths in `routers/images.py` did not, so a frame cropped in place stayed
    silently eligible and pass 2 would have discarded the crop. Asserted through
    the *preview*, since that is what decides.

    `POST /images/batch/crop` and `/batch/resize` record one too, but cannot be
    driven from here: `POST /images/{image_id}/crop` is declared first and reads
    `batch` as an image id, so both have been unreachable since they shipped
    (nothing in the frontend calls them either). Left alone here — un-shadowing a
    dead endpoint is not this feature's change to make.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            assert len(frames) >= 2, "the fixture must produce enough frames for this"
            cropped, resized = frames[0], frames[1]

            r = await env.client.post(f"{API}/images/{cropped.id}/crop", json={
                "x": 0, "y": 0, "width": 80, "height": 60, "replace": True,
            })
            assert r.status_code == 200, r.text
            r = await env.client.post(f"{API}/images/{resized.id}/resize", json={"scale": 0.5})
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                for row_id, op in ((cropped.id, "crop"), (resized.id, "resize")):
                    row = await db.get(Image, row_id)
                    assert [e["op"] for e in (row.processing_history or [])] == [op], row_id

            preview = (await _preview(env, video_id=video["id"])).json()
            assert preview["eligible"] == len(frames) - 2
            assert _reasons(preview) == {"already edited in place": 2}

    run(scenario())


def test_a_frame_edited_in_place_by_upscale_is_skipped(tmp_path):
    """`backend/models/image.py` states this rule: a replace-mode upscale keeps a
    frame's lineage while the pixels stop being the extracted frame, so
    re-extracting it would silently discard the edit."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            async with env.Session() as db:
                row = await db.get(Image, frames[0].id)
                row.processing_history = [{"op": "crop_to_detection", "at": "2026-01-01T00:00:00"}]
                await db.commit()

            preview = (await _preview(env, video_id=video["id"])).json()
            assert preview["eligible"] == len(frames) - 1
            assert _reasons(preview) == {"already edited in place": 1}
            assert preview["skipped"][0]["image_id"] == frames[0].id

    run(scenario())


# ---------------------------------------------------------------------------
# The extension change
# ---------------------------------------------------------------------------


def test_png_output_renames_the_file_and_moves_nothing_else(tmp_path):
    """A *pure* extension change. The stem never moves, so the thumbnail
    (`{stem}.webp`) and the caption sidecar (`{stem}.txt`) both stay exactly
    where they are — and the old `.jpg` must not be left behind."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            target = frames[0]
            old_path = Path(target.file_path)
            thumb = Path(target.thumbnail_path)
            sidecar = old_path.with_suffix(".txt")
            sidecar.write_text("kept", encoding="utf-8")

            _payload, jobs = await _reextract_and_wait(
                env, image_ids=[target.id], format="png"
            )
            assert [j["status"] for j in jobs] == ["completed"], jobs

            async with env.Session() as db:
                row = await db.get(Image, target.id)
            assert row.filename == old_path.with_suffix(".png").name
            assert row.file_path == str(old_path.with_suffix(".png"))
            assert row.format == "PNG"
            assert Path(row.file_path).exists()
            assert not old_path.exists(), "the .jpg was orphaned"
            # Neither derived artifact is keyed on the extension.
            assert row.thumbnail_path == str(thumb)
            assert thumb.exists()
            assert sidecar.exists() and sidecar.read_text(encoding="utf-8") == "kept"

            # And back again — the name round-trips.
            _payload, jobs = await _reextract_and_wait(env, image_ids=[target.id])
            assert [j["status"] for j in jobs] == ["completed"], jobs
            async with env.Session() as db:
                row = await db.get(Image, target.id)
            assert row.file_path == str(old_path)
            assert not old_path.with_suffix(".png").exists()

    run(scenario())


def test_an_unregistered_file_at_the_target_png_path_is_never_clobbered(tmp_path):
    """The only real hazard in the extension swap: a file hand-dropped into
    `images/` and not yet rescanned has no DB row guarding it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            _video, frames = await _triage(env, ds["id"], long_edge=160)
            target = frames[0]
            squatter = Path(target.file_path).with_suffix(".png")
            squatter.write_bytes(png_bytes(size=(7, 7)))

            _payload, jobs = await _reextract_and_wait(
                env, image_ids=[target.id], format="png"
            )
            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["rewritten"] == 0
            assert jobs[0]["result_data"]["failed"] == 1

            assert squatter.read_bytes() == png_bytes(size=(7, 7))
            async with env.Session() as db:
                row = await db.get(Image, target.id)
            assert row.file_path == target.file_path
            assert Path(row.file_path).exists()
            assert row.width == 160, "the original row was modified anyway"

    run(scenario())


def test_a_run_whose_every_frame_is_refused_still_completes(tmp_path, monkeypatch):
    """A name collision is not a decode fault. It counts as `failed` — the user
    asked for that frame and did not get it — but it must not feed the
    consecutive-failure breaker, or a directory full of squatters aborts the run
    instead of reporting each frame."""
    async def scenario():
        from backend.routers import videos as videos_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            monkeypatch.setattr(videos_router, "EXTRACT_MAX_CONSECUTIVE_FAILURES", 1)

            squatters = {}
            for f in frames:
                path = Path(f.file_path).with_suffix(".png")
                path.write_bytes(png_bytes(size=(7, 7)))
                squatters[f.id] = path

            _payload, jobs = await _reextract_and_wait(
                env, video_id=video["id"], format="png"
            )
            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["rewritten"] == 0
            assert jobs[0]["result_data"]["failed"] == len(frames)

            for path in squatters.values():
                assert path.read_bytes() == png_bytes(size=(7, 7))
            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            for img in rows:
                assert img.width == 160, "an original row was modified anyway"
            images_dir = Path(rows[0].file_path).parent
            assert not list(images_dir.glob("*.tmp*")), "a temp file was left behind"

    run(scenario())


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_a_temp_file_that_will_not_reopen_leaves_the_original_intact(tmp_path):
    """The ordering that distinguishes pass 2 from upscale and LUT: write a temp,
    verify it, only then swap. Those two overwrite first and discover afterwards,
    so the same failure destroys the source."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            before = {f.id: Path(f.file_path).read_bytes() for f in frames}

            from backend.routers import videos as videos_router

            monkey = videos_router.get_image_info
            videos_router.get_image_info = lambda _path: {}
            try:
                _payload, jobs = await _reextract_and_wait(env, video_id=video["id"])
            finally:
                videos_router.get_image_info = monkey

            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["rewritten"] == 0
            assert jobs[0]["result_data"]["failed"] == len(frames)

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            for img in rows:
                assert Path(img.file_path).read_bytes() == before[img.id]
                assert img.width == 160
            images_dir = Path(rows[0].file_path).parent
            assert not list(images_dir.glob("*.tmp*")), "a temp file was left behind"

    run(scenario())


def test_a_raise_between_the_render_and_the_swap_leaves_no_temp_behind(tmp_path, monkeypatch):
    """The temp carries a real image extension and sits in `images/`, where
    `rescan_dataset` would adopt it as a new image. Handled returns unlink it; so
    must an exception on the way to `os.replace`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            before = {f.id: Path(f.file_path).read_bytes() for f in frames}

            async def boom(*_args, **_kwargs):
                raise RuntimeError("the object store fell over")

            from backend.services import version_service

            monkeypatch.setattr(
                version_service, "protect_file_before_overwrite", boom
            )
            _payload, jobs = await _reextract_and_wait(env, video_id=video["id"])
            assert jobs[0]["status"] == "failed", jobs

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            for img in rows:
                assert Path(img.file_path).read_bytes() == before[img.id]
            images_dir = Path(rows[0].file_path).parent
            assert not list(images_dir.glob("*.tmp*")), "a temp file was left behind"

    run(scenario())


def _break_thumbnails(monkeypatch):
    """Make `generate_thumbnail` deterministically fail.

    Patched on the *router module attribute*: `videos.py` imports the name at
    module import, so patching `image_service` would not be seen. A plain sync
    `def`, because the call goes through `run_in_executor`.
    """
    from backend.routers import videos as videos_router

    def boom(_src, _dest):
        raise OSError("no space left on device")

    monkeypatch.setattr(videos_router, "generate_thumbnail", boom)


def test_a_thumbnail_that_will_not_regenerate_still_commits_the_rewrite(tmp_path, monkeypatch):
    """The thumbnail is an epilogue, not a step. `generate_thumbnail` catches
    nothing, so before PM-013 a full disk raised straight out of the job — the
    session closed without committing and the row kept the triage geometry while
    the file on disk was already the full-res one.

    The frame counts as `rewritten`: it was written and committed. Counting it
    failed would both lie and feed the consecutive-failure breaker."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            thumbs = {f.id: Path(f.thumbnail_path) for f in frames}
            stale = {i: p.read_bytes() for i, p in thumbs.items()}
            async with env.Session() as db:
                for f in frames:
                    (await db.get(Image, f.id)).phash = "0" * 16
                await db.commit()

            _break_thumbnails(monkeypatch)
            _payload, jobs = await _reextract_and_wait(env, video_id=video["id"])

            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["rewritten"] == len(frames)
            assert jobs[0]["result_data"]["failed"] == 0
            assert jobs[0]["result_data"]["thumbnails_stale"] == len(frames)

            async with env.Session() as db:
                rows = (await db.execute(
                    select(Image).where(Image.source_video_id == video["id"])
                )).scalars().all()
            assert len(rows) == len(frames)
            for img in rows:
                assert (img.width, img.height) == (320, 240), "the row was rolled back"
                assert img.phash != "0" * 16, "phash must be re-derived"
                assert [e["op"] for e in img.processing_history] == ["reextract"], \
                    "the skip guard for the next run was lost with the row"
                assert Path(img.file_path).exists()
                # The old thumbnail is left exactly as it was — stale, but never
                # deleted, so the gallery card keeps rendering something.
                assert thumbs[img.id].exists()
                assert thumbs[img.id].read_bytes() == stale[img.id]
            images_dir = Path(rows[0].file_path).parent
            assert not list(images_dir.glob("*.tmp*")), "a temp file was left behind"

    run(scenario())


def test_a_thumbnail_failure_during_the_png_rename_still_serves_the_frame(tmp_path, monkeypatch):
    """The 404 that opened PM-013. With the extension change the swap renames the
    file, so a raise before the commit left the row naming a `.jpg` that no longer
    existed: `GET /images/{id}/file` 404s, the gallery card breaks, and a later
    `rescan_dataset` adopts the orphaned `.png` as a second unrelated image."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            target = frames[0]
            jpg_path = Path(target.file_path)

            _break_thumbnails(monkeypatch)
            _payload, jobs = await _reextract_and_wait(
                env, image_ids=[target.id], format="png"
            )

            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["rewritten"] == 1
            assert jobs[0]["result_data"]["thumbnails_stale"] == 1

            async with env.Session() as db:
                row = await db.get(Image, target.id)
            png_path = jpg_path.with_suffix(".png")
            assert row.filename == png_path.name
            assert row.file_path == str(png_path)
            assert png_path.exists()
            assert not jpg_path.exists(), "the superseded .jpg was left behind"

            r = await env.client.get(f"{API}/images/{target.id}/file")
            assert r.status_code == 200, r.text

            # Exactly one file per stem: nothing for a later rescan to adopt.
            images_dir = jpg_path.parent
            for stem in {p.stem for p in images_dir.glob("*") if p.is_file()}:
                assert len(list(images_dir.glob(f"{stem}.*"))) == 1, stem

    run(scenario())


def test_a_commit_that_fails_after_the_swap_leaves_the_original_in_place(tmp_path, monkeypatch):
    """The one irreducible window, and why the superseded original is unlinked
    *after* the commit rather than merely before the thumbnail.

    A failing `commit()` cannot be ordered away. Unlinking after it makes that
    window survivable: the row still names the `.jpg`, the `.jpg` still exists,
    and `/file` still 200s. The residue is an orphan `.png` — the same
    rescan-adoptable leftover a `SIGKILL` already leaves, not a broken row."""
    async def scenario():
        import os

        from sqlalchemy.ext.asyncio import AsyncSession

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            _video, frames = await _triage(env, ds["id"], long_edge=160)
            target = frames[0]
            jpg_path = Path(target.file_path)

            state = {"swapped": False, "raised": False}
            real_replace = os.replace
            real_commit = AsyncSession.commit

            def spy_replace(src, dst, **kwargs):
                out = real_replace(src, dst, **kwargs)
                if Path(dst) == jpg_path.with_suffix(".png"):
                    state["swapped"] = True
                return out

            async def flaky_commit(self):
                if state["swapped"] and not state["raised"]:
                    state["raised"] = True
                    raise RuntimeError("the transaction log went away")
                return await real_commit(self)

            monkeypatch.setattr(os, "replace", spy_replace)
            monkeypatch.setattr(AsyncSession, "commit", flaky_commit)
            _payload, jobs = await _reextract_and_wait(
                env, image_ids=[target.id], format="png"
            )
            monkeypatch.undo()

            assert state["raised"], "the commit under test never ran"
            assert jobs[0]["status"] == "failed", jobs

            async with env.Session() as db:
                row = await db.get(Image, target.id)
            assert row.file_path == str(jpg_path), "the row followed a rolled-back swap"
            assert jpg_path.exists(), "the original was unlinked before the commit"

            r = await env.client.get(f"{API}/images/{target.id}/file")
            assert r.status_code == 200, r.text
            # The orphan .png is deliberately *not* asserted away: it is the
            # accepted residue, adoptable by a rescan.

    run(scenario())


def test_a_full_disk_is_a_507_before_any_job_exists(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, _frames = await _triage(env, ds["id"], long_edge=160)

            monkeypatch.setattr(shutil, "disk_usage", _usage(DISK_FLOOR_BYTES // 4))
            r = await _reextract(env, video_id=video["id"])
            assert r.status_code == 507, r.text

            async with env.Session() as db:
                jobs = (await db.execute(
                    select(BackgroundJob).where(BackgroundJob.job_type == "video_reextract")
                )).scalars().all()
            assert jobs == []

    run(scenario())


def test_a_disk_that_fills_mid_rewrite_fails_the_job(tmp_path, monkeypatch):
    """Pass 1's twin (`test_a_disk_that_fills_mid_run_…`), against the one
    structural difference: pass 2 has no commit interval, only the disk recheck.
    Same harness — gate the router's own `require_free_space` on "has any frame
    been rendered yet", so both pre-loop preflights pass and the in-loop check is
    the first call that raises."""
    import threading

    async def scenario():
        from backend.routers import videos as videos_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)
            assert len(frames) > 1, "a single frame cannot show a mid-run failure"

            rendered = threading.Event()
            real_render = video_extract.render_at_timestamps
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

            monkeypatch.setattr(video_extract, "render_at_timestamps", render_spy)
            monkeypatch.setattr(videos_router, "require_free_space", require_spy)
            monkeypatch.setattr(videos_router, "EXTRACT_DISK_RECHECK_EVERY", 1)

            r = await _reextract(env, video_id=video["id"])
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["groups"][0]["job_id"], timeout=180)

            assert job["status"] == "failed", job
            assert "disk space" in (job["error_msg"] or "")

            # What was rewritten stays, at its new size, with its file intact —
            # and nothing was left half-swapped.
            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            grown = [i for i in rows if i.width and i.width > 160]
            assert grown, "the check fired before anything was rewritten"
            for img in rows:
                assert Path(img.file_path).exists()
            images_dir = Path(rows[0].file_path).parent
            assert not list(images_dir.glob("*.tmp*")), "a temp file was left behind"

    run(scenario())


def test_reextract_409s_while_the_dataset_is_busy(tmp_path):
    """Ordering trap: the guard sits *after* `_resolve_reextract_targets` and
    after the `if not groups: return` early exit, so a dataset with nothing
    extracted gets a 200 with an empty result and would pass for the wrong
    reason. Hence a real pass 1 first."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import dataset_busy

            ds = await env.create_dataset("d")
            video, _frames = await _triage(env, ds["id"], long_edge=160)

            with dataset_busy.busy(ds["id"], "versioning"):
                r = await _reextract(env, video_id=video["id"])
            assert r.status_code == 409, r.text

            async with env.Session() as db:
                jobs = (await db.execute(
                    select(BackgroundJob).where(BackgroundJob.job_type == "video_reextract")
                )).scalars().all()
            assert jobs == []

    run(scenario())


def test_a_reextract_with_nothing_to_do_does_not_409(tmp_path):
    """The twin of the test above, recording that ordering deliberately: the
    empty-result early exit is reached before the busy guard, so a request that
    would rewrite nothing is a 200 even mid-restore. It writes nothing either."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import dataset_busy

            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)

            with dataset_busy.busy(ds["id"], "versioning"):
                r = await _reextract(env, video_id=video["id"])
            assert r.status_code == 200, r.text
            assert r.json()["groups"] == []

    run(scenario())


def test_a_stored_deinterlace_without_ffmpeg_is_a_503(tmp_path, monkeypatch):
    """The gate tests the *stored* filter, which is what the job replays — there
    is no request field to override it. Without this the job dies inside every
    frame and reports a missing package as a decode fault."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, _frames = await _triage(env, ds["id"], long_edge=160)
            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                row.deinterlace = "bwdif"
                await db.commit()

            monkeypatch.setattr(
                video_extract, "capabilities",
                lambda: {"shot_detection": True, "deinterlace": False,
                         "scenedetect_version": None, "ffmpeg_version": None},
            )
            r = await _reextract(env, video_id=video["id"])
            assert r.status_code == 503, r.text
            assert "imageio-ffmpeg" in r.json()["detail"]

    run(scenario())


def test_cancelling_keeps_the_frames_already_rewritten(tmp_path, monkeypatch):
    """Made deterministic — the first frame's render blocks until the cancel has
    been posted. Files already swapped in are real and their COW backups exist,
    so they stay, and `refresh_stats` still runs because the runner is wrapped."""
    import threading

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)

            rendering = threading.Event()
            resume = threading.Event()
            real_render = video_extract.render_at_timestamps

            def blocking_render(*args, **kwargs):
                out = real_render(*args, **kwargs)
                if not rendering.is_set():
                    rendering.set()
                    resume.wait(60)
                return out

            monkeypatch.setattr(video_extract, "render_at_timestamps", blocking_render)

            r = await _reextract(env, video_id=video["id"])
            job_id = r.json()["groups"][0]["job_id"]
            for _ in range(600):
                if rendering.is_set():
                    break
                await asyncio.sleep(0.05)
            assert rendering.is_set(), "the first frame never rendered"
            cancel = await env.client.delete(f"{API}/jobs/{job_id}")
            assert cancel.status_code == 204, cancel.text
            resume.set()

            job = await wait_for_job(env, job_id, timeout=180)
            assert job["status"] == "cancelled", job

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            grown = [i for i in rows if i.width == 320]
            assert grown, "the frame that had already been rewritten was not kept"
            for img in rows:
                assert Path(img.file_path).exists()
            assert len(grown) < len(frames), "nothing was actually cancelled"

    run(scenario())


# ---------------------------------------------------------------------------
# In-flight dedupe, both directions
# ---------------------------------------------------------------------------


def test_a_video_already_extracting_is_skipped_by_a_reextract(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)

            async with env.Session() as db:
                db.add(BackgroundJob(
                    job_type="video_extract", status="running", dataset_id=ds["id"],
                    config={"video_id": video["id"]},
                ))
                await db.commit()

            preview = (await _preview(env, video_id=video["id"])).json()
            assert preview["eligible"] == 0
            assert _reasons(preview) == {
                "source video is already being extracted": len(frames)
            }

            enqueued = (await _reextract(env, video_id=video["id"])).json()
            assert enqueued["groups"] == []

    run(scenario())


def test_a_video_being_reextracted_is_skipped_by_an_extract(tmp_path):
    """The other direction, and the one that actually destroys data if missed: a
    pass-1 `replace` deletes the very rows pass 2 is rewriting."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, _frames = await _triage(env, ds["id"], long_edge=160)

            async with env.Session() as db:
                db.add(BackgroundJob(
                    job_type="video_reextract", status="running", dataset_id=ds["id"],
                    config={"video_id": video["id"]},
                ))
                await db.commit()

            r = await env.client.post(
                f"{API}/videos/extract", json={"video_ids": [video["id"]], "mode": "replace"}
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["jobs"] == []
            assert body["skipped"][0]["video_id"] == video["id"]

    run(scenario())


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------


def test_a_pre_existing_snapshot_still_restores_the_triage_pixels(tmp_path):
    """The COW hook fires and — just as importantly — the caller commits right
    after it. The hook only *flushes* the hash backfill, so without the commit a
    crash during the swap would leave the snapshot claiming "content unchanged"
    about the file pass 2 replaced."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)

            r = await env.client.patch(
                f"{API}/settings/thresholds", json={"versioning_mode": "auto"}
            )
            assert r.status_code == 200, r.text
            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/versions", json={"name": "triage"}
            )
            assert r.status_code in (200, 201, 202), r.text
            payload = r.json()
            if "job_id" in payload:
                await wait_for_job(env, payload["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{ds['id']}/versions")).json()[0]["id"]

            _p, jobs = await _reextract_and_wait(env, video_id=video["id"])
            assert [j["status"] for j in jobs] == ["completed"], jobs
            async with env.Session() as db:
                grown = (await db.execute(select(Image))).scalars().all()
            assert {i.width for i in grown} == {320}

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/versions/{version_id}/restore",
                json={"handle_extra_images": "remove"},
            )
            assert r.status_code in (200, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                restored = (await db.execute(select(Image))).scalars().all()
            assert {i.filename for i in restored} == {f.filename for f in frames}
            for img in restored:
                assert Path(img.file_path).exists()
            from PIL import Image as PilImage
            for img in restored:
                with PilImage.open(img.file_path) as opened:
                    assert max(opened.size) == 160, "the triage pixels did not come back"

    run(scenario())


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def test_progress_payloads_name_their_video_and_their_frame(tmp_path, monkeypatch):
    """`done` means one thing for the whole job — frames a gallery refetch would
    actually see — so it must be the committed count and it can never go
    backwards, which is what `TopBar`'s per-job high-water-mark gate depends on."""
    async def scenario():
        from backend.workers.progress import broadcaster

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"], long_edge=160)

            seen: list[dict] = []
            real_emit = broadcaster.emit

            async def capturing(job_id, payload):
                seen.append(payload)
                return await real_emit(job_id, payload)

            monkeypatch.setattr(broadcaster, "emit", capturing)
            _p, jobs = await _reextract_and_wait(env, video_id=video["id"])
            assert [j["status"] for j in jobs] == ["completed"], jobs

            progress = [
                p for p in seen if p.get("job_type") == "video_reextract" and "phase" in p
            ]
            assert len(progress) == len(frames), progress
            assert all(p["video_id"] == video["id"] for p in progress), progress
            assert all(p.get("image_id") for p in progress), progress
            counts = [p["done"] for p in progress]
            assert counts == sorted(counts), progress
            assert all(p["done"] <= p["total"] == len(frames) for p in progress), progress
            assert progress[-1]["percent"] == 100.0
            # The last frame is announced too. `_rewrite` has to commit mid-frame
            # for the COW hook, so a batching threshold would never be reached and
            # the bar used to top out at N-1 / N until the terminal event.
            assert progress[-1]["done"] == len(frames), progress

    run(scenario())
