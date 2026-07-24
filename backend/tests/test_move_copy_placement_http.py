"""Request-level regression tests for cross-dataset move/copy file placement and
the two DB-vs-filesystem ordering invariants (CLAUDE.md "Key invariants").

`test_provenance_http.py` drives the same endpoints but asserts provenance columns
only — never where the image file, its `.webp` thumbnail, or its `.txt` sidecar
landed, and never what survives a mid-batch filesystem fault. These pin all of that:

- B1/B2: happy-path placement of file + thumbnail + sidecar in the target (and, for
  copy, that the source is left byte-identical).
- B3 (commit-before-FS): a fault mid-rename leaves the DB holding the full intended
  state with zero bytes destroyed — the documented degraded state where a file is
  stranded at its old path while its row points forward.
- B4 (FS-before-commit): a fault mid-copy commits nothing; the source is untouched.

Nothing keys on which id receives `image.png` vs `image_001.png` — that ties on
`created_at`. Every assertion resolves per row via DB `file_path` + unique bytes,
and opens a fresh `env.Session()` after the request.
"""
from pathlib import Path

import backend.routers.images as images_router
from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def expect_unhandled(coro, exc_type=RuntimeError):
    """An injected fault surfaces either as a raised exception (ASGITransport's
    `raise_app_exceptions=True` today) or, if that ever changes, a 5xx response."""
    try:
        r = await coro
    except exc_type:
        return
    assert r.status_code >= 500, r.text


def _dirs(dataset: dict) -> tuple[Path, Path]:
    root = Path(dataset["folder_path"])
    return root / "images", root / "thumbnails"


async def _rows(env, dataset_id: str) -> list[Image]:
    async with env.Session() as db:
        return list((await db.execute(
            select(Image).where(Image.dataset_id == dataset_id)
        )).scalars().all())


async def _snapshot(env, image_ids: list[str]) -> dict[str, tuple[str, bytes]]:
    """Map id -> (original file_path, original bytes) before a move/copy."""
    out: dict[str, tuple[str, bytes]] = {}
    async with env.Session() as db:
        for iid in image_ids:
            row = await db.get(Image, iid)
            out[iid] = (row.file_path, Path(row.file_path).read_bytes())
    return out


def _png_count(d: Path) -> int:
    return len(list(d.glob("*.png")))


def test_move_dataset_places_files_thumbs_sidecars(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dst = await env.create_dataset("dst")
            src_images, src_thumbs = _dirs(src)
            dst_images, dst_thumbs = _dirs(dst)

            a = await upload_image(env, src["id"], "a.png", png_bytes((1, 2, 3)))
            b = await upload_image(env, src["id"], "b.png", png_bytes((4, 5, 6)))
            r = await env.client.put(
                f"{API}/captions/image/{a['id']}", json={"caption_text": "cap one"}
            )
            assert r.status_code == 200, r.text
            orig = await _snapshot(env, [a["id"], b["id"]])

            r = await env.client.post(f"{API}/images/batch/move-dataset", json={
                "image_ids": [a["id"], b["id"]],
                "target_dataset_id": dst["id"],
            })
            assert r.status_code == 200, r.text
            assert r.json()["moved"] == 2

            rows = await _rows(env, dst["id"])
            assert len(rows) == 2
            assert {r.filename for r in rows} == {"image.png", "image_001.png"}
            for row in rows:
                assert row.dataset_id == dst["id"]
                assert Path(row.file_path).parent == dst_images
                assert Path(row.file_path).read_bytes() == orig[row.id][1]

            # Captioned row's sidecar followed it; the other has none.
            for row in rows:
                sidecar = Path(row.file_path).with_suffix(".txt")
                if row.id == a["id"]:
                    assert sidecar.read_text(encoding="utf-8") == "cap one"
                else:
                    assert not sidecar.exists()

            # Source emptied of image and sidecar files; nothing left behind.
            assert _png_count(src_images) == 0
            assert list(src_images.glob("*.txt")) == []
            # Thumbnails: 2 in dst matching the row stems, none in src.
            dst_thumb_stems = {p.stem for p in dst_thumbs.glob("*.webp")}
            assert dst_thumb_stems == {Path(r.file_path).stem for r in rows}
            assert list(src_thumbs.glob("*.webp")) == []
            # Global count of image files is conserved.
            assert _png_count(dst_images) + _png_count(src_images) == 2

    run(scenario())


def test_copy_dataset_places_files_thumbs_sidecars(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dst = await env.create_dataset("dst")
            src_images, src_thumbs = _dirs(src)
            dst_images, dst_thumbs = _dirs(dst)

            a = await upload_image(env, src["id"], "a.png", png_bytes((1, 2, 3)))
            b = await upload_image(env, src["id"], "b.png", png_bytes((4, 5, 6)))
            r = await env.client.put(
                f"{API}/captions/image/{a['id']}", json={"caption_text": "cap one"}
            )
            assert r.status_code == 200, r.text
            orig = await _snapshot(env, [a["id"], b["id"]])

            r = await env.client.post(f"{API}/images/batch/copy-dataset", json={
                "image_ids": [a["id"], b["id"]],
                "target_dataset_id": dst["id"],
            })
            assert r.status_code == 200, r.text
            assert r.json()["copied"] == 2

            # Source rows and files untouched.
            src_rows = await _rows(env, src["id"])
            assert {r.id for r in src_rows} == {a["id"], b["id"]}
            for row in src_rows:
                assert Path(row.file_path).read_bytes() == orig[row.id][1]

            # Two brand-new rows in dst (new ids), matched to source by bytes.
            dst_rows = await _rows(env, dst["id"])
            assert len(dst_rows) == 2
            assert {r.id for r in dst_rows}.isdisjoint({a["id"], b["id"]})
            src_bytes = {orig[a["id"]][1], orig[b["id"]][1]}
            assert {Path(r.file_path).read_bytes() for r in dst_rows} == src_bytes

            # Sidecar "cap one" present in BOTH dirs (source kept, copy made).
            src_sidecars = {p.read_text(encoding="utf-8") for p in src_images.glob("*.txt")}
            dst_sidecars = {p.read_text(encoding="utf-8") for p in dst_images.glob("*.txt")}
            assert src_sidecars == {"cap one"}
            assert dst_sidecars == {"cap one"}

            # Thumbnails: dst gains 2, src still has 2.
            assert len(list(dst_thumbs.glob("*.webp"))) == 2
            assert len(list(src_thumbs.glob("*.webp"))) == 2

    run(scenario())


def _fault_on_second_call(real):
    """Return a wrapper that calls `real` once, then raises on the next call —
    style of test_versioning_restore.py's mid-batch fault injection."""
    calls = {"n": 0}

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("disk on fire")
        return real(*args, **kwargs)

    return wrapper


def test_move_fs_failure_mid_batch_db_holds_final_state(tmp_path, monkeypatch):
    """Ordering invariant #1 (commit-before-FS): a mid-rename fault leaves the DB
    with the full intended state and zero bytes destroyed. The stranded file whose
    row already points forward is the documented degraded state."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dst = await env.create_dataset("dst")
            src_images, _ = _dirs(src)
            dst_images, _ = _dirs(dst)

            a = await upload_image(env, src["id"], "a.png", png_bytes((1, 2, 3)))
            b = await upload_image(env, src["id"], "b.png", png_bytes((4, 5, 6)))
            orig = await _snapshot(env, [a["id"], b["id"]])

            monkeypatch.setattr(
                images_router, "rename_with_sidecar",
                _fault_on_second_call(images_router.rename_with_sidecar),
            )
            await expect_unhandled(env.client.post(
                f"{API}/images/batch/move-dataset",
                json={"image_ids": [a["id"], b["id"]], "target_dataset_id": dst["id"]},
            ))

            # Commit landed the full intended state before any FS work.
            rows = await _rows(env, dst["id"])
            assert len(rows) == 2
            for row in rows:
                assert row.dataset_id == dst["id"]
                assert Path(row.file_path).parent == dst_images

            # Exactly one file moved, one stranded at its old path — nothing lost.
            assert _png_count(dst_images) == 1
            assert _png_count(src_images) == 1
            for row in rows:
                new_path = Path(row.file_path)
                old_path = Path(orig[row.id][0])
                found = new_path if new_path.exists() else old_path
                assert found.read_bytes() == orig[row.id][1]

    run(scenario())


def test_copy_fs_failure_mid_batch_commits_nothing(tmp_path, monkeypatch):
    """Ordering invariant #2 (FS-before-commit): a mid-copy fault reaches no
    commit, so the staged destination rows are discarded and the source is
    untouched. A single orphan file in dst is acceptable (not asserted absent)."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dst = await env.create_dataset("dst")
            src_images, _ = _dirs(src)

            a = await upload_image(env, src["id"], "a.png", png_bytes((1, 2, 3)))
            b = await upload_image(env, src["id"], "b.png", png_bytes((4, 5, 6)))
            orig = await _snapshot(env, [a["id"], b["id"]])

            monkeypatch.setattr(
                images_router, "copy_with_sidecar",
                _fault_on_second_call(images_router.copy_with_sidecar),
            )
            await expect_unhandled(env.client.post(
                f"{API}/images/batch/copy-dataset",
                json={"image_ids": [a["id"], b["id"]], "target_dataset_id": dst["id"]},
            ))

            # Nothing committed to the target.
            assert await _rows(env, dst["id"]) == []
            # Source rows and files byte-identical.
            src_rows = await _rows(env, src["id"])
            assert {r.id for r in src_rows} == {a["id"], b["id"]}
            for row in src_rows:
                assert Path(row.file_path).read_bytes() == orig[row.id][1]
            assert _png_count(src_images) == 2

    run(scenario())
