"""Folder import and rescan, several files at once and one decode per file (M10 T3).

The parallel loops must give what the one-at-a-time loops gave: the same rows,
names, thumbnails and provenance, in the same order, and the same `failed`
list. Each parity test runs the same input with the window forced to 1 (the
serial behaviour) and to 4, and compares.
"""

import io
import json
import struct
import threading
import time
import zlib
from pathlib import Path

import pytest
from PIL import Image as PilImage
from PIL import PngImagePlugin
from sqlalchemy import select, text
from sqlalchemy.orm import undefer

from backend.models import Image
from backend.services import dataset_service, image_service
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.workers.job_queue import job_queue

ROW_FIELDS = (
    "filename", "original_filename", "subfolder", "width", "height", "format",
    "file_size_bytes", "phash", "caption_text", "captioned_by", "generation_metadata",
    "source_name", "source_url", "license", "attribution", "source_meta",
)


def _encoded(img: PilImage.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def _rotated_jpeg() -> bytes:
    """A 40x20 JPEG whose EXIF says rotate 90°, so `exif_transpose` matters."""
    exif = PilImage.Exif()
    exif[0x0112] = 6
    return _encoded(PilImage.new("RGB", (40, 20), (200, 30, 30)), "JPEG", exif=exif)


def _a1111_png() -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a red fox\nNegative prompt: blur\nSteps: 20, Sampler: Euler a")
    return _encoded(PilImage.new("RGB", (24, 24), (220, 120, 40)), "PNG", pnginfo=info)


def _gradient_png(k: int) -> bytes:
    """A distinct picture per k, so every pHash and thumbnail differs."""
    img = PilImage.linear_gradient("L").resize((48 + k, 40)).convert("RGB")
    return _encoded(img.rotate(k * 7), "PNG")


def _write_fixture(folder: Path, nested: bool) -> None:
    """Every kind of file the ingest path treats differently. Written in a fixed
    order, so two copies of it walk in the same order on every filesystem."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rot.jpg").write_bytes(_rotated_jpeg())
    (folder / "pal.png").write_bytes(
        _encoded(PilImage.new("P", (30, 30), 3), "PNG")
    )
    (folder / "rgba.png").write_bytes(
        _encoded(PilImage.new("RGBA", (30, 30), (10, 200, 10, 120)), "PNG")
    )
    (folder / "gen.png").write_bytes(_a1111_png())
    (folder / "cap.png").write_bytes(png_bytes((1, 2, 3), (20, 20)))
    (folder / "cap.txt").write_text("a captioned image", encoding="utf-8")
    (folder / "prov.png").write_bytes(png_bytes((4, 5, 6), (20, 20)))
    (folder / "prov.png.json").write_text(json.dumps({
        "category": "examplebooru", "post_url": "https://example.com/p/1",
        "artist": "Someone", "license": "CC-BY-4.0",
    }), encoding="utf-8")
    # A stem pair: one keeps the name, the other becomes `same_001`.
    (folder / "same.png").write_bytes(png_bytes((7, 8, 9), (20, 20)))
    (folder / "same.jpg").write_bytes(_encoded(PilImage.new("RGB", (20, 20), (9, 8, 7)), "JPEG"))
    (folder / "broken.jpg").write_bytes(b"not an image at all")
    # Enough plain files that the window is full for most of the run.
    for k in range(24):
        (folder / f"bulk_{k:02d}.png").write_bytes(_gradient_png(k))
    if nested:
        (folder / "sub" / "deep").mkdir(parents=True)
        (folder / "sub" / "n1.png").write_bytes(_gradient_png(40))
        (folder / "sub" / "deep" / "n2.png").write_bytes(_gradient_png(41))


async def _rows(env, dataset_id: str) -> list[Image]:
    """The dataset's images in insertion order."""
    async with env.Session() as db:
        res = await db.execute(
            select(Image)
            .options(undefer(Image.source_meta))
            .where(Image.dataset_id == dataset_id)
            .order_by(text("images.rowid"))
        )
        return list(res.scalars().all())


def _snapshot(rows: list[Image]) -> list[tuple]:
    return [
        tuple(getattr(r, f) for f in ROW_FIELDS)
        + (Path(r.file_path).name, Path(r.thumbnail_path).read_bytes())
        for r in rows
    ]


def _force_workers(monkeypatch, n: int) -> None:
    monkeypatch.setattr(dataset_service, "_import_workers", lambda: n)


# --- one decode -------------------------------------------------------------


def _png_chunk(ctype: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data))


def _after_pixels(png: bytes, *chunks: bytes) -> bytes:
    """`png` with `chunks` placed after the pixel data, just before IEND.

    Pillow writes every chunk before the pixels, but other tools don't, and a
    chunk there is only seen once the image is loaded: the case where reading
    the metadata from an already-loaded image could change what it finds.
    """
    iend = png.rindex(b"IEND") - 4
    return png[:iend] + b"".join(chunks) + png[iend:]


def _tiff_exif(**tags) -> bytes:
    exif = PilImage.Exif()
    for tag, value in tags.items():
        exif[int(tag[1:])] = value
    data = exif.tobytes()
    return data[6:] if data.startswith(b"Exif\x00\x00") else data


def _write_metadata_fixture(folder: Path) -> None:
    """Files for every branch of the two metadata readers."""
    folder.mkdir(parents=True, exist_ok=True)
    plain = PilImage.linear_gradient("L").resize((40, 30)).convert("RGB")

    def png_with(**text) -> bytes:
        info = PngImagePlugin.PngInfo()
        for k, v in text.items():
            info.add_text(k, v)
        return _encoded(plain, "PNG", pnginfo=info)

    (folder / "comfy.png").write_bytes(png_with(
        workflow=json.dumps({"nodes": []}),
        prompt=json.dumps({"3": {"class_type": "CLIPTextEncode", "inputs": {"text": "a castle"}}}),
    ))
    (folder / "comment.png").write_bytes(png_with(Comment=json.dumps({"seed": 5})))
    (folder / "author.png").write_bytes(png_with(Author="Ann Artist", Copyright="(c) Ann"))
    (folder / "late_text.png").write_bytes(_after_pixels(
        _encoded(plain, "PNG"),
        _png_chunk(b"tEXt", b"parameters\x00a late prompt\nSteps: 9"),
        _png_chunk(b"tEXt", b"Author\x00Late Author"),
    ))
    (folder / "late_exif.png").write_bytes(_after_pixels(
        _encoded(plain, "PNG"), _png_chunk(b"eXIf", _tiff_exif(t315="Trailing Artist")),
    ))
    jpeg_exif = PilImage.Exif()
    jpeg_exif[315] = "Jay Artist"
    jpeg_exif[33432] = "(c) Jay"
    jpeg_exif.get_ifd(0x8769)[37510] = b"ASCII\x00\x00\x00a jpeg prompt\nSteps: 30, Sampler: DPM"
    (folder / "artist.jpg").write_bytes(_encoded(plain, "JPEG", exif=jpeg_exif))
    webp_exif = PilImage.Exif()
    webp_exif[315] = "Web Artist"
    (folder / "exif.webp").write_bytes(_encoded(plain, "WEBP", exif=webp_exif))
    (folder / "pal.gif").write_bytes(_encoded(plain.convert("P"), "GIF"))
    (folder / "rot.jpg").write_bytes(_rotated_jpeg())
    (folder / "pal.png").write_bytes(_encoded(PilImage.new("P", (30, 30), 3), "PNG"))
    (folder / "rgba.png").write_bytes(
        _encoded(PilImage.new("RGBA", (30, 30), (10, 200, 10, 120)), "PNG")
    )
    (folder / "gen.png").write_bytes(_a1111_png())


def _old_reads(f: Path, thumb: Path) -> tuple:
    """The four separate calls the ingest paths made before M10 T3."""
    info = image_service.get_image_info(str(f))
    gen_meta = image_service.extract_generation_metadata(str(f))
    image_service.generate_thumbnail(str(f), str(thumb))
    embedded = image_service.extract_embedded_provenance(str(f))
    return info, gen_meta, embedded, thumb.read_bytes()


def test_decode_for_ingest_matches_the_four_separate_reads(tmp_path, monkeypatch):
    """Same size, pHash, generation metadata, attribution and thumbnail bytes as
    the four calls, from one open of the file."""
    src = tmp_path / "src"
    _write_metadata_fixture(src)
    real_open = PilImage.open
    opens: list[str] = []

    def counting_open(fp, *a, **kw):
        opens.append(str(fp))
        return real_open(fp, *a, **kw)

    for f in sorted(src.iterdir()):
        old = _old_reads(f, tmp_path / "old" / f"{f.stem}.webp")

        monkeypatch.setattr(PilImage, "open", counting_open)
        opens.clear()
        thumb = tmp_path / "new" / f"{f.stem}.webp"
        read = image_service.decode_for_ingest(str(f), str(thumb))
        monkeypatch.setattr(PilImage, "open", real_open)

        assert opens == [str(f)], f.name
        assert (read.info, read.gen_meta, read.embedded_provenance, thumb.read_bytes()) == old, f.name
        assert read.info["phash"], f.name

    # The fixture reached the branches it was written for, so the parity above
    # compared real values rather than a column of Nones.
    def read(name):
        return image_service.decode_for_ingest(str(src / name), str(tmp_path / "x" / f"{name}.webp"))

    assert read("comfy.png").gen_meta["source"] == "comfyui"
    assert read("gen.png").gen_meta["prompt"] == "a red fox"
    assert read("author.png").embedded_provenance == {"attribution": "Ann Artist — (c) Ann"}
    assert "Trailing Artist" in read("late_exif.png").embedded_provenance["attribution"]
    assert "Jay Artist" in read("artist.jpg").embedded_provenance["attribution"]
    assert (read("rot.jpg").info["width"], read("rot.jpg").info["height"]) == (20, 40)


def test_decode_for_ingest_decodes_a_png_once(tmp_path, monkeypatch):
    """The point of it: a plain PNG used to be decoded four times on ingest,
    because `exif_transpose` and both metadata readers call `getexif()`, which
    loads a PNG to look for a trailing eXIf chunk."""
    f = tmp_path / "plain.png"
    f.write_bytes(_gradient_png(3))
    real_load_end = PngImagePlugin.PngImageFile.load_end
    decodes = {"n": 0}

    def counting_load_end(self):
        decodes["n"] += 1
        return real_load_end(self)

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load_end", counting_load_end)
    _old_reads(f, tmp_path / "old.webp")
    assert decodes["n"] == 4
    decodes["n"] = 0
    image_service.decode_for_ingest(str(f), str(tmp_path / "new.webp"))
    assert decodes["n"] == 1


def test_decode_for_ingest_fails_like_the_thumbnail_did(tmp_path):
    """An undecodable file raises, as `generate_thumbnail` did, and leaves no thumbnail."""
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not an image at all")
    thumb = tmp_path / "t" / "broken.webp"
    with pytest.raises(Exception) as new_err:
        image_service.decode_for_ingest(str(bad), str(thumb))
    with pytest.raises(Exception) as old_err:
        image_service.generate_thumbnail(str(bad), str(thumb))
    assert type(new_err.value) is type(old_err.value)
    assert not thumb.exists()


# --- folder import ----------------------------------------------------------


async def _import(env, src: Path, name: str) -> tuple[dict, dict]:
    ds = await env.create_dataset(name)
    r = await env.client.post(
        f"{API}/datasets/{ds['id']}/import",
        json={"folder_path": str(src), "preserve_structure": True},
    )
    assert r.status_code == 200, r.text
    job = await wait_for_job(env, r.json()["job_id"])
    return ds, job


def test_import_in_parallel_matches_the_serial_import(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            src = tmp_path / "src"
            _write_fixture(src, nested=True)

            _force_workers(monkeypatch, 1)
            ds1, job1 = await _import(env, src, "serial")
            _force_workers(monkeypatch, 4)
            ds4, job4 = await _import(env, src, "parallel")

            for job in (job1, job4):
                assert job["status"] == "completed", job
            r1, r4 = job1["result_data"], job4["result_data"]
            assert r1["added"] == r4["added"] == 34
            assert r1["failed_count"] == r4["failed_count"] == 1
            assert [f["file"] for f in r1["failed"]] == [f["file"] for f in r4["failed"]] == ["broken.jpg"]

            rows1, rows4 = await _rows(env, ds1["id"]), await _rows(env, ds4["id"])
            assert _snapshot(rows4) == _snapshot(rows1)

            # The fixture's special cases did reach the rows.
            by_orig = {r.original_filename: r for r in rows4}
            assert by_orig["cap.png"].caption_text == "a captioned image"
            assert by_orig["prov.png"].source_url == "https://example.com/p/1"
            assert by_orig["prov.png"].attribution == "Someone"
            assert by_orig["gen.png"].generation_metadata["prompt"] == "a red fox"
            assert by_orig["n2.png"].subfolder == "sub/deep"

            # Input order: the source walk's order, minus the file that failed.
            walk, _videos, _size = dataset_service._scan_source_files(src, True)
            names = [p.name for p in walk]
            assert [r.original_filename for r in rows4] == [n for n in names if n != "broken.jpg"]

            # Of the stem pair, the one walked first keeps the name.
            first, second = sorted(("same.png", "same.jpg"), key=names.index)
            assert by_orig[first].filename == first
            assert by_orig[second].filename == "same_001" + Path(second).suffix

    run(scenario())


def test_import_runs_up_to_the_bound_at_once(tmp_path, monkeypatch):
    real = dataset_service._ingest_file_sync
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def slow(*args):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        try:
            time.sleep(0.05)
            return real(*args)
        finally:
            with lock:
                state["now"] -= 1

    async def scenario():
        async with api_env(tmp_path) as env:
            src = tmp_path / "src"
            _write_fixture(src, nested=False)
            _force_workers(monkeypatch, 3)
            monkeypatch.setattr(dataset_service, "_ingest_file_sync", slow)
            _ds, job = await _import(env, src, "d")
            assert job["status"] == "completed", job

    run(scenario())
    assert state["peak"] == 3


def test_a_cancelled_import_keeps_an_ordered_prefix_and_no_orphans(tmp_path, monkeypatch):
    """A cancel stops new files at once. The ones already in flight finish and
    get rows, because their bytes are already in images/."""
    real = dataset_service._ingest_file_sync
    calls = {"n": 0}

    def cancel_on_sixth(*args):
        calls["n"] += 1
        if calls["n"] == 6:
            job_queue.request_cancel(job_queue._current_job_id)
        time.sleep(0.02)
        return real(*args)

    async def scenario():
        async with api_env(tmp_path) as env:
            src = tmp_path / "src"
            _write_fixture(src, nested=False)
            _force_workers(monkeypatch, 4)
            monkeypatch.setattr(dataset_service, "_ingest_file_sync", cancel_on_sixth)
            ds, job = await _import(env, src, "d")
            assert job["status"] == "cancelled", job

            rows = await _rows(env, ds["id"])
            walk, _videos, _size = dataset_service._scan_source_files(src, True)
            all_names = [p.name for p in walk]
            names = [n for n in all_names if n != "broken.jpg"]
            got = [r.original_filename for r in rows]
            # The six files the ingest was called for all finished; the cancel
            # stopped the run well short of the end; and the rows are a prefix.
            assert set(all_names[:6]) - {"broken.jpg"} <= set(got)
            assert len(got) < len(names)
            assert got == names[: len(got)]

            images_dir = Path(ds["folder_path"]) / "images"
            on_disk = {p.name for p in images_dir.iterdir() if p.suffix in (".png", ".jpg")}
            assert on_disk - {"broken.jpg"} == {r.filename for r in rows}

    run(scenario())


# --- rescan -----------------------------------------------------------------


async def _rescan_fixture(env, tmp_path: Path, name: str) -> tuple[dict, dict, list[str]]:
    """A dataset with one registered image whose caption changed on disk, and
    the fixture dropped into images/ by hand. Returns the walk order too."""
    ds = await env.create_dataset(name)
    await upload_image(env, ds["id"], "old.png", png_bytes((50, 60, 70), (20, 20)))
    images_dir = Path(ds["folder_path"]) / "images"
    (images_dir / "old.txt").write_text("caption written by hand", encoding="utf-8")
    _write_fixture(images_dir, nested=False)
    (images_dir / "sub").mkdir()
    (images_dir / "sub" / "nested.png").write_bytes(_gradient_png(50))

    walk = [p.name for p in images_dir.rglob("*") if p.is_file() and p.suffix in (".png", ".jpg")]
    r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
    assert r.status_code == 200, r.text
    job = await wait_for_job(env, r.json()["job_id"])
    return ds, job, walk


def test_rescan_in_parallel_matches_the_serial_rescan(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _force_workers(monkeypatch, 1)
            ds1, job1, walk1 = await _rescan_fixture(env, tmp_path, "serial")
            _force_workers(monkeypatch, 4)
            ds4, job4, walk4 = await _rescan_fixture(env, tmp_path, "parallel")

            # Precondition: both folders walk in one order, so the two runs saw
            # the same input. Files written in the same order do, on every
            # filesystem the suite runs on.
            assert walk1 == walk4

            for job in (job1, job4):
                assert job["status"] == "completed", job
            r1, r4 = job1["result_data"], job4["result_data"]
            for key in ("added", "renamed", "captions_updated", "missing", "total_on_disk", "failed_count"):
                assert r1[key] == r4[key], key
            assert r4["added"] == 33 and r4["renamed"] == 1 and r4["captions_updated"] == 1
            assert [f["file"] for f in r1["failed"]] == [f["file"] for f in r4["failed"]] == ["broken.jpg"]

            rows1, rows4 = await _rows(env, ds1["id"]), await _rows(env, ds4["id"])
            assert _snapshot(rows4) == _snapshot(rows1)

            by_orig = {r.original_filename: r for r in rows4}
            assert by_orig["old.png"].caption_text == "caption written by hand"
            assert by_orig["cap.png"].caption_text == "a captioned image"
            assert by_orig["prov.png"].attribution == "Someone"
            # The renamed one of the pair still recorded its own name, and
            # still read the caption-less pair's (absent) sidecar without error.
            renamed = [r for r in rows4 if r.filename != r.original_filename]
            assert [(r.original_filename, r.filename) for r in renamed] in (
                [("same.jpg", "same_001.jpg")], [("same.png", "same_001.png")],
            )

            # New rows land in walk order (the upload's row is first).
            new = [r.original_filename for r in rows4[1:]]
            assert new == [n for n in walk4 if n not in ("old.png", "broken.jpg")]

    run(scenario())
