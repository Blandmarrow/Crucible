"""`gate_rejected` goes when an image leaves `rejects/` (the user's ruling, 2026-11-04).

One test per writer of `Image.subfolder` (`services/gate_flags.py`): `move_to_subfolder`
in both branches, `repath_subfolder`, `delete_subfolder`, and the cross-dataset move and
copy. A move that stays inside `rejects/` keeps the flag, and so does the gate's own
move into it.
"""
from sqlalchemy import select

from backend.models.comfy import ComfyPlan, ComfyRow
from backend.models.image import Image
from backend.schemas.run import GateSettings
from backend.services import dataset_service, image_move, run_gate
from backend.services.gate_flags import REJECTS_FOLDER, clear_gate_flags_outside_rejects
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image

FLAGGED = {"gate_rejected": True, "gate_reasons": ["is_blurry"], "is_blurry": True}


async def _seed(env, ds_id: str, name: str, subfolder: str, n: int = 0) -> str:
    """An uploaded image labelled `subfolder` and carrying the gate's flag."""
    img = await upload_image(env, ds_id, name, png_bytes((n, 9, 9)))
    async with env.Session() as db:
        row = await db.get(Image, img["id"])
        row.subfolder = subfolder
        row.quality_flags = dict(FLAGGED)
        await db.commit()
    return img["id"]


async def _flags(env, image_id: str) -> dict:
    async with env.Session() as db:
        return dict((await db.get(Image, image_id)).quality_flags or {})


def _cleared(flags: dict) -> bool:
    return "gate_rejected" not in flags and "gate_reasons" not in flags


def test_the_helper_clears_only_images_outside_rejects(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            out = await _seed(env, ds["id"], "a.png", "gen/keep", 1)
            inside = await _seed(env, ds["id"], "b.png", f"gen/{REJECTS_FOLDER}/x", 2)
            root = await _seed(env, ds["id"], "c.png", "", 3)
            async with env.Session() as db:
                assert await clear_gate_flags_outside_rejects(db, ds["id"]) == 2
                await db.commit()
            assert _cleared(await _flags(env, out)) and _cleared(await _flags(env, root))
            assert await _flags(env, inside) == FLAGGED
            # Other flags survive, and the helper is idempotent.
            assert (await _flags(env, out)) == {"is_blurry": True}
            async with env.Session() as db:
                assert await clear_gate_flags_outside_rejects(db, ds["id"]) == 0
    run(go())


def test_move_to_subfolder_clears_the_flag_without_rename(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await _seed(env, ds["id"], "a.png", "gen/rejects/x", 1)
            async with env.Session() as db:
                await image_move.move_to_subfolder(db, [a], "gen/keep", rename_on_move=False)
            assert _cleared(await _flags(env, a))
    run(go())


def test_move_to_subfolder_clears_the_flag_with_rename(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await _seed(env, ds["id"], "a.png", "gen/rejects/x", 1)
            async with env.Session() as db:
                res = await image_move.move_to_subfolder(db, [a], "gen/keep", rename_on_move=True)
            assert res["moved"] == 1
            assert _cleared(await _flags(env, a))
    run(go())


def test_a_move_inside_rejects_keeps_the_flag(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await _seed(env, ds["id"], "a.png", "gen/rejects/x", 1)
            b = await _seed(env, ds["id"], "b.png", "gen/rejects/x", 2)
            async with env.Session() as db:
                await image_move.move_to_subfolder(db, [a], "gen/rejects/y", rename_on_move=False)
            async with env.Session() as db:
                await image_move.move_to_subfolder(db, [b], "gen/rejects/z", rename_on_move=True)
            assert await _flags(env, a) == FLAGGED
            assert await _flags(env, b) == FLAGGED
    run(go())


def test_the_gate_keeps_every_flag_across_several_targets(tmp_path):
    """The gate flags all its rejects before its first move; an earlier move must not
    clear the flag of a later target's image while that image is still outside rejects/."""
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = []
            for n, sub in enumerate(("gen/a", "gen/b")):
                img = await upload_image(env, ds["id"], f"{n}.png", png_bytes((n, 1, 1)))
                ids.append(img["id"])
                async with env.Session() as db:
                    row = await db.get(Image, img["id"])
                    row.subfolder = sub
                    row.quality_flags = {"is_blurry": True}
                    await db.commit()
            async with env.Session() as db:
                plan = ComfyPlan(dataset_id=ds["id"], name="p", workflow_json={}, pinned_params=[],
                                 output_node_ids=[])
                db.add(plan)
                await db.flush()
                rows = [ComfyRow(plan_id=plan.id, status="completed", subfolder=sub, image_ids=[iid])
                        for iid, sub in zip(ids, ("a", "b"))]
                db.add_all(rows)
                await db.flush()
                res = await run_gate.apply_gate(
                    db, [r.id for r in rows], "gen", GateSettings(exclude_flags=["is_blurry"]),
                    dataset_id=ds["id"],
                )
                await db.commit()
            assert res["rejected"] == 2
            for iid in ids:
                assert (await _flags(env, iid))["gate_rejected"] is True
            async with env.Session() as db:
                subs = {r.subfolder for r in (await db.execute(select(Image))).scalars()}
            assert subs == {"gen/rejects/a", "gen/rejects/b"}
    run(go())


def test_repath_subfolder_clears_the_flag(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await _seed(env, ds["id"], "a.png", "gen/rejects/x", 1)
            keep = await _seed(env, ds["id"], "b.png", "other/rejects/x", 2)
            async with env.Session() as db:
                await dataset_service.repath_subfolder(db, ds["id"], "gen/rejects", "gen/ok")
            assert _cleared(await _flags(env, a))
            assert await _flags(env, keep) == FLAGGED
            # A rename that stays inside rejects/ keeps it.
            async with env.Session() as db:
                await dataset_service.repath_subfolder(db, ds["id"], "other/rejects", "elsewhere/rejects")
            assert await _flags(env, keep) == FLAGGED
    run(go())


def test_delete_subfolder_clears_the_flag(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await _seed(env, ds["id"], "a.png", "gen/rejects/x", 1)
            async with env.Session() as db:
                await dataset_service.delete_subfolder(db, ds["id"], "gen/rejects")
            assert _cleared(await _flags(env, a))
            async with env.Session() as db:
                assert (await db.get(Image, a)).subfolder == ""
    run(go())


def test_a_cross_dataset_move_clears_the_flag_outside_rejects_only(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            a = await _seed(env, src["id"], "a.png", "gen/rejects/x", 1)
            b = await _seed(env, src["id"], "b.png", "gen/rejects/x", 2)
            r = await env.client.post(f"{API}/images/batch/move-dataset", json={
                "image_ids": [a], "target_dataset_id": dest["id"], "subfolder": "fresh"})
            assert r.status_code == 200, r.text
            r = await env.client.post(f"{API}/images/batch/move-dataset", json={
                "image_ids": [b], "target_dataset_id": dest["id"], "subfolder": "gen/rejects/x"})
            assert r.status_code == 200, r.text
            assert _cleared(await _flags(env, a))
            assert (await _flags(env, b))["gate_rejected"] is True
    run(go())


def test_a_cross_dataset_copy_clears_the_flag_outside_rejects_only(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            a = await _seed(env, src["id"], "a.png", "gen/rejects/x", 1)
            for sub in ("fresh", "gen/rejects/x"):
                r = await env.client.post(f"{API}/images/batch/copy-dataset", json={
                    "image_ids": [a], "target_dataset_id": dest["id"], "subfolder": sub})
                assert r.status_code == 200, r.text
            async with env.Session() as db:
                copies = {i.subfolder: dict(i.quality_flags or {}) for i in (await db.execute(
                    select(Image).where(Image.dataset_id == dest["id"]))).scalars()}
            assert _cleared(copies["fresh"])
            assert copies["gen/rejects/x"]["gate_rejected"] is True
            # The original keeps its flag: it is still in rejects/.
            assert (await _flags(env, a))["gate_rejected"] is True
    run(go())
