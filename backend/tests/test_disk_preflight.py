"""The disk-space preflight: `utils.require_free_space` and the export paths using it.

Two layers, because they fail differently and a user meets both:

- the helper itself, with `shutil.disk_usage` monkeypatched — real free space is not
  something a test can arrange, and the arithmetic (headroom vs floor, walking up to
  an existing ancestor) is where the bugs live;
- the export endpoints, where a full disk must answer 507 *before* a job id is
  handed out, and a payload too big for the volume must fail the job with a message
  the user can read instead of dying mid-write on ENOSPC.
"""
import shutil
from collections import namedtuple

import pytest

from backend.models.image import Image
from backend.tests.conftest import API, api_env, run, upload_image, wait_for_job
from backend.utils import (
    DISK_FLOOR_BYTES,
    InsufficientDiskSpaceError,
    format_bytes,
    require_free_space,
)

GB = 2 ** 30
_Usage = namedtuple("_Usage", "total used free")


def _usage(free_bytes: int):
    """A fake shutil.disk_usage reporting `free_bytes` free."""
    def fake(_path):
        return _Usage(100 * GB, 100 * GB - free_bytes, free_bytes)
    return fake


def test_passes_when_free_space_covers_payload_and_headroom(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", _usage(10 * GB))
    require_free_space(tmp_path, 5 * GB)  # 5 GB × 1.2 headroom = 6 GB < 10 GB


def test_headroom_is_applied_to_the_estimate(tmp_path, monkeypatch):
    # 9 GB free is more than the 8 GB payload, but not the 9.6 GB it really needs.
    monkeypatch.setattr(shutil, "disk_usage", _usage(9 * GB))
    with pytest.raises(InsufficientDiskSpaceError) as exc:
        require_free_space(tmp_path, 8 * GB)
    assert "8.0 GB" in str(exc.value)  # the payload is named, so the message is actionable


def test_floor_applies_when_no_estimate_is_given(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", _usage(DISK_FLOOR_BYTES // 2))
    with pytest.raises(InsufficientDiskSpaceError):
        require_free_space(tmp_path)

    monkeypatch.setattr(shutil, "disk_usage", _usage(DISK_FLOOR_BYTES * 2))
    require_free_space(tmp_path)


def test_walks_up_to_the_nearest_existing_ancestor(tmp_path, monkeypatch):
    """The destination is usually created by the run itself, so it may not exist yet."""
    seen: list = []

    def fake(path):
        seen.append(path)
        return shutil._ntuple_diskusage(100 * GB, 0, 100 * GB)

    monkeypatch.setattr(shutil, "disk_usage", fake)
    require_free_space(tmp_path / "not" / "created" / "yet", 1)
    assert seen == [tmp_path.resolve()]


def test_unreadable_path_is_not_treated_as_full(tmp_path, monkeypatch):
    """A failing stat must not block the operation — let it run and fail for real."""
    def boom(_path):
        raise OSError("no such device")

    monkeypatch.setattr(shutil, "disk_usage", boom)
    require_free_space(tmp_path, 10 * GB)


def test_format_bytes_scales():
    assert format_bytes(512) == "512 B"
    assert format_bytes(2 * GB) == "2.0 GB"


# --- export --------------------------------------------------------------


def test_export_endpoint_rejects_a_full_disk_with_507(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("full-disk")
            await upload_image(env, ds["id"], "a.png")  # writes before the disk "fills"

            monkeypatch.setattr(shutil, "disk_usage", _usage(DISK_FLOOR_BYTES // 4))
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(tmp_path / "out"),
            })
            assert r.status_code == 507, r.text
            assert "disk space" in r.json()["detail"]

    run(scenario())


def test_export_endpoint_rejects_a_relative_output_dir(tmp_path):
    """The destination is a client-supplied path: `sanitize_abs_path` gates it (400)."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("relative-out")

            for bad in ("out/here", "out\x00/here"):
                r = await env.client.post(f"{API}/export/plain", json={
                    "dataset_id": ds["id"], "output_dir": bad,
                })
                assert r.status_code == 400, r.text

    run(scenario())


def test_export_job_fails_when_the_payload_exceeds_free_space(tmp_path, monkeypatch):
    """Free space clears the request-path floor but not the images' own size."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("big")
            img = await upload_image(env, ds["id"], "a.png")

            # Claim the one image is 4 GB; only the export loop's estimate reads this.
            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                row.file_size_bytes = 4 * GB
                await db.commit()

            monkeypatch.setattr(shutil, "disk_usage", _usage(2 * GB))  # > 256 MB floor, < 4 GB × 1.2
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(tmp_path / "out"),
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])

            assert job["status"] == "failed", job
            assert "disk space" in (job["error_msg"] or "")
            # Nothing was written: the check runs before the first file.
            assert not (tmp_path / "out").exists() or not list((tmp_path / "out").rglob("*.png"))

    run(scenario())
