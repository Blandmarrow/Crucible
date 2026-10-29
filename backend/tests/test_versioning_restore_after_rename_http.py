"""Restoring a snapshot taken before a dataset rename (GPT-021, PM-025).

`VersionImageState.file_path` is absolute. `rename_dataset` used to rebase only
the live rows, so a pre-rename snapshot still named the old root and a restore
wrote there: it re-created the old folder, and when a new dataset had since
taken the old name it **overwrote** (Manual) or **moved over** (Auto) that
dataset's file — with no backup anywhere, since the safety snapshot protects
only the dataset being restored.

Two fixes, both pinned here end to end over HTTP:
- the rename rebases the snapshot rows, so the ordinary case simply works;
- restore's Pass 0c gate refuses a snapshot whose paths still escape (a legacy
  row the migration could not repair), before anything — including the
  pre-restore snapshot — is written.

These were `docs/dev/gpt-review/probes/backend/probe_versioning_021*.py`, which
asserted the bug; the assertions here are their inverse.
"""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from backend.models import DatasetVersion, Image, VersionImageState
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job

A_BYTES = png_bytes((200, 10, 10))
B_BYTES = png_bytes((10, 10, 200))


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def _set_mode(env, mode: str) -> None:
    r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": mode})
    assert r.status_code == 200, r.text


async def _snapshot(env, ds_id: str, name: str) -> str:
    r = await env.client.post(f"{API}/datasets/{ds_id}/versions", json={"name": name})
    assert r.status_code == 200, r.text
    if "job_id" in r.json():
        job = await wait_for_job(env, r.json()["job_id"])
        assert job["status"] == "completed", job
    versions = (await env.client.get(f"{API}/datasets/{ds_id}/versions")).json()
    return next(v for v in versions if v["name"] == name)["id"]


async def _restore(env, ds_id: str, version_id: str) -> dict:
    r = await env.client.post(
        f"{API}/datasets/{ds_id}/versions/{version_id}/restore",
        json={"handle_extra_images": "keep", "pre_restore_snapshot": True},
    )
    assert r.status_code == 200, r.text
    return await wait_for_job(env, r.json()["job_id"])


async def _file_path(env, image_id: str) -> str:
    async with env.Session() as db:
        return (await db.get(Image, image_id)).file_path


async def _rename_then_reuse(env, mode: str, reuse: bool):
    """A snapshotted as `reused-name`, renamed, and (optionally) B created under
    the freed name with its own `x.png`. Returns everything the asserts need."""
    await _set_mode(env, mode)
    a = await env.create_dataset("reused-name")
    a_img = await upload_image(env, a["id"], "x.png", A_BYTES)
    vid = await _snapshot(env, a["id"], "snapA")
    old_root = Path(a["folder_path"])

    r = await env.client.patch(f"{API}/datasets/{a['id']}", json={"name": "new-root-name"})
    assert r.status_code == 200, r.text
    new_root = Path(r.json()["folder_path"])

    b = await env.create_dataset("reused-name" if reuse else "other-name")
    if reuse:
        assert Path(b["folder_path"]) == old_root, "the scenario needs B to take A's old folder"
    b_img = await upload_image(env, b["id"], "x.png", B_BYTES)
    b_path = await _file_path(env, b_img["id"])
    return a, a_img, vid, old_root, new_root, b, b_img, b_path


@pytest.mark.parametrize("mode", ["manual", "auto"])
def test_restore_after_rename_with_name_reuse_leaves_the_other_dataset_alone(tmp_path, mode):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            a, a_img, vid, old_root, new_root, b, b_img, b_path = await _rename_then_reuse(
                env, mode, reuse=True
            )
            b_before = _sha(b_path)

            job = await _restore(env, a["id"], vid)
            assert job["status"] == "completed", job

            assert _sha(b_path) == b_before, "B's file must be untouched"
            a_path = await _file_path(env, a_img["id"])
            assert a_path == str(new_root / "images" / "x.png")
            assert _sha(a_path) == hashlib.sha256(A_BYTES).hexdigest()
            assert a_path != await _file_path(env, b_img["id"])

    run(scenario())


@pytest.mark.parametrize("mode", ["manual", "auto"])
def test_restore_after_rename_does_not_recreate_the_old_root(tmp_path, mode):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            a, a_img, vid, old_root, new_root, b, b_img, b_path = await _rename_then_reuse(
                env, mode, reuse=False
            )
            b_before = _sha(b_path)

            job = await _restore(env, a["id"], vid)
            assert job["status"] == "completed", job

            assert _sha(b_path) == b_before
            assert not old_root.exists(), "restore re-created the pre-rename folder"
            a_path = Path(await _file_path(env, a_img["id"]))
            assert a_path.is_relative_to(new_root) and a_path.exists()

    run(scenario())


def test_a_legacy_unrebased_snapshot_is_refused_and_changes_nothing(tmp_path):
    """The gate, over HTTP. A snapshot row still naming the old root — what every
    snapshot looked like before the rename rebase, and what the migration leaves
    when it cannot prove a rebase safe — would land on B's file. The job fails
    with the reason, and nothing anywhere changes: no pre-restore version, B's
    bytes, A's row and the branch head all as they were."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            a, a_img, vid, old_root, new_root, b, b_img, b_path = await _rename_then_reuse(
                env, "manual", reuse=True
            )
            async with env.Session() as db:
                await db.execute(
                    update(VersionImageState)
                    .where(VersionImageState.version_id == vid)
                    .values(file_path=str(old_root / "images" / "x.png"))
                )
                await db.commit()
                versions_before = (await db.execute(
                    select(func.count()).select_from(DatasetVersion)
                    .where(DatasetVersion.dataset_id == a["id"])
                )).scalar_one()
            a_path_before = await _file_path(env, a_img["id"])
            b_before = _sha(b_path)
            a_before = _sha(a_path_before)

            job = await _restore(env, a["id"], vid)
            assert job["status"] == "failed", job
            assert "Restore refused" in (job.get("error_msg") or ""), job
            assert "Nothing was changed" in job["error_msg"]

            assert _sha(b_path) == b_before
            assert await _file_path(env, a_img["id"]) == a_path_before
            assert _sha(a_path_before) == a_before
            async with env.Session() as db:
                versions_after = (await db.execute(
                    select(func.count()).select_from(DatasetVersion)
                    .where(DatasetVersion.dataset_id == a["id"])
                )).scalar_one()
            assert versions_after == versions_before, "a refusal must not leave a pre-restore version"

    run(scenario())
