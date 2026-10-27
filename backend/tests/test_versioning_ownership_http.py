"""Versioning ids must belong to the dataset in the URL (GPT-024).

`POST /datasets/{A}/versions` used to accept B's `branch_id`: the new version got
`dataset_id=A` and `branch_id=<B's branch>`, and B's branch head moved onto it.
`create_snapshot` did `db.get(DatasetBranch, branch_id)` with no ownership check
and the router forwarded the id untouched. `create_branch`'s `from_version_id`
had the same hole.

The router now answers 404 *before* it creates a job — so a foreign id never
becomes a failed job row — and the service refuses it too, for every function
that takes a branch or version id alongside a dataset id. The router tests run in
both modes because Manual always takes the job path and Auto (under 100 images)
the inline one.
"""
import pytest
from sqlalchemy import func, select

from backend.models import BackgroundJob
from backend.models.versioning import DatasetBranch, DatasetVersion
from backend.services import version_service
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


async def _mode(env, mode):
    r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": mode})
    assert r.status_code == 200, r.text


async def _snap(env, ds_id, name):
    r = await env.client.post(f"{API}/datasets/{ds_id}/versions", json={"name": name})
    assert r.status_code == 200, r.text
    body = r.json()
    if "job_id" in body:
        job = await wait_for_job(env, body["job_id"])
        assert job["status"] == "completed", job
    versions = (await env.client.get(f"{API}/datasets/{ds_id}/versions")).json()
    return next(v for v in versions if v["name"] == name)["id"]


async def _branch(env, ds_id):
    return (await env.client.get(f"{API}/datasets/{ds_id}/versions/branches")).json()[0]


async def _two_snapshotted_datasets(env):
    a = await env.create_dataset("A")
    b = await env.create_dataset("B")
    await upload_image(env, a["id"], "a.png", png_bytes((1, 2, 3)))
    await upload_image(env, b["id"], "b.png", png_bytes((3, 2, 1)))
    va = await _snap(env, a["id"], "a1")
    vb = await _snap(env, b["id"], "b1")
    return a["id"], b["id"], va, vb


async def _counts(env):
    async with env.Session() as db:
        versions = (await db.execute(select(func.count(DatasetVersion.id)))).scalar()
        jobs = (await db.execute(select(func.count(BackgroundJob.id)))).scalar()
    return versions, jobs


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_snapshot_onto_a_foreign_branch_is_a_404(tmp_path, mode):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, mode)
            a_id, b_id, _, vb = await _two_snapshotted_datasets(env)
            b_branch = await _branch(env, b_id)
            assert b_branch["head_version_id"] == vb
            before = await _counts(env)

            r = await env.client.post(
                f"{API}/datasets/{a_id}/versions",
                json={"name": "x", "branch_id": b_branch["id"]},
            )
            assert r.status_code == 404, r.text
            assert r.json()["detail"] == "Branch not found"
            assert (await _branch(env, b_id))["head_version_id"] == vb
            assert await _counts(env) == before   # no version, no job row
    run(scenario())


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_snapshot_onto_a_nonexistent_branch_is_a_404_not_a_500(tmp_path, mode):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, mode)
            a = await env.create_dataset("A")
            await upload_image(env, a["id"], "a.png")
            before = await _counts(env)
            r = await env.client.post(
                f"{API}/datasets/{a['id']}/versions",
                json={"name": "x", "branch_id": "no-such-branch"},
            )
            assert r.status_code == 404, r.text
            assert await _counts(env) == before
    run(scenario())


def test_branch_from_a_foreign_version_is_a_404(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            a_id, _, _, vb = await _two_snapshotted_datasets(env)
            r = await env.client.post(
                f"{API}/datasets/{a_id}/versions/branches",
                json={"name": "side", "from_version_id": vb},
            )
            assert r.status_code == 404, r.text
            assert r.json()["detail"] == "Version not found"
            async with env.Session() as db:
                names = (await db.execute(
                    select(DatasetBranch.name).where(DatasetBranch.dataset_id == a_id)
                )).scalars().all()
            assert "side" not in names
    run(scenario())


def test_every_service_function_refuses_a_foreign_id(tmp_path):
    """The router checks are the user-facing half; these are the service's own,
    so a future caller that skips the router cannot reach another dataset."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            a_id, b_id, va, vb = await _two_snapshotted_datasets(env)
            b_branch = await _branch(env, b_id)
            before = await _counts(env)

            async with env.Session() as db:
                with pytest.raises(ValueError, match="Branch not found"):
                    await version_service.create_snapshot(
                        db, a_id, "x", "", branch_id=b_branch["id"]
                    )
            async with env.Session() as db:
                with pytest.raises(ValueError, match="not found"):
                    await version_service.create_snapshot(db, "no-such-dataset", "x", "")
            async with env.Session() as db:
                with pytest.raises(ValueError, match="Version not found"):
                    await version_service.create_branch(db, a_id, "side", from_version_id=vb)
            async with env.Session() as db:
                with pytest.raises(ValueError, match="Dataset not found"):
                    await version_service.create_branch(db, "no-such-dataset", "side")
            async with env.Session() as db:
                with pytest.raises(ValueError, match="Version not found"):
                    await version_service.restore_snapshot(
                        db, a_id, vb, pre_restore_snapshot=False
                    )
            async with env.Session() as db:
                with pytest.raises(ValueError, match="not found"):
                    await version_service.diff_versions(db, a_id, va, vb)
            async with env.Session() as db:
                with pytest.raises(ValueError, match="Branch not found"):
                    await version_service.checkout_branch(db, a_id, b_branch["id"])

            assert await _counts(env) == before
            assert (await _branch(env, b_id))["head_version_id"] == vb
    run(scenario())


def test_checkout_refuses_a_branch_whose_head_is_another_datasets_version(tmp_path):
    """A head can only name a foreign version through a write the checks above
    now refuse, so this is built by hand: the service must not restore it."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            a_id, _, _, vb = await _two_snapshotted_datasets(env)
            a_branch = await _branch(env, a_id)
            async with env.Session() as db:
                br = await db.get(DatasetBranch, a_branch["id"])
                br.head_version_id = vb
                await db.commit()
            async with env.Session() as db:
                with pytest.raises(ValueError, match="not found"):
                    await version_service.checkout_branch(
                        db, a_id, a_branch["id"], pre_restore_snapshot=False
                    )
    run(scenario())
