"""A locked file is reported, not crashed into — delete, rename and re-extract.

The bug this pins is Windows-only and invisible on POSIX, which is why it needs
a monkeypatch to exist here at all. Windows refuses to unlink or rename a file
another handle has open unless that handle asked for FILE_SHARE_DELETE, which
Python's `open()` does not — and the handle is usually *Crucible's own*:
Starlette's `FileResponse` keeps the file open for the whole body send, and a
browser under `preload="metadata"` holds the range request open so it can resume
a seek. POSIX simply unlinks the directory entry, so no CI run and no dev
container can reach the failing branch. See
docs/dev/postmortems/PM-021-served-file-handle-blocked-windows-delete.md.

`winerror` is set explicitly and `errno` is left at EPERM (which the helper does
*not* treat as locked), so these tests exercise the Windows branch of
`utils._is_locked_error` rather than the POSIX one that would fire anyway.

The last group covers the third converted site, which is not a route: pass 2's
in-place frame overwrite. There a lock is not a 409 — the job cannot answer a
status code — so the frame is counted `failed` and the run carries on, and the
thing worth pinning is that the original survives untouched.
"""

import pathlib
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.models import Image, Video
from backend.services import video_extract
from backend.tests.conftest import (
    API,
    api_env,
    mp4_shots_bytes,
    run,
    upload_video,
    wait_for_job,
)
from backend.utils import _LOCK_RETRY_DELAYS

pytest.importorskip("cv2", reason="opencv is not installed")

# `attempts=5` is a tuning constant, not a contract — asserting the literal
# breaks a harmless change to it. This is the same number, derived.
LOCK_ATTEMPTS = len(_LOCK_RETRY_DELAYS) + 1

needs_shots = pytest.mark.skipif(
    not video_extract.capabilities()["shot_detection"],
    reason="scenedetect is not installed",
)


def _sharing_violation() -> PermissionError:
    """The exact shape of WinError 32 as Python raises it."""
    err = PermissionError(1, "The process cannot access the file because it is being used by another process")
    err.winerror = 32
    return err


class _PatchedUnlink:
    """Replace `Path.unlink` for paths whose name ends with `suffix`.

    `fail_times=None` means "always"; a number makes the lock transient, which is
    the case the backoff exists for. Counts calls so a test can assert the retry
    actually happened rather than inferring it from the status code.
    """

    def __init__(self, suffix: str, fail_times: int | None = None):
        self.suffix, self.fail_times, self.calls = suffix, fail_times, 0
        self._original = pathlib.Path.unlink

    def __enter__(self):
        original, this = self._original, self

        def patched(self, *args, **kwargs):
            if not str(self).endswith(this.suffix):
                return original(self, *args, **kwargs)
            this.calls += 1
            if this.fail_times is None or this.calls <= this.fail_times:
                raise _sharing_violation()
            return original(self, *args, **kwargs)

        pathlib.Path.unlink = patched
        return self

    def __exit__(self, *exc):
        pathlib.Path.unlink = self._original


class _PatchedRename:
    """The same, for `Path.rename` — `MoveFileEx` has the identical exposure."""

    def __init__(self, suffix: str, fail_times: int | None = None):
        self.suffix, self.fail_times, self.calls = suffix, fail_times, 0
        self._original = pathlib.Path.rename

    def __enter__(self):
        original, this = self._original, self

        def patched(self, target):
            if not str(self).endswith(this.suffix):
                return original(self, target)
            this.calls += 1
            if this.fail_times is None or this.calls <= this.fail_times:
                raise _sharing_violation()
            return original(self, target)

        pathlib.Path.rename = patched
        return self

    def __exit__(self, *exc):
        pathlib.Path.rename = self._original


class _PatchedReplace:
    """The same, for `Path.replace` — pass 2's in-place frame overwrite.

    `Path.replace` and not `os.replace`, because that is the call
    `utils.replace_retrying` makes; patching the other one would leave the test
    passing while proving nothing. Matches on the *destination*, since the source
    is a uuid temp.
    """

    def __init__(self, suffix: str, fail_times: int | None = None):
        self.suffix, self.fail_times, self.calls = suffix, fail_times, 0
        self._original = pathlib.Path.replace

    def __enter__(self):
        original, this = self._original, self

        def patched(self, target):
            if not str(target).endswith(this.suffix):
                return original(self, target)
            this.calls += 1
            if this.fail_times is None or this.calls <= this.fail_times:
                raise _sharing_violation()
            return original(self, target)

        pathlib.Path.replace = patched
        return self

    def __exit__(self, *exc):
        pathlib.Path.replace = self._original


def test_a_locked_video_file_409s_and_keeps_the_row(tmp_path):
    """Not a 500, and not a half-delete: the exception fires before `db.delete`,
    so row and file both survive and retrying is a real option."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            async with env.Session() as db:
                path = Path((await db.execute(select(Video))).scalar_one().file_path)

            with _PatchedUnlink(".mp4") as patch:
                r = await env.client.delete(f"{API}/videos/{video['id']}")

            assert r.status_code == 409, r.text
            # The message names the actionable step, not the errno.
            assert "another program" in r.json()["detail"]
            # The retry is load-bearing, so pin that it happened at all.
            assert patch.calls == LOCK_ATTEMPTS, f"expected a retry, got {patch.calls}"

            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalar_one().id == video["id"]
            assert path.exists()

    run(scenario())


def test_a_lock_that_clears_within_the_backoff_deletes_normally(tmp_path):
    """The point of the retry: the socket teardown a delete races against is
    over in milliseconds, so the second or third attempt wins."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            async with env.Session() as db:
                path = Path((await db.execute(select(Video))).scalar_one().file_path)

            with _PatchedUnlink(".mp4", fail_times=2) as patch:
                r = await env.client.delete(f"{API}/videos/{video['id']}")

            assert r.status_code == 204, r.text
            assert patch.calls == 3
            assert not path.exists()
            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalar_one_or_none() is None

    run(scenario())


def test_a_locked_poster_alone_does_not_block_the_delete(tmp_path):
    """A poster is never a gate (CLAUDE.md § Videos are sources).

    409-ing on the poster would abandon a video file that is already gone,
    leaving a row `_rescan_videos` reports under `videos_missing` — a worse
    outcome than a stale .webp nothing reads.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
                file_path, poster_path = Path(row.file_path), Path(row.poster_path)
            assert poster_path.exists()

            with _PatchedUnlink(".webp"):
                r = await env.client.delete(f"{API}/videos/{video['id']}")

            assert r.status_code == 204, r.text
            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalar_one_or_none() is None
            assert not file_path.exists()
            assert poster_path.exists(), "the locked poster is left behind, deliberately"

    run(scenario())


def test_a_locked_video_file_409s_on_rename_and_keeps_the_old_name(tmp_path):
    """`MoveFileEx` fails the same way, and the row must not claim a name the
    file does not have — the commit runs after the rename, so a 409 rolls the
    whole thing back."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            async with env.Session() as db:
                old = Path((await db.execute(select(Video))).scalar_one().file_path)

            with _PatchedRename(".mp4") as patch:
                r = await env.client.patch(
                    f"{API}/videos/{video['id']}/rename", json={"new_stem": "renamed"}
                )

            assert r.status_code == 409, r.text
            assert patch.calls == LOCK_ATTEMPTS
            assert old.exists()
            assert not (old.parent / "renamed.mp4").exists()

            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
            assert row.filename == "clip.mp4"
            assert row.file_path == str(old)

    run(scenario())


def test_a_missing_file_still_404s_rather_than_409s(tmp_path):
    """The retry recognises *locked*, and nothing else — FileNotFoundError is a
    different OSError and keeps its own branch in `rename_video`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            async with env.Session() as db:
                Path((await db.execute(select(Video))).scalar_one().file_path).unlink()

            r = await env.client.patch(
                f"{API}/videos/{video['id']}/rename", json={"new_stem": "renamed"}
            )
            assert r.status_code == 404, r.text

    run(scenario())


# ---------------------------------------------------------------------------
# Pass 2's in-place overwrite — the third converted site, and not a route
# ---------------------------------------------------------------------------


async def _triage(env, dataset_id):
    """Upload a video and run pass 1, so there are frames to re-extract over."""
    video = await upload_video(env, dataset_id, "clip.mp4", mp4_shots_bytes())
    r = await env.client.post(
        f"{API}/videos/extract", json={"video_ids": [video["id"]], "long_edge": 160}
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


async def _reextract_and_wait(env, **body):
    r = await env.client.post(f"{API}/videos/reextract", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    return [await wait_for_job(env, g["job_id"], timeout=180) for g in payload["groups"]]


@needs_shots
def test_a_locked_frame_is_reported_failed_and_leaves_the_original_intact(tmp_path):
    """Pass 2 cannot answer a 409 — it is a job, not a request — so the lock
    becomes one `failed` frame and the run continues.

    Before the conversion the `WinError 32` propagated out of `_rewrite` and
    killed the whole job, which is worse than the delete bug this branch set out
    to fix: every remaining frame loses its turn over one open detail pane.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"])
            before = {f.id: Path(f.file_path).read_bytes() for f in frames}

            with _PatchedReplace(".jpg") as patch:
                jobs = await _reextract_and_wait(env, video_id=video["id"])

            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["rewritten"] == 0
            assert jobs[0]["result_data"]["failed"] == len(frames)
            # Every frame retried, so the backoff is wired into this path too.
            assert patch.calls == LOCK_ATTEMPTS * len(frames), patch.calls

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            for img in rows:
                assert Path(img.file_path).read_bytes() == before[img.id], (
                    "the original was modified despite the failed replace"
                )
                assert img.width == 160, "the row was updated for a frame never written"
            images_dir = Path(rows[0].file_path).parent
            assert not list(images_dir.glob("*.tmp*")), "a temp file was left behind"

    run(scenario())


@needs_shots
def test_a_frame_lock_that_clears_within_the_backoff_is_rewritten(tmp_path):
    """The only thing proving the retry is wired at all rather than the failure
    path merely being reachable."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, frames = await _triage(env, ds["id"])

            with _PatchedReplace(".jpg", fail_times=2) as patch:
                jobs = await _reextract_and_wait(env, video_id=video["id"])

            assert jobs[0]["status"] == "completed", jobs
            assert jobs[0]["result_data"]["rewritten"] == len(frames)
            assert jobs[0]["result_data"]["failed"] == 0
            assert patch.calls > len(frames), "the lock never actually fired"

            async with env.Session() as db:
                rows = (await db.execute(select(Image))).scalars().all()
            for img in rows:
                assert img.width > 160, "the frame was not re-extracted at full res"
            images_dir = Path(rows[0].file_path).parent
            assert not list(images_dir.glob("*.tmp*")), "a temp file was left behind"

    run(scenario())


@needs_shots
def test_a_locked_frame_counts_as_a_fault_and_feeds_the_breaker(tmp_path, monkeypatch):
    """`is_fault=True`, pinned by the one observable consequence of the flag.

    A lock that outlived the backoff is a pane someone left open, so it will
    still be there for the next frame. With `False`, a systemic lock would pay a
    full-res decode *and* a whole-file copy into `.versions/objects/` per frame
    for zero rewrites, then report "completed, 0 rewritten".
    """
    async def scenario():
        from backend.routers import videos as videos_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video, _frames = await _triage(env, ds["id"])
            monkeypatch.setattr(videos_router, "EXTRACT_MAX_CONSECUTIVE_FAILURES", 1)

            with _PatchedReplace(".jpg"):
                jobs = await _reextract_and_wait(env, video_id=video["id"])

            assert jobs[0]["status"] == "failed", jobs
            assert "consecutive failures" in (jobs[0]["error_msg"] or "")

    run(scenario())
