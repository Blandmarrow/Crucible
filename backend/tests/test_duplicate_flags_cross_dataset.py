"""A duplicate mark never survives a dataset boundary it cannot point inside.

`duplicate_of` names *another row*, which makes it derived-from-elsewhere in
exactly the sense `Image.source_video_id` is — and the rule CLAUDE.md already
states for that column applies unchanged: remapped by the path that carried the
row it names, NULLed by every other. Copied verbatim it recreates PM-022's
symptom in the destination and is worse than the original defect, because the
prune cannot reach it: the root is alive, just in another dataset.

Four paths cross a boundary, and each is covered below — `batch_copy_dataset`,
`batch_move_dataset`, and `duplicate_dataset`'s two branches. The last case pins
the reader half of the same invariant: `get_duplicates` resolves a group's root
inside the dataset it was asked about, so pre-existing drift renders as a group
with no `kept` row rather than pulling a foreign image into the payload where
*Keep best* can delete it.

Modelled on the `test_cross_dataset_*` group in `test_video_lineage_mirrors.py`,
which pins the same rule for `source_video_id`.
"""
from sqlalchemy import select

from backend.models.image import Image
from backend.models.versioning import VersionImageState
from backend.tests.conftest import API, api_env, run, upload_image, wait_for_job
from backend.tests.test_duplicate_groups_http import _seed_group


async def _by_original(env, dataset_id: str) -> dict[str, Image]:
    """The dataset's rows keyed by `original_filename`.

    A cross-dataset copy renames the file it lands (`image.png`, `image_001.png`
    …), so the uploaded name is the only stable handle on which copy is which.
    """
    async with env.Session() as db:
        rows = (await db.execute(
            select(Image).where(Image.dataset_id == dataset_id)
        )).scalars().all()
    return {img.original_filename: img for img in rows}


async def _flags(env, image_id: str) -> dict:
    async with env.Session() as db:
        return dict((await db.get(Image, image_id)).quality_flags or {})


def test_copying_a_member_without_its_root_drops_the_mark(tmp_path):
    """The copy would otherwise land flagged as a duplicate of an image the
    destination does not contain — unresolvable by its duplicates panel and
    unreachable by its prune, since the root is alive back in the source."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            root, a, _b = await _seed_group(env, src["id"], ["root.png", "a.png", "b.png"])

            r = await env.client.post(
                f"{API}/images/batch/copy-dataset",
                json={"image_ids": [a["id"]], "target_dataset_id": dest["id"], "subfolder": ""},
            )
            assert r.status_code == 200, r.text

            copies = await _by_original(env, dest["id"])
            assert list(copies) == ["a.png"]
            assert dict(copies["a.png"].quality_flags or {}) == {}
            # The original is untouched.
            assert await _flags(env, a["id"]) == {
                "is_duplicate": True, "duplicate_of": root["id"],
            }

    run(scenario())


def test_copying_a_whole_group_remaps_onto_the_copied_root(tmp_path):
    """The remap needs the *complete* id map, so it runs after the constructor
    loop: the root is uploaded first and copied first here, but a member whose
    root sorts after it must resolve just as well."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            root, a, b = await _seed_group(env, src["id"], ["root.png", "a.png", "b.png"])

            r = await env.client.post(
                f"{API}/images/batch/copy-dataset",
                json={
                    "image_ids": [a["id"], b["id"], root["id"]],
                    "target_dataset_id": dest["id"],
                    "subfolder": "",
                },
            )
            assert r.status_code == 200, r.text

            copies = await _by_original(env, dest["id"])
            new_root = copies["root.png"].id
            assert new_root != root["id"]
            for name in ("a.png", "b.png"):
                assert dict(copies[name].quality_flags or {}) == {
                    "is_duplicate": True, "duplicate_of": new_root,
                }, name
            # A root is not itself flagged, here as anywhere else.
            assert dict(copies["root.png"].quality_flags or {}) == {}

            # The destination's panel now renders its own group, with its own
            # root marked kept and no row from the source in it.
            groups = (await env.client.get(f"{API}/quality/duplicates/{dest['id']}")).json()["groups"]
            assert len(groups) == 1
            assert groups[0][0]["id"] == new_root and groups[0][0]["kept"] is True
            assert {m["id"] for m in groups[0]} == {new_root, copies["a.png"].id, copies["b.png"].id}

    run(scenario())


def test_a_copy_never_shares_its_quality_flags_dict_with_the_source(tmp_path):
    """Two rows holding one dict object is the trap the CLAUDE.md JSON invariant
    is about: an edit to either would look like a change to neither."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            _root, a, _b = await _seed_group(env, src["id"], ["root.png", "a.png", "b.png"])
            async with env.Session() as db:
                row = await db.get(Image, a["id"])
                row.quality_flags = {**row.quality_flags, "is_blurry": True}
                await db.commit()

            r = await env.client.post(
                f"{API}/images/batch/copy-dataset",
                json={"image_ids": [a["id"]], "target_dataset_id": dest["id"], "subfolder": ""},
            )
            assert r.status_code == 200, r.text

            copies = await _by_original(env, dest["id"])
            # Unrelated flags travel; only the two duplicate keys are decided at
            # the boundary.
            assert dict(copies["a.png"].quality_flags or {}) == {"is_blurry": True}
            assert await _flags(env, a["id"]) == {
                "is_duplicate": True, "duplicate_of": _root["id"], "is_blurry": True,
            }

    run(scenario())


def test_moving_a_member_alone_strips_it_and_moving_the_group_keeps_it(tmp_path):
    """A move is an UPDATE in place, so the ids do not change and the map is the
    identity over the moved set: a whole group travelling together resolves, and
    a member leaving its root behind does not."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            root, a, b = await _seed_group(env, src["id"], ["root.png", "a.png", "b.png"])

            r = await env.client.post(
                f"{API}/images/batch/move-dataset",
                json={"image_ids": [a["id"]], "target_dataset_id": dest["id"], "subfolder": ""},
            )
            assert r.status_code == 200, r.text
            assert await _flags(env, a["id"]) == {}

            # The root and the other member together: the ids are unchanged, so
            # the mark still names a row inside the destination.
            r = await env.client.post(
                f"{API}/images/batch/move-dataset",
                json={
                    "image_ids": [root["id"], b["id"]],
                    "target_dataset_id": dest["id"],
                    "subfolder": "",
                },
            )
            assert r.status_code == 200, r.text
            assert await _flags(env, b["id"]) == {
                "is_duplicate": True, "duplicate_of": root["id"],
            }

    run(scenario())


def test_duplicate_dataset_points_the_clones_groups_at_its_own_rows(tmp_path):
    """The clone is a whole dataset, so every group travels — but as the clone's
    own group. A verbatim copy would leave every member of it pointing back at
    the source, which is the one form of drift no scan of either dataset repairs
    (the source's rows are alive; the clone's marks name none of its own)."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            root, a, _b = await _seed_group(env, src["id"], ["root.png", "a.png", "b.png"])

            r = await env.client.post(f"{API}/datasets/{src['id']}/duplicate", json={"new_name": "copy"})
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job
            clone_id = job["result_data"]["dataset_id"]

            clone = await _by_original(env, clone_id)
            new_root = clone["root.png"].id
            assert new_root != root["id"]
            assert dict(clone["a.png"].quality_flags or {}) == {
                "is_duplicate": True, "duplicate_of": new_root,
            }
            # And the source keeps its own.
            assert await _flags(env, a["id"]) == {
                "is_duplicate": True, "duplicate_of": root["id"],
            }

    run(scenario())


def test_duplicate_dataset_from_a_snapshot_remaps_and_strips_a_missing_root(tmp_path):
    """The snapshot's `quality_flags` carry `duplicate_of` as a *live* image id,
    so the same remap applies — keyed off `VersionImageState.image_id`, the
    mirror's link back to the row a state came from. It is nullable and carries
    no FK, so a state whose image is gone contributes no map entry and any mark
    naming it is stripped rather than carried across."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            root, _a, _b = await _seed_group(env, src["id"], ["root.png", "a.png", "b.png"])

            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{src['id']}/versions", json={"name": "v1"})
            assert r.status_code in (200, 201, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{src['id']}/versions")).json()[0]["id"]

            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate",
                json={"new_name": "from-snapshot", "source_version_id": version_id},
            )
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            clone = await _by_original(env, job["result_data"]["dataset_id"])
            new_root = clone["root.png"].id
            assert new_root != root["id"]
            assert dict(clone["a.png"].quality_flags or {}) == {
                "is_duplicate": True, "duplicate_of": new_root,
            }

            # Now sever the root's state from any live row, as a snapshot older
            # than the image's deletion has it, and duplicate again.
            async with env.Session() as db:
                state = (await db.execute(
                    select(VersionImageState).where(
                        VersionImageState.version_id == version_id,
                        VersionImageState.image_id == root["id"],
                    )
                )).scalar_one()
                state.image_id = None
                await db.commit()

            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate",
                json={"new_name": "from-orphan-snapshot", "source_version_id": version_id},
            )
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            orphan_clone = await _by_original(env, job["result_data"]["dataset_id"])
            assert dict(orphan_clone["a.png"].quality_flags or {}) == {}
            assert dict(orphan_clone["b.png"].quality_flags or {}) == {}

    run(scenario())


def test_the_duplicates_panel_never_renders_a_row_from_another_dataset(tmp_path):
    """The reader half, pinned independently of the four writers: drift written
    by an older build must render as a group with no `kept` row — the case the
    endpoint already documents for a root deleted since the scan — rather than
    resolving to a foreign image *Keep best* could then delete."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            root, _a, _b = await _seed_group(env, src["id"], ["root.png", "a.png", "b.png"])
            stray = await upload_image(env, dest["id"], "stray.png")
            spare = await upload_image(env, dest["id"], "spare.png")

            # Exactly what a pre-fix copy left behind: a flagged row naming a
            # root in another dataset.
            async with env.Session() as db:
                (await db.get(Image, stray["id"])).quality_flags = {
                    "is_duplicate": True, "duplicate_of": root["id"],
                }
                await db.commit()

            groups = (await env.client.get(f"{API}/quality/duplicates/{dest['id']}")).json()["groups"]
            assert len(groups) == 1
            assert [m["id"] for m in groups[0]] == [stray["id"]]
            assert all(m["kept"] is False for m in groups[0])

            # And the first delete in that dataset repairs it: the aliveness
            # check is dataset-scoped, so a root that is elsewhere counts as gone
            # and the lone survivor is pruned.
            r = await env.client.delete(f"{API}/images/{spare['id']}")
            assert r.status_code in (200, 204), r.text
            assert await _flags(env, stray["id"]) == {}
            # The source's own group is none of that dataset's business.
            assert await _flags(env, _a["id"]) == {
                "is_duplicate": True, "duplicate_of": root["id"],
            }

    run(scenario())
