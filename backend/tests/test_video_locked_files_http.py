"""A locked video file answers 409, not 500 — DELETE /videos/{id} and rename.

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
"""

import pathlib
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.models import Video
from backend.tests.conftest import API, api_env, run, upload_video

pytest.importorskip("cv2", reason="opencv is not installed")


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
            assert patch.calls == 5, f"expected 5 attempts, got {patch.calls}"

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
            assert patch.calls == 5
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
