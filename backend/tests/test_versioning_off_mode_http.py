"""`versioning_mode == "off"` refuses every versioning *write*, and no read.

Off mode used to guard only `create_snapshot` and `prune_versions`. The other six
write routes ran — and off mode also no-ops both copy-on-write hooks
(`protect_file_before_overwrite` fires only in `"auto"`,
`mark_image_deleted_in_versions` returns immediately on `"off"`), so a restore or
a version delete overwrote and unlinked the current files with nothing kept in
the object store. `VersionsPage` returns its disabled placeholder in this mode,
which is not a guard: the routes are reachable directly, and on an
unauthenticated API that is the whole exposure.

**The read half of this module is the load-bearing half.** `Sidebar` and
`DatasetsPage` call `listBranches`/`listVersions` whatever the mode is, so gating
the four read routes alongside the writes would turn a disabled feature into a
wall of 400s in the sidebar of every page. The asymmetry is deliberate; these
tests fail if someone "completes" the guard by adding it to the readers.

The guard sits *after* each route's 404 lookups, so every id used below is real —
a test that reached the guard with a bogus id would pass on a 404 and prove
nothing.
"""
from pathlib import Path

from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _snapshotted_dataset(env, tmp_path: Path):
    """A dataset with one image, one branch and one snapshot, left in "off" mode.

    The snapshot has to be taken while versioning is on — creating it is itself
    one of the routes under test.
    """
    r = await env.client.patch(
        f"{API}/settings/thresholds", json={"versioning_mode": "manual"}
    )
    assert r.status_code == 200, r.text

    ds = await env.create_dataset("d")
    await upload_image(env, ds["id"], "a.png", png_bytes((1, 2, 3)))

    from backend.services import version_service
    async with env.Session() as db:
        await version_service.create_snapshot(db, ds["id"], "s1", "")

    branches = await env.client.get(f"{API}/datasets/{ds['id']}/versions/branches")
    assert branches.status_code == 200, branches.text
    branch_id = branches.json()[0]["id"]

    versions = await env.client.get(f"{API}/datasets/{ds['id']}/versions")
    assert versions.status_code == 200, versions.text
    version_id = versions.json()[0]["id"]

    r = await env.client.patch(
        f"{API}/settings/thresholds", json={"versioning_mode": "off"}
    )
    assert r.status_code == 200, r.text

    return ds["id"], branch_id, version_id


def test_off_mode_refuses_every_versioning_write(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, branch_id, version_id = await _snapshotted_dataset(env, tmp_path)
            base = f"{API}/datasets/{ds_id}/versions"

            writes = [
                ("create snapshot", env.client.post(base, json={"name": "s2"})),
                ("create branch", env.client.post(
                    f"{base}/branches", json={"name": "b2"})),
                ("checkout branch", env.client.post(
                    f"{base}/branches/{branch_id}/checkout", json={})),
                ("delete branch", env.client.delete(f"{base}/branches/{branch_id}")),
                ("update version", env.client.patch(
                    f"{base}/{version_id}", json={"is_pinned": True})),
                ("restore version", env.client.post(
                    f"{base}/{version_id}/restore",
                    json={"handle_extra_images": "keep"})),
                ("delete version", env.client.delete(f"{base}/{version_id}")),
                ("prune", env.client.post(f"{base}/prune")),
            ]
            for label, coro in writes:
                r = await coro
                assert r.status_code == 400, f"{label}: {r.status_code} {r.text}"
                assert "disabled" in r.text.lower(), f"{label}: {r.text}"

    run(scenario())


def test_off_mode_leaves_the_read_routes_answering(tmp_path):
    """The sidebar queries these on every page; a 400 here is a UI regression."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, _branch_id, version_id = await _snapshotted_dataset(env, tmp_path)
            base = f"{API}/datasets/{ds_id}/versions"

            for label, url in [
                ("list branches", f"{base}/branches"),
                ("list versions", base),
                ("get version", f"{base}/{version_id}"),
            ]:
                r = await env.client.get(url)
                assert r.status_code == 200, f"{label}: {r.status_code} {r.text}"

    run(scenario())


def test_the_snapshot_survives_a_refused_restore(tmp_path):
    """The refusal is a precondition check: nothing is consumed on the way out."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, _branch_id, version_id = await _snapshotted_dataset(env, tmp_path)
            base = f"{API}/datasets/{ds_id}/versions"

            r = await env.client.post(
                f"{base}/{version_id}/restore", json={"handle_extra_images": "remove"}
            )
            assert r.status_code == 400, r.text

            listed = await env.client.get(base)
            assert listed.status_code == 200, listed.text
            assert [v["id"] for v in listed.json()] == [version_id]

    run(scenario())


def test_turning_versioning_back_on_restores_the_writes(tmp_path):
    """The guard reads the mode per request — it is not cached at import time."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, _branch_id, _version_id = await _snapshotted_dataset(env, tmp_path)
            base = f"{API}/datasets/{ds_id}/versions"

            refused = await env.client.post(f"{base}/branches", json={"name": "b2"})
            assert refused.status_code == 400, refused.text

            r = await env.client.patch(
                f"{API}/settings/thresholds", json={"versioning_mode": "manual"}
            )
            assert r.status_code == 200, r.text

            allowed = await env.client.post(f"{base}/branches", json={"name": "b2"})
            assert allowed.status_code == 200, allowed.text

    run(scenario())
