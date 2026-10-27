"""A caption sidecar that will not move cannot strand the image's row (GPT-009).

`utils.rename_with_sidecar` renames the image and then its `.txt`. The two are
not atomic, and before this fix a failed sidecar rename raised *after* the image
had already moved — so every rename-first caller (gallery rename, file-browser
rename) rolled back the row onto the old path of a file that was no longer
there: 500/403, and `GET /images/{id}/file` 404'd for good.

The contract now is "a raise means nothing moved": only the image rename can
fail the call, and a sidecar failure is logged and reported by the return value.
The lock is simulated with `winerror=32`, which proves the code path rather than
the Windows behaviour (see PM-021).
"""

import pathlib
from pathlib import Path

import httpx
from sqlalchemy import select

from backend.models import Image
from backend.tests.conftest import API, api_env, run, upload_image
from backend.utils import rename_with_sidecar


def _sharing_violation() -> PermissionError:
    err = PermissionError(1, "The process cannot access the file because it is being used by another process")
    err.winerror = 32
    return err


def _lock_txt_renames(monkeypatch):
    original = pathlib.Path.rename

    def patched(self, target):
        if self.suffix == ".txt":
            raise _sharing_violation()
        return original(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", patched)


def _client():
    from backend.main import app
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


async def _row(env, image_id):
    async with env.Session() as db:
        return await db.get(Image, image_id)


def test_the_helper_moves_the_image_and_reports_a_stuck_sidecar(tmp_path, monkeypatch):
    old = tmp_path / "a.png"
    old.write_bytes(b"img")
    old.with_suffix(".txt").write_text("cap", encoding="utf-8")
    _lock_txt_renames(monkeypatch)

    assert rename_with_sidecar(old, tmp_path / "b.png") is False
    assert (tmp_path / "b.png").read_bytes() == b"img"
    assert (tmp_path / "a.txt").exists()


def test_the_helper_raises_only_when_the_image_did_not_move(tmp_path):
    old = tmp_path / "missing.png"
    try:
        rename_with_sidecar(old, tmp_path / "b.png")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")
    assert not (tmp_path / "b.png").exists()


def test_a_stuck_sidecar_does_not_strand_a_gallery_rename(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            old = Path((await _row(env, img["id"])).file_path)
            old.with_suffix(".txt").write_text("cap", encoding="utf-8")
            _lock_txt_renames(monkeypatch)

            async with _client() as c:
                r = await c.patch(f"{API}/images/{img['id']}/rename", json={"new_stem": "zzz"})
            assert r.status_code == 200, r.text

            new = old.with_name("zzz.png")
            assert new.exists() and not old.exists()
            assert (await _row(env, img["id"])).file_path == str(new)
            assert (await env.client.get(f"{API}/images/{img['id']}/file")).status_code == 200

    run(scenario())


def test_a_stuck_sidecar_does_not_strand_a_file_browser_rename(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            old = Path((await _row(env, img["id"])).file_path)
            old.with_suffix(".txt").write_text("cap", encoding="utf-8")
            _lock_txt_renames(monkeypatch)

            async with _client() as c:
                r = await c.post(
                    f"{API}/filesystem/rename", json={"path": str(old), "new_name": "zzz.png"}
                )
            assert r.status_code == 200, r.text

            new = old.with_name("zzz.png")
            assert (await _row(env, img["id"])).file_path == str(new)
            assert (await env.client.get(f"{API}/images/{img['id']}/file")).status_code == 200

    run(scenario())


def test_a_failed_subfolder_move_rename_keeps_every_row_on_its_file(tmp_path, monkeypatch):
    """The second rename fails: the first image is moved *and* committed, the
    second stays at its old name under its old row, the third still moves."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = [(await upload_image(env, ds["id"], f"{n}.png"))["id"] for n in "abc"]
            victim = Path((await _row(env, ids[1])).file_path)

            original = pathlib.Path.rename

            def patched(self, target):
                if self == victim:
                    raise _sharing_violation()
                return original(self, target)

            monkeypatch.setattr(pathlib.Path, "rename", patched)
            r = await env.client.post(
                f"{API}/images/batch/move-subfolder",
                json={"image_ids": ids, "subfolder": "sub", "rename_on_move": True},
            )
            assert r.status_code == 200, r.text
            assert r.json()["moved"] == 2 and r.json()["failed"] == 1

            async with env.Session() as db:
                rows = {i.id: i for i in (await db.execute(select(Image))).scalars()}
            for image_id in ids:
                row = rows[image_id]
                assert Path(row.file_path).exists(), row.file_path
            assert rows[ids[1]].file_path == str(victim)
            assert rows[ids[1]].subfolder in (None, "")
            assert rows[ids[0]].subfolder == "sub" and rows[ids[2]].subfolder == "sub"

    run(scenario())
