"""A client-supplied id list must never reach one `X.in_(ids)` past SQLite's
32,766-parameter ceiling, so every such site runs its `IN` through `utils.chunked`
(or, for `_apply_bulk_filters`, caps the list with a 422).

The chunk size is shrunk to 2 with a spy instead of creating tens of thousands of
rows, as `test_start_id_chunking.py` does. Each site is posted five distinct ids plus
two repeats: the repeats must collapse (a chunk-spanning repeat would otherwise return
the row twice) and the chunk sizes must come out `[2, 2, 1]`.
"""
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import func, select

from backend.models import Image
from backend.models.detection import Detection
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job

CHUNKS = [2, 2, 1]


def _spy(monkeypatch, module) -> list[list[int]]:
    """Replace `module.chunked` with a size-2 wrapper that records each call's chunk sizes."""
    from backend.utils import chunked

    sizes: list[list[int]] = []

    def small(seq, size=2):
        out = list(chunked(seq, 2))
        sizes.append([len(c) for c in out])
        return out

    monkeypatch.setattr(module, "chunked", small)
    return sizes


async def _images(env, n: int = 5, **kw) -> tuple[dict, list[dict]]:
    ds = await env.create_dataset("d")
    imgs = [
        await upload_image(env, ds["id"], f"i{i}.png", png_bytes(color=(i * 30, 5, 5)), **kw)
        for i in range(n)
    ]
    return ds, imgs


def _with_repeats(ids: list[str]) -> list[str]:
    return ids + ids[:2]


async def _set(env, image_id: str, **cols) -> None:
    async with env.Session() as db:
        img = await db.get(Image, image_id)
        for k, v in cols.items():
            setattr(img, k, v)
        await db.commit()


# ---- quality: style similarity -------------------------------------------------

_DIM = 768


def _emb(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.astype(np.float16).tobytes()


def _layer_emb(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    m = rng.standard_normal((12, _DIM)).astype(np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    return m.astype(np.float16).tobytes()


def test_style_similarity_chunks_references_and_scope(tmp_path, monkeypatch):
    import backend.routers.quality as quality

    sizes = _spy(monkeypatch, quality)

    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs = await _images(env)
            for i, img in enumerate(imgs):
                await _set(env, img["id"], clip_embedding=_emb(i))
            ids = _with_repeats([i["id"] for i in imgs])
            r = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"], "reference_image_ids": ids,
                "image_ids": ids, "embedding_type": "clip",
            })
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 5
            assert r.json()["skipped"] == 0
    run(go())
    # The scope filter, then the reference lookup.
    assert sizes == [CHUNKS, CHUNKS]


def test_style_similarity_all_layers_paginates_each_chunk(tmp_path, monkeypatch):
    """The keyset paginator restarts per chunk, so a selection spanning chunks scores
    every selected image once and none outside it."""
    import backend.routers.quality as quality

    sizes = _spy(monkeypatch, quality)

    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs = await _images(env)
            for i, img in enumerate(imgs):
                await _set(env, img["id"], clip_embedding=_emb(i), dino_layer_embeddings=_layer_emb(i))
            ref = [imgs[0]["id"]]
            body = {"dataset_id": ds["id"], "reference_image_ids": ref, "embedding_type": "combined_all_layers"}

            chosen = [i["id"] for i in imgs[:3]]
            r = await env.client.post(f"{API}/quality/style-similarity",
                                      json={**body, "image_ids": _with_repeats(chosen)})
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 3 and r.json()["skipped"] == 0
            async with env.Session() as db:
                scored = (await db.execute(
                    select(Image.id).where(Image.style_similarity_score.isnot(None))
                )).scalars().all()
            assert set(scored) == set(chosen)

            # Empty / absent list keeps meaning "the whole dataset".
            r = await env.client.post(f"{API}/quality/style-similarity", json=body)
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 5
    run(go())
    assert [2, 1] in sizes  # the three-id selection


# ---- captioning pipeline --------------------------------------------------------

def test_caption_pipeline_chunks_scope_and_previous_captions(tmp_path, monkeypatch):
    import backend.routers.captioning as captioning
    from backend.tests.test_caption_start_scope import _seed

    sizes = _spy(monkeypatch, captioning)

    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch, n=5)
            ids = _with_repeats([i["id"] for i in imgs])
            r = await env.client.post(f"{API}/captioning/pipeline", json={
                "dataset_id": ds["id"], "image_ids": ids,
                "steps": [
                    {"model": model},
                    {"model": model, "custom_prompt": "Improve: {previous_caption}"},
                ],
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job
    run(go())
    # The scope query, then the second step's previous-caption load.
    assert sizes == [CHUNKS, CHUNKS]


# ---- caption_service ------------------------------------------------------------

def test_caption_bulk_edit_and_find_replace_chunk(tmp_path, monkeypatch):
    import backend.services.caption_service as cs

    sizes = _spy(monkeypatch, cs)

    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs = await _images(env)
            for img in imgs:
                await _set(env, img["id"], caption_text="a cat")
            ids = _with_repeats([i["id"] for i in imgs])
            r = await env.client.post(f"{API}/captions/dataset/{ds['id']}/bulk-edit", json={
                "operation": "append", "text": "x", "image_ids": ids,
            })
            assert r.status_code == 200, r.text
            assert r.json()["affected"] == 5

            r = await env.client.post(f"{API}/captions/dataset/{ds['id']}/find-replace", json={
                "find": "cat", "replace": "dog", "image_ids": ids,
            })
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 5

            # The two functions disagree on `[]` on purpose, and still do:
            # bulk-edit selects nothing, find-replace widens to the dataset.
            r = await env.client.post(f"{API}/captions/dataset/{ds['id']}/bulk-edit", json={
                "operation": "append", "text": "y", "image_ids": [],
            })
            assert r.json() == {"affected": 0, "skipped": 0}
            r = await env.client.post(f"{API}/captions/dataset/{ds['id']}/find-replace", json={
                "find": "dog", "replace": "fox", "image_ids": [],
            })
            assert r.json()["updated"] == 5
    run(go())
    # bulk-edit, find-replace, then bulk-edit's empty list (no chunks); find-replace's
    # empty list is falsy and never chunks.
    assert sizes == [CHUNKS, CHUNKS, []]


# ---- tag consolidation ----------------------------------------------------------

def test_subsume_chunks_explicit_ids(tmp_path, monkeypatch):
    import backend.services.tag_consolidation_service as tcs

    sizes = _spy(monkeypatch, tcs)

    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs = await _images(env)
            for img in imgs:
                await _set(env, img["id"], caption_text="cat, black cat")
            ids = _with_repeats([i["id"] for i in imgs])
            r = await env.client.post(f"{API}/tag-consolidation/dataset/{ds['id']}/subsume", json={
                "dry_run": True, "image_ids": ids,
            })
            assert r.status_code == 200, r.text
            assert r.json() == {"affected": 5, "skipped": 0}
            r = await env.client.post(f"{API}/tag-consolidation/dataset/{ds['id']}/subsume", json={
                "dry_run": False, "image_ids": []})
            assert r.json() == {"affected": 0, "skipped": 0}  # `[]` selects nothing
    run(go())
    assert sizes == [CHUNKS, []]


# ---- detection ------------------------------------------------------------------

async def _detections(env, image_ids: list[str], per_image: int = 1, label: str = "face") -> list[int]:
    async with env.Session() as db:
        rows = []
        for iid in image_ids:
            for _ in range(per_image):
                d = Detection(image_id=iid, label=label, bbox=[0.1, 0.1, 0.5, 0.5],
                              score=0.9, model="m", task="t")
                db.add(d)
                rows.append(d)
        await db.commit()
        return [d.id for d in rows]


def test_merge_detections_chunks_ids(tmp_path, monkeypatch):
    import backend.routers.detection as detection

    sizes = _spy(monkeypatch, detection)

    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs = await _images(env, n=1)
            det_ids = await _detections(env, [imgs[0]["id"]], per_image=5)
            r = await env.client.post(f"{API}/detection/merge", json={
                "detection_ids": _with_repeats(det_ids),
            })
            assert r.status_code == 200, r.text
            assert r.json()["model"] == "manual"
            async with env.Session() as db:
                n = (await db.execute(select(func.count()).select_from(Detection))).scalar_one()
            assert n == 1  # five merged into one
    run(go())
    assert sizes == [CHUNKS]


def test_bulk_delete_detections_chunks_dry_run_and_real(tmp_path, monkeypatch):
    import backend.routers.detection as detection

    sizes = _spy(monkeypatch, detection)

    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs = await _images(env)
            await _detections(env, [i["id"] for i in imgs], label="face")
            keep = await _detections(env, [imgs[0]["id"]], label="hand")
            body = {"dataset_id": ds["id"], "image_ids": _with_repeats([i["id"] for i in imgs]),
                    "labels": ["face"]}

            r = await env.client.post(f"{API}/detection/bulk-delete", json={**body, "dry_run": True})
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": 5, "dry_run": True}

            r = await env.client.post(f"{API}/detection/bulk-delete", json=body)
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": 5, "dry_run": False}
            async with env.Session() as db:
                left = (await db.execute(select(Detection.id))).scalars().all()
            assert left == keep  # the label filter still ANDs with every chunk
    run(go())
    assert sizes == [CHUNKS, CHUNKS]


# ---- comfy ----------------------------------------------------------------------

def test_comfy_row_id_lists_chunk(tmp_path, monkeypatch):
    import backend.routers.comfy as comfy
    from backend.models.comfy import ComfyRow
    from backend.tests.test_comfy_bulk_edit_regex_http import PROMPT_ALIAS, WORKFLOW

    sizes = _spy(monkeypatch, comfy)

    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("c")
            r = await env.client.post(f"{API}/comfy/plans", json={
                "dataset_id": ds["id"], "name": "p", "workflow_json": WORKFLOW,
                "pinned_params": [{"node_id": "6", "input": "text", "alias": PROMPT_ALIAS, "is_prompt": True}],
                "output_node_ids": ["9"],
            })
            plan_id = r.json()["id"]
            row_ids = []
            for i in range(5):
                r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows",
                                          json={"values": {PROMPT_ALIAS: f"red {i}"}})
                row_ids.append(r.json()["id"])
            ids = _with_repeats(row_ids)

            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows/bulk-edit", json={
                "operation": "find_replace", "text": "red", "replacement": "blue", "row_ids": ids,
            })
            assert r.status_code == 200, r.text
            assert r.json() == {"affected": 5, "skipped": 0}

            async with env.Session() as db:
                for row in (await db.execute(select(ComfyRow))).scalars().all():
                    row.status = "failed"
                await db.commit()
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows/reset", json={"row_ids": ids})
            assert r.status_code == 200, r.text
            assert r.json() == {"reset": 5}
    run(go())
    assert sizes == [CHUNKS, CHUNKS]


def test_comfy_run_chunks_row_ids(tmp_path, monkeypatch):
    import backend.routers.comfy as comfy
    from backend.tests.fake_comfy import FakeComfy
    from backend.tests.test_comfy_run_body import _add_row, _rows, _setup, _workflow

    sizes = _spy(monkeypatch, comfy)

    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])
            rows = [await _add_row(env, plan_id, f"knight {i}") for i in range(5)]
            ids = _with_repeats([r["id"] for r in rows])
            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id, "row_ids": ids})
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job
            done = await _rows(env, plan_id)
            assert [x["status"] for x in done] == ["completed"] * 5
    run(go())
    # The start check, then the job body's row load.
    assert sizes.count(CHUNKS) >= 2


# ---- images ---------------------------------------------------------------------

def test_batch_reorder_chunks_and_still_rejects_foreign_ids(tmp_path, monkeypatch):
    import backend.routers.images as images

    sizes = _spy(monkeypatch, images)

    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs = await _images(env)
            other = await env.create_dataset("other")
            stranger = await upload_image(env, other["id"], "z.png")
            ups = [{"id": i["id"], "sort_order": n} for n, i in enumerate(imgs)]
            # Repeats are deduped before chunking, but still a 400, as they were
            # when one `IN` collapsed them and the count fell short.
            r = await env.client.patch(f"{API}/images/batch/reorder",
                                       json={"dataset_id": ds["id"], "updates": ups + ups[:2]})
            assert r.status_code == 400, r.text

            r = await env.client.patch(f"{API}/images/batch/reorder",
                                       json={"dataset_id": ds["id"], "updates": ups})
            assert r.status_code == 200, r.text

            r = await env.client.patch(f"{API}/images/batch/reorder", json={
                "dataset_id": ds["id"],
                "updates": ups[:4] + [{"id": stranger["id"], "sort_order": 9}],
            })
            assert r.status_code == 400
            assert "do not belong" in r.json()["detail"]
    run(go())
    assert sizes[0] == CHUNKS


def test_bulk_filters_cap_the_id_list_with_a_422(tmp_path, monkeypatch):
    import backend.routers.images as images

    monkeypatch.setattr(images, "SELECT_ALL_ID_CAP", 4)

    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs = await _images(env)
            ids = [i["id"] for i in imgs]
            r = await env.client.post(f"{API}/images/bulk-count", json={"dataset_id": ds["id"], "image_ids": ids})
            assert r.status_code == 422, r.text
            assert "Too many" in r.json()["detail"]

            # Repeats collapse before the count: four distinct ids plus repeats pass.
            r = await env.client.post(f"{API}/images/bulk-count", json={
                "dataset_id": ds["id"], "image_ids": _with_repeats(ids[:4]),
            })
            assert r.status_code == 200, r.text
    run(go())


def test_bulk_rename_over_a_whole_dataset(tmp_path):
    """No ids: the batch is every row, which the old `~Image.id.in_(batch_ids)` bound."""
    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs = await _images(env)
            r = await env.client.post(f"{API}/images/bulk-rename", json={
                "dataset_id": ds["id"], "new_stem": "pic",
            })
            assert r.status_code == 200, r.text
            assert r.json()["affected"] == 5
            listing = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            names = [i["filename"] for i in listing]
            assert len(set(names)) == 5 and all(n.startswith("pic") for n in names)

            # A subset must still steer around the names of the rows it leaves alone.
            subset = [i["id"] for i in listing[:2]]
            r = await env.client.post(f"{API}/images/bulk-rename", json={
                "dataset_id": ds["id"], "new_stem": "pic", "image_ids": subset,
            })
            assert r.status_code == 200, r.text
            names = [i["filename"] for i in (await env.client.get(
                f"{API}/images/", params={"dataset_id": ds["id"]})).json()]
            assert len(set(names)) == 5
    run(go())


# ---- export ---------------------------------------------------------------------

def test_export_with_ids_keeps_sort_order_nulls_last_then_created_at(tmp_path, monkeypatch):
    import backend.services.export_service as export_service

    sizes = _spy(monkeypatch, export_service)
    # (sort_order, minutes after the epoch) per image; the expected order is
    # sort_order ascending, NULLs last, ties and NULLs by created_at.
    spec = [(None, 5), (3, 1), (1, 4), (None, 2), (3, 0)]
    expected = [2, 4, 1, 3, 0]

    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ds_dir = env.datasets_dir / ds["id"]
            (ds_dir / "images").mkdir(parents=True, exist_ok=True)
            base = datetime(2024, 1, 1)
            ids = []
            async with env.Session() as db:
                for i, (so, minutes) in enumerate(spec):
                    name = f"same.{['png', 'jpg', 'webp', 'bmp', 'gif'][i]}"
                    fp = ds_dir / "images" / name
                    fp.write_bytes(b"DATA" + bytes([i]))
                    img = Image(
                        dataset_id=ds["id"], filename=name, original_filename=name, subfolder="",
                        file_path=str(fp), thumbnail_path=str(ds_dir / "thumbnails" / f"{name}.webp"),
                        caption_text=f"cap{i}", sort_order=so, created_at=base + timedelta(minutes=minutes),
                    )
                    db.add(img)
                    await db.flush()
                    ids.append(img.id)
                await db.commit()

            out = tmp_path / "out"
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(out), "image_ids": _with_repeats(ids),
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["exported"] == 5

            # Every source shares the stem `same`, so the export numbers them in the
            # order it processed them (same, same_001, ...); the extension says which.
            exts = ["png", "jpg", "webp", "bmp", "gif"]
            got = []
            for stem in ["same", "same_001", "same_002", "same_003", "same_004"]:
                [written] = (out / "images").glob(f"{stem}.*")
                got.append(exts.index(written.suffix[1:]))
            assert got == expected
    run(go())
    assert CHUNKS in sizes
