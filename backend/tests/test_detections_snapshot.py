"""A version snapshot saves an image's detections, and a restore brings them back (GPT-026).

Detection geometry is normalized to the pixels it was measured on, so it is a
qualifier of those pixels, exactly like the scores. A replace-mode crop remaps
every box into the cropped frame; a restore that then swapped the original pixels
back left the cropped frame's boxes over the uncropped picture. Now:

- `create_snapshot` records each image's detections on
  `VersionImageState.detections` (canonical, sorted, `[]` for none);
- `restore_snapshot` replaces the image's detections with the snapshot's at the
  end of Pass 2c, keyed on the row the plan writes (`p.img.id`);
- a state with `detections IS NULL` predates the mirror and leaves the image's
  detections alone;
- a row whose content could not be written gets its pre-restore detections back
  in Pass 3b's reconcile, so its geometry stays in the frame of its pixels;
- the diff compares them (only when both sides recorded them) and renders each
  side as a summary string, never the polygons.

Dataset duplication still does not carry detections — deliberately unchanged.
"""
import ast
import inspect
import json
import shutil
import textwrap
from pathlib import Path

from PIL import Image as PILImage
from sqlalchemy import select, update

from backend.models.detection import Detection
from backend.models.image import Image
from backend.models.label import ImageLabel, Label
from backend.models.versioning import VersionImageState
from backend.services import version_service
from backend.services.detection_service import (
    canonical_detections, deserialize_detection, serialize_detection,
)
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _mode(env, mode):
    r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": mode})
    assert r.status_code == 200, r.text


async def _snapshot(env, ds_id, name):
    async with env.Session() as db:
        return (await version_service.create_snapshot(db, ds_id, name, "")).id


async def _restore(env, ds_id, vid, **kw):
    kw.setdefault("pre_restore_snapshot", False)
    async with env.Session() as db:
        return await version_service.restore_snapshot(db, ds_id, vid, **kw)


async def _crop(env, img_id, x, y, w, h):
    r = await env.client.post(
        f"{API}/images/{img_id}/crop",
        json={"x": x, "y": y, "width": w, "height": h, "replace": True},
    )
    assert r.status_code == 200, r.text


async def _add(env, img_id, label="face", bbox=(0.2, 0.2, 0.4, 0.4), mask=None, model="m"):
    async with env.Session() as db:
        db.add(Detection(
            image_id=img_id, label=label, bbox=list(bbox), mask=mask,
            score=0.9, model=model, task="t",
        ))
        await db.commit()


async def _dets(env, img_id) -> list[Detection]:
    async with env.Session() as db:
        return list((await db.execute(
            select(Detection).where(Detection.image_id == img_id).order_by(Detection.label)
        )).scalars().all())


def _r(b):
    return [round(v, 3) for v in b]


def _size(p):
    with PILImage.open(p) as im:
        return im.size


async def _cropped(env, mode="manual"):
    """100×80 with one face at [0.2,0.2,0.4,0.4], snapshotted, then cropped."""
    await _mode(env, mode)
    ds = await env.create_dataset("d")
    img = await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80)))
    await _add(env, img["id"])
    vid = await _snapshot(env, ds["id"], "orig")
    await _crop(env, img["id"], 10, 8, 50, 40)
    return ds["id"], img["id"], vid


# ── the probe, inverted ──────────────────────────────────────────────────────


def test_a_restore_brings_the_snapshots_detection_geometry_back(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds_id, img_id, vid = await _cropped(env)
            (after_crop,) = await _dets(env, img_id)
            # The crop remapped it into the 50×40 frame.
            assert _r(after_crop.bbox) == [0.2, 0.2, 0.6, 0.6]

            res = await _restore(env, ds_id, vid)
            assert res["files_failed"] == 0 and res["files_unavailable"] == 0

            async with env.Session() as db:
                path = (await db.get(Image, img_id)).file_path
            assert _size(path) == (100, 80)
            (det,) = await _dets(env, img_id)
            assert _r(det.bbox) == [0.2, 0.2, 0.4, 0.4]
            # Derived, recomputed by the ORM listeners on construction.
            assert det.mask_area is not None and abs(det.mask_area - 0.04) < 1e-6
            assert (det.label, det.model, det.task, det.score) == ("face", "m", "t", 0.9)
    run(scenario())


def test_restore_replaces_rather_than_merges(tmp_path):
    """A detection added after the snapshot is geometry over pixels the restore
    took away — it goes, exactly as a label added after the snapshot does."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            await _add(env, img, label="face")
            vid = await _snapshot(env, ds["id"], "v1")
            await _add(env, img, label="hand", bbox=(0.5, 0.5, 0.9, 0.9))
            assert [d.label for d in await _dets(env, img)] == ["face", "hand"]

            await _restore(env, ds["id"], vid)
            assert [d.label for d in await _dets(env, img)] == ["face"]
    run(scenario())


def test_an_unchanged_image_keeps_its_detection_rows(tmp_path):
    """Equal lists are skipped, so a no-op restore does not churn detection ids."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            await _add(env, img)
            vid = await _snapshot(env, ds["id"], "v1")
            before = [d.id for d in await _dets(env, img)]
            await _restore(env, ds["id"], vid)
            assert [d.id for d in await _dets(env, img)] == before
    run(scenario())


def test_a_state_that_never_recorded_detections_leaves_them_alone(tmp_path):
    """NULL is "not recorded" — a row older than the mirror column. Restoring it
    must not read as "no detections" and wipe them."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            vid = await _snapshot(env, ds["id"], "v1")
            async with env.Session() as db:
                await db.execute(
                    update(VersionImageState)
                    .where(VersionImageState.version_id == vid)
                    .values(detections=None)
                )
                await db.commit()
            await _add(env, img, label="face")

            await _restore(env, ds["id"], vid)
            assert [d.label for d in await _dets(env, img)] == ["face"]
    run(scenario())


def test_keep_mode_extras_keep_their_detections(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            first = (await upload_image(env, ds["id"], "a.png"))["id"]
            vid = await _snapshot(env, ds["id"], "v1")
            extra = (await upload_image(env, ds["id"], "b.png"))["id"]
            await _add(env, extra, label="hand")

            await _restore(env, ds["id"], vid, handle_extra_images="keep")
            assert await _dets(env, first) == []
            assert [d.label for d in await _dets(env, extra)] == ["hand"]
    run(scenario())


# ── the reconcile path ───────────────────────────────────────────────────────


def _fail_object_copies(monkeypatch):
    """Break the object → temp copy, as test_restore_outcome_http does."""
    real = shutil.copy2
    hits = {"n": 0}

    def selective(src, dst, *a, **k):
        if "/.versions/objects/" in Path(src).as_posix() and Path(dst).name.startswith(".tmp-"):
            hits["n"] += 1
            Path(dst).write_bytes(b"partial")
            raise OSError("injected object copy failure")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(version_service.shutil, "copy2", selective)
    return hits


def test_a_failed_content_write_keeps_the_pre_restore_detections(tmp_path, monkeypatch):
    """The cropped pixels survive a failed write, so must the cropped frame's
    geometry — the snapshot's boxes over them would be wrong the other way."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds_id, img_id, vid = await _cropped(env)
            hits = _fail_object_copies(monkeypatch)
            res = await _restore(env, ds_id, vid)
            assert hits["n"] == 1 and res["files_failed"] == 1

            async with env.Session() as db:
                row = await db.get(Image, img_id)
            assert _size(row.file_path) == (50, 40)
            (det,) = await _dets(env, img_id)
            assert _r(det.bbox) == [0.2, 0.2, 0.6, 0.6]
            assert abs(det.mask_area - 0.16) < 1e-6
    run(scenario())


def test_a_failed_re_creation_leaves_no_detections(tmp_path, monkeypatch):
    """Foreign keys OFF on purpose: with them on, the `images` cascade would hide
    a missing explicit delete, and the explicit delete is what this pins."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=False) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80)))
            await _add(env, img["id"])
            vid = await _snapshot(env, ds["id"], "orig")
            assert (await env.client.delete(f"{API}/images/{img['id']}")).status_code == 204

            _fail_object_copies(monkeypatch)
            res = await _restore(env, ds["id"], vid)
            assert res["images_re_created"] == 0
            async with env.Session() as db:
                assert await db.get(Image, img["id"]) is None
            assert await _dets(env, img["id"]) == []
    run(scenario())


def test_remove_mode_extras_take_their_detections_with_them(tmp_path):
    """An extra deleted by a remove-mode restore leaves no detection or label rows.
    Foreign keys OFF on purpose, as above: the cascade would hide a missing delete."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=False) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "kept.png")
            vid = await _snapshot(env, ds["id"], "v1")
            extra = (await upload_image(env, ds["id"], "extra.png"))["id"]
            await _add(env, extra)
            async with env.Session() as db:
                label = Label(name="tagged")
                db.add(label)
                await db.flush()
                db.add(ImageLabel(image_id=extra, label_id=label.id))
                await db.commit()

            res = await _restore(env, ds["id"], vid, handle_extra_images="remove")
            assert res["images_removed"] == 1
            assert await _dets(env, extra) == []
            async with env.Session() as db:
                left = (await db.execute(
                    select(ImageLabel).where(ImageLabel.image_id == extra)
                )).scalars().all()
            assert left == []
    run(scenario())


# ── fork / adoption ──────────────────────────────────────────────────────────


def test_a_forked_row_gets_the_detections_on_the_fork(tmp_path):
    """Pass 0b mints a fresh id for an image since moved to another dataset; the
    detections go on that fork, and the moved original keeps its own."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            src = await env.create_dataset("src")
            other = await env.create_dataset("other")
            img = (await upload_image(env, src["id"], "a.png"))["id"]
            await _add(env, img, label="face")
            vid = await _snapshot(env, src["id"], "v1")

            r = await env.client.post(
                f"{API}/images/batch/move-dataset",
                json={"image_ids": [img], "target_dataset_id": other["id"], "target_subfolder": ""},
            )
            assert r.status_code == 200, r.text
            await _add(env, img, label="hand", bbox=(0.5, 0.5, 0.9, 0.9))

            await _restore(env, src["id"], vid)

            listing = (await env.client.get(f"{API}/images/", params={"dataset_id": src["id"]})).json()
            assert len(listing) == 1
            fork = listing[0]["id"]
            assert fork != img
            assert [d.label for d in await _dets(env, fork)] == ["face"]
            assert [d.label for d in await _dets(env, img)] == ["face", "hand"]
    run(scenario())


# ── the safety snapshot ──────────────────────────────────────────────────────


def test_the_pre_restore_snapshot_records_current_detections(tmp_path):
    """The safety net is what makes a restore undoable, so it must hold the
    post-crop geometry — restoring it brings the cropped frame's boxes back."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds_id, img_id, vid = await _cropped(env)
            res = await _restore(env, ds_id, vid, pre_restore_snapshot=True)
            safety = res["pre_restore_version_id"]
            assert safety
            (det,) = await _dets(env, img_id)
            assert _r(det.bbox) == [0.2, 0.2, 0.4, 0.4]

            async with env.Session() as db:
                st = (await db.execute(
                    select(VersionImageState).where(VersionImageState.version_id == safety)
                )).scalars().one()
            assert [_r(d["bbox"]) for d in st.detections] == [[0.2, 0.2, 0.6, 0.6]]

            await _restore(env, ds_id, safety)
            async with env.Session() as db:
                path = (await db.get(Image, img_id)).file_path
            assert _size(path) == (50, 40)
            (det,) = await _dets(env, img_id)
            assert _r(det.bbox) == [0.2, 0.2, 0.6, 0.6]
    run(scenario())


# ── the mirror itself ────────────────────────────────────────────────────────


def test_the_snapshot_stores_detections_sorted_and_never_null(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            a = (await upload_image(env, ds["id"], "a.png"))["id"]
            b = (await upload_image(env, ds["id"], "b.png"))["id"]
            mask = json.dumps([[[0.1, 0.1], [0.3, 0.1], [0.3, 0.3]]])
            await _add(env, a, label="zebra", model="b")
            await _add(env, a, label="hand", model="a", mask=mask)
            await _add(env, a, label="face", model="b")
            vid = await _snapshot(env, ds["id"], "v1")

            async with env.Session() as db:
                states = {s.image_id: s for s in (await db.execute(
                    select(VersionImageState).where(VersionImageState.version_id == vid)
                )).scalars().all()}
            assert states[b].detections == []
            stored = states[a].detections
            assert [(d["model"], d["label"]) for d in stored] == [("a", "hand"), ("b", "face"), ("b", "zebra")]
            assert stored == canonical_detections(stored)
            assert set(stored[0]) == {"label", "bbox", "score", "model", "task", "mask", "detected_at"}
            assert stored[0]["mask"] == mask
    run(scenario())


def test_serialize_round_trips_and_recomputes_mask_area():
    from datetime import datetime

    src = Detection(
        image_id="x", label="face", mask=None, bbox=[0.0, 0.0, 0.5, 0.5],
        score=None, model="m", task="t", detected_at=datetime(2026, 1, 2, 3, 4, 5, 6),
    )
    d = serialize_detection(src)
    back = deserialize_detection("y", d)
    assert serialize_detection(back) == d
    assert back.image_id == "y"
    assert abs(back.mask_area - 0.25) < 1e-9


# ── the diff ─────────────────────────────────────────────────────────────────


async def _diff(env, ds_id, a, b):
    r = await env.client.get(f"{API}/datasets/{ds_id}/versions/diff", params={"v1": a, "v2": b})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_diff_summarises_detections_by_label(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            a = await _snapshot(env, ds["id"], "v1")
            await _add(env, img, label="face")
            await _add(env, img, label="face", bbox=(0.5, 0.5, 0.6, 0.6))
            await _add(env, img, label="hand")
            b = await _snapshot(env, ds["id"], "v2")

            diff = await _diff(env, ds["id"], a, b)
            assert diff["summary"]["modified"] == 1
            change = diff["modified"][0]["changes"]["detections"]
            assert change == {"from": "none", "to": "3: face×2, hand"}
            # Never the polygons / boxes.
            assert "bbox" not in json.dumps(diff)
    run(scenario())


def test_a_geometry_only_change_still_shows_in_the_diff(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            await _add(env, img, label="face")
            a = await _snapshot(env, ds["id"], "v1")
            async with env.Session() as db:
                det = (await db.execute(select(Detection).where(Detection.image_id == img))).scalars().one()
                det.bbox = [0.1, 0.1, 0.2, 0.2]
                await db.commit()
            b = await _snapshot(env, ds["id"], "v2")

            change = (await _diff(env, ds["id"], a, b))["modified"][0]["changes"]["detections"]
            assert change == {"from": "1: face", "to": "1: face (geometry changed)"}
    run(scenario())


def test_null_against_a_list_is_no_diff_row(tmp_path):
    """A side that predates the mirror recorded nothing to compare."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            a = await _snapshot(env, ds["id"], "v1")
            async with env.Session() as db:
                await db.execute(
                    update(VersionImageState)
                    .where(VersionImageState.version_id == a)
                    .values(detections=None)
                )
                await db.commit()
            await _add(env, img, label="face")
            b = await _snapshot(env, ds["id"], "v2")

            diff = await _diff(env, ds["id"], a, b)
            assert diff["summary"]["modified"] == 0
    run(scenario())


def test_the_mirror_column_is_nullable_and_diffed():
    col = VersionImageState.__table__.c.detections
    # Nullable with no server default, unlike label_ids: NULL is "not recorded".
    assert col.nullable is True and col.server_default is None
    assert "detections" in {c.key for c in version_service._DIFF_COLS}
    assert "detections" in version_service._DIFF_COMPARE_FIELDS


# ── structural ───────────────────────────────────────────────────────────────


def _constructs(func) -> set[str]:
    """Call names and kwarg names in `func`'s source, read out of the AST — the
    same approach as `test_labels_survive_rebuild_paths._label_constructs`, whose
    guard cannot see this table: it looks for label-shaped constructs only, and
    `test_video_lineage_mirrors` walks `Image`'s columns, which detections are not."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    found: set[str] = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        if name:
            found.add(name)
        for kw in n.keywords:
            if kw.arg:
                found.add(f"{kw.arg}=")
    return found


def test_snapshot_and_restore_keep_their_detection_handling():
    """Deleting the detection handling from either side fails here rather than
    in a restore that leaves a crop's boxes over the uncropped picture."""
    snap = _constructs(version_service.create_snapshot)
    assert "detections=" in snap and "detections_by_image" in snap, (
        "create_snapshot no longer records detections on VersionImageState"
    )
    restore = _constructs(version_service.restore_snapshot)
    assert "set_detections" in restore, "restore_snapshot no longer writes detections back"
    assert "detections_by_image" in restore, (
        "restore_snapshot no longer captures pre-restore detections for its reconcile"
    )
