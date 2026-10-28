"""A locked image file is reported, not crashed into — every image-delete site (GPT-008).

The image deletes used to commit the row removal and *then* unlink the file. A
file another handle holds open (on Windows, routinely Crucible's own
`FileResponse`; see PM-021) answered 500 with the row already gone, the file
still on disk, the dataset counters stale — `refresh_stats` ran after the
failing unlink — and a retry impossible. They now follow `delete_video`: files
first through `unlink_retrying`, and the row goes only once its file has.

`winerror=32` with `errno` EPERM exercises the Windows branch of
`utils._is_locked_error`, which proves the code path rather than the OS
behaviour. Helpers are copied from `test_video_locked_files_http.py`, which
`importorskip`s cv2 and so cannot be imported here.
"""

import pathlib
from pathlib import Path

import httpx
from sqlalchemy import func, select

from backend.models import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image
from backend.utils import _LOCK_RETRY_DELAYS

LOCK_ATTEMPTS = len(_LOCK_RETRY_DELAYS) + 1


def _sharing_violation() -> PermissionError:
    err = PermissionError(1, "The process cannot access the file because it is being used by another process")
    err.winerror = 32
    return err


class _PatchedUnlink:
    """Lock `Path.unlink` for exactly the paths in `locked`; `fail_times=None` is "always"."""

    def __init__(self, locked: set[Path], fail_times: int | None = None):
        self.locked, self.fail_times, self.calls = locked, fail_times, 0
        self._original = pathlib.Path.unlink

    def __enter__(self):
        original, this = self._original, self

        def patched(self, *args, **kwargs):
            if self not in this.locked:
                return original(self, *args, **kwargs)
            this.calls += 1
            if this.fail_times is None or this.calls <= this.fail_times:
                raise _sharing_violation()
            return original(self, *args, **kwargs)

        pathlib.Path.unlink = patched
        return self

    def __exit__(self, *exc):
        pathlib.Path.unlink = self._original


def _client():
    from backend.main import app
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://localhost"
    )


async def _row(env, image_id):
    async with env.Session() as db:
        return await db.get(Image, image_id)


async def _image_count(env, dataset_id) -> int:
    return (await env.client.get(f"{API}/datasets/{dataset_id}")).json()["image_count"]


def test_a_locked_image_file_409s_and_keeps_the_row(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            target = Path((await _row(env, img["id"])).file_path)

            with _PatchedUnlink({target}) as patch:
                async with _client() as c:
                    r = await c.delete(f"{API}/images/{img['id']}")
            assert r.status_code == 409, r.text
            assert "a.png" in r.json()["detail"]
            assert patch.calls == LOCK_ATTEMPTS
            assert await _row(env, img["id"]) is not None
            assert target.exists()
            assert await _image_count(env, ds["id"]) == 1

            # Once the lock is gone, the same request just works.
            r = await env.client.delete(f"{API}/images/{img['id']}")
            assert r.status_code == 204
            assert await _row(env, img["id"]) is None and not target.exists()
            assert await _image_count(env, ds["id"]) == 0

    run(scenario())


def test_a_lock_that_clears_within_the_backoff_deletes_normally(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            target = Path((await _row(env, img["id"])).file_path)

            with _PatchedUnlink({target}, fail_times=2) as patch:
                r = await env.client.delete(f"{API}/images/{img['id']}")
            assert r.status_code == 204
            assert patch.calls == 3
            assert not target.exists() and await _row(env, img["id"]) is None

    run(scenario())


def test_a_locked_thumbnail_or_sidecar_alone_does_not_block_the_delete(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            row = await _row(env, img["id"])
            sidecar = Path(row.file_path).with_suffix(".txt")
            sidecar.write_text("cap", encoding="utf-8")
            thumb = Path(row.thumbnail_path)
            thumb.parent.mkdir(parents=True, exist_ok=True)
            thumb.write_bytes(b"webp")

            with _PatchedUnlink({sidecar, thumb}):
                r = await env.client.delete(f"{API}/images/{img['id']}")
            assert r.status_code == 204
            assert not Path(row.file_path).exists()
            assert await _row(env, img["id"]) is None
            assert await _image_count(env, ds["id"]) == 0

    run(scenario())


async def _three(env):
    ds = await env.create_dataset("d")
    imgs = [
        await upload_image(env, ds["id"], f"{n}.png", png_bytes(color=(i * 60, 10, 10)))
        for i, n in enumerate("abc")
    ]
    locked = Path((await _row(env, imgs[1]["id"])).file_path)
    return ds, [i["id"] for i in imgs], locked


async def _assert_only_the_locked_one_survives(env, ds, ids, locked):
    async with env.Session() as db:
        left = (await db.execute(select(Image.id).where(Image.dataset_id == ds["id"]))).scalars().all()
    assert left == [ids[1]]
    assert locked.exists()
    assert await _image_count(env, ds["id"]) == 1


def test_batch_delete_keeps_only_the_locked_row(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, ids, locked = await _three(env)
            with _PatchedUnlink({locked}):
                r = await env.client.request("DELETE", f"{API}/images/batch/delete", json=ids)
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": 2, "in_use": 1, "failed": 0}
            await _assert_only_the_locked_one_survives(env, ds, ids, locked)

    run(scenario())


def test_bulk_delete_keeps_only_the_locked_row(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, ids, locked = await _three(env)
            with _PatchedUnlink({locked}):
                r = await env.client.post(
                    f"{API}/images/bulk-delete", json={"dataset_id": ds["id"], "image_ids": ids}
                )
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": 2, "in_use": 1, "failed": 0}
            await _assert_only_the_locked_one_survives(env, ds, ids, locked)

    run(scenario())


def test_resolving_duplicates_keeps_only_the_locked_copy(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = [(await upload_image(env, ds["id"], f"{n}.png"))["id"] for n in "abc"]
            locked = Path((await _row(env, ids[1])).file_path)
            with _PatchedUnlink({locked}):
                r = await env.client.post(
                    f"{API}/quality/duplicates/resolve",
                    json={"keep_ids": [ids[0]], "delete_ids": ids[1:]},
                )
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": 1, "in_use": 1, "failed": 0}
            async with env.Session() as db:
                left = set((await db.execute(select(Image.id))).scalars())
                assert left == {ids[0], ids[1]}
                assert (await db.execute(select(func.count()).select_from(Image))).scalar() == 2
            assert locked.exists()
            assert await _image_count(env, ds["id"]) == 2

    run(scenario())


def test_a_non_lock_failure_is_counted_failed_and_keeps_the_row(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, ids, victim = await _three(env)
            original = pathlib.Path.unlink

            def patched(self, *args, **kwargs):
                if self == victim:
                    raise IsADirectoryError(21, "Is a directory")
                return original(self, *args, **kwargs)

            monkeypatch.setattr(pathlib.Path, "unlink", patched)
            r = await env.client.request("DELETE", f"{API}/images/batch/delete", json=ids)
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": 2, "in_use": 0, "failed": 1}
            await _assert_only_the_locked_one_survives(env, ds, ids, victim)

    run(scenario())


# ── The snapshot backup outlives a failure after the unlink ──────────────────
# The versioning hook records the doomed file's hash on its snapshot rows. Left
# pending in the delete's own transaction, a failure after the unlink — here the
# duplicate prune — rolled that record back with the file already gone, and no
# retry could re-record it (the hook skips a missing file). It is now committed
# before the first unlink.

async def _auto_snapshot(env, dataset_id):
    from backend.services import version_service
    r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "auto"})
    assert r.status_code == 200, r.text
    async with env.Session() as db:
        await version_service.create_snapshot(db, dataset_id, "s1", "")


async def _snapshot_hashes(env, image_id) -> list:
    from backend.models.versioning import VersionImageState
    async with env.Session() as db:
        rows = (await db.execute(
            select(VersionImageState.file_hash).where(VersionImageState.image_id == image_id)
        )).scalars().all()
    return list(rows)


def _break_prune(monkeypatch, module):
    async def boom(*_a, **_k):
        raise RuntimeError("simulated failure after the unlink")
    monkeypatch.setattr(module, "prune_orphaned_duplicate_flags", boom)


def test_a_failure_after_the_unlink_keeps_the_snapshot_backup(tmp_path, monkeypatch):
    import backend.routers.images as images_router

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            await _auto_snapshot(env, ds["id"])
            _break_prune(monkeypatch, images_router)

            async with _client() as c:
                r = await c.delete(f"{API}/images/{img['id']}")
            assert r.status_code == 500
            hashes = await _snapshot_hashes(env, img["id"])
            assert hashes and all(h is not None for h in hashes), hashes

    run(scenario())


def test_a_failure_after_the_batch_unlinks_keeps_every_snapshot_backup(tmp_path, monkeypatch):
    import backend.routers.images as images_router

    async def scenario():
        async with api_env(tmp_path) as env:
            ds, ids, _locked = await _three(env)
            await _auto_snapshot(env, ds["id"])
            _break_prune(monkeypatch, images_router)

            async with _client() as c:
                r = await c.request("DELETE", f"{API}/images/batch/delete", json=ids)
            assert r.status_code == 500
            for image_id in ids:
                hashes = await _snapshot_hashes(env, image_id)
                assert hashes and all(h is not None for h in hashes), (image_id, hashes)

    run(scenario())


def test_resolving_around_a_locked_copy_keeps_its_group(tmp_path):
    """Keep member M1, delete root R and member M2, M2's file locked. M2 survives,
    so M1 is still its duplicate: clearing M1's flags (and the backstop prune then
    clearing M2 as an orphan of a dead root) left two identical, unflagged images
    and nothing for "close it and try again" to resolve."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, (root, m1, m2), _ = await _three(env)
            locked = Path((await _row(env, m2)).file_path)
            async with env.Session() as db:
                for iid in (m1, m2):
                    (await db.get(Image, iid)).quality_flags = {"is_duplicate": True, "duplicate_of": root}
                await db.commit()
            with _PatchedUnlink({locked}):
                r = await env.client.post(
                    f"{API}/quality/duplicates/resolve",
                    json={"keep_ids": [m1], "delete_ids": [root, m2]},
                )
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": 1, "in_use": 1, "failed": 0}
            for iid in (m1, m2):
                flags = (await _row(env, iid)).quality_flags
                assert flags.get("is_duplicate") is True, iid
            groups = (await env.client.get(f"{API}/quality/duplicates/{ds['id']}")).json()["groups"]
            assert len(groups) == 1 and {g["id"] for g in groups[0]} == {m1, m2}

    run(scenario())
