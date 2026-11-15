"""`POST /images/contact-sheet`: the Compare page's sheet export.

Pins what the route promises: the output is the size the pure helper predicts, a
download with a safe name, PNG or JPEG; the path guard refuses an out-of-tree row
before any read; a file gone from disk is a placeholder cell; an oversize or
over-long request is a 422 before any image is opened; an EXIF-rotated file is
drawn upright; every image opened is closed; and nothing is written under the
datasets folder.
"""
import io
from pathlib import Path

from PIL import Image
from sqlalchemy import select, update

from backend.config import settings
from backend.models import Image as ImageRow
from backend.services import contact_sheet as cs
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image

FIELDS = ["header", "filename", "size", "score"]


async def _sheet(env, ids, **body):
    return await env.client.post(f"{API}/images/contact-sheet", json={"image_ids": ids, **body})


async def _path(env, image_id: str) -> Path:
    async with env.Session() as s:
        return Path((await s.execute(select(ImageRow.file_path).where(ImageRow.id == image_id))).scalar_one())


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


async def _three(env, size=(64, 48)):
    ds = await env.create_dataset("My Set")
    return ds, [
        await upload_image(env, ds["id"], f"{n}.png", png_bytes(size=size)) for n in ("a", "b", "c")
    ]


def test_sheet_is_the_predicted_size_and_a_safe_download(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, imgs = await _three(env)
            r = await _sheet(env, [i["id"] for i in imgs], columns=3, cell_px=200, fields=FIELDS)
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "image/png"
            assert r.headers["content-disposition"] == 'attachment; filename="my-set-sheet.png"'
            # 64x48 is 4:3, which is also the fallback aspect.
            expect = cs.sheet_size(3, 3, 200, 64 / 48, FIELDS)
            with Image.open(io.BytesIO(r.content)) as out:
                assert out.format == "PNG" and out.size == expect

    run(scenario())


def test_several_datasets_download_as_compare_and_names_are_slugged(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            d1 = await env.create_dataset('Bad "name"; ../x')
            d2 = await env.create_dataset("other")
            a = await upload_image(env, d1["id"], "a.png", png_bytes())
            b = await upload_image(env, d2["id"], "b.png", png_bytes())
            r = await _sheet(env, [a["id"]])
            assert r.headers["content-disposition"] == 'attachment; filename="bad-name-x-sheet.png"'
            r = await _sheet(env, [a["id"], b["id"]], format="jpeg")
            assert r.headers["content-disposition"] == 'attachment; filename="compare-sheet.jpg"'

    run(scenario())


def test_jpeg_output(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, imgs = await _three(env)
            r = await _sheet(env, [i["id"] for i in imgs], format="jpeg")
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/jpeg"
            assert r.content[:3] == b"\xff\xd8\xff"
            with Image.open(io.BytesIO(r.content)) as out:
                assert out.format == "JPEG"

    run(scenario())


def test_region_sizes_cells_to_its_aspect(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes(size=(200, 100)))
            full = await _sheet(env, [a["id"]], columns=1, cell_px=256, fields=["filename"])
            half = await _sheet(env, [a["id"]], columns=1, cell_px=256, fields=["filename"],
                                region=[0, 0, 0.5, 1])
            assert full.status_code == half.status_code == 200
            with Image.open(io.BytesIO(full.content)) as f, Image.open(io.BytesIO(half.content)) as h:
                assert f.size == cs.sheet_size(1, 1, 256, 2.0, ["filename"])
                # The left half of a 200x100 image is square.
                assert h.size == cs.sheet_size(1, 1, 256, 1.0, ["filename"])
                assert h.height > f.height

    run(scenario())


def test_bad_region_is_422(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png")
            for region in ([0.5, 0, 0.5, 1], [0, 0.6, 1, 0.2], [-0.1, 0, 1, 1], [0, 0, 1.2, 1]):
                assert (await _sheet(env, [a["id"]], region=region)).status_code == 422

    run(scenario())


def test_a_row_outside_the_datasets_folder_is_refused_and_untouched(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png")
            b = await upload_image(env, ds["id"], "b.png")
            outside = tmp_path / "outside.png"
            outside.write_bytes(png_bytes(color=(250, 0, 0)))
            before = outside.read_bytes()
            async with env.Session() as s:
                await s.execute(update(ImageRow).where(ImageRow.id == b["id"]).values(file_path=str(outside)))
                await s.commit()
            r = await _sheet(env, [a["id"], b["id"]])
            assert r.status_code == 403
            assert outside.read_bytes() == before

    run(scenario())


def test_a_file_missing_from_disk_is_a_placeholder_cell(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, imgs = await _three(env)
            (await _path(env, imgs[1]["id"])).unlink()
            r = await _sheet(env, [i["id"] for i in imgs], columns=3, fields=FIELDS)
            assert r.status_code == 200, r.text
            with Image.open(io.BytesIO(r.content)) as out:
                assert out.size == cs.sheet_size(3, 3, 256, 64 / 48, FIELDS)

    run(scenario())


def test_unknown_ids_drop_out_and_none_left_is_404(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png")
            r = await _sheet(env, ["nope", a["id"], a["id"]], columns=1, fields=["filename"])
            assert r.status_code == 200
            with Image.open(io.BytesIO(r.content)) as out:  # one cell, not three
                assert out.size == cs.sheet_size(1, 1, 256, 1.0, ["filename"])
            assert (await _sheet(env, ["nope"])).status_code == 404

    run(scenario())


def test_more_than_100_ids_is_422(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await _sheet(env, [f"id{i}" for i in range(101)])).status_code == 422
            assert (await _sheet(env, [])).status_code == 422
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png")
            assert (await _sheet(env, [a["id"]], columns=13)).status_code == 422
            assert (await _sheet(env, [a["id"]], cell_px=64)).status_code == 422

    run(scenario())


def test_oversize_sheet_is_422_before_any_image_is_opened(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = [await upload_image(env, ds["id"], f"{n}.png") for n in range(30)]
            opened = []
            real = cs.Image.open
            monkeypatch.setattr(cs.Image, "open", lambda *a, **k: opened.append(a) or real(*a, **k))
            r = await _sheet(env, [i["id"] for i in imgs], columns=1, cell_px=1024)
            assert r.status_code == 422
            assert "16000" in r.json()["detail"]
            assert opened == []
            # The same request one column wider is fine, so the cap is the cause.
            r = await _sheet(env, [i["id"] for i in imgs[:3]], columns=1, cell_px=128)
            assert r.status_code == 200 and opened

    run(scenario())


def test_exif_rotated_jpeg_is_drawn_upright(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            # Row says 20x40 (portrait), the way the importer records a rotated file.
            a = await upload_image(env, ds["id"], "a.png", png_bytes(size=(20, 40)))
            # Stored landscape 40x20: left half red, right half blue, Orientation 6
            # (rotate 90 clockwise to view), so it displays red on top, blue below.
            raw = Image.new("RGB", (40, 20), (0, 0, 255))
            raw.paste((255, 0, 0), (0, 0, 20, 20))
            exif = Image.Exif()
            exif[0x0112] = 6
            buf = io.BytesIO()
            raw.save(buf, "JPEG", quality=100, subsampling=0, exif=exif)
            (await _path(env, a["id"])).write_bytes(buf.getvalue())

            r = await _sheet(env, [a["id"]], columns=1, cell_px=128, fields=[])
            assert r.status_code == 200
            with Image.open(io.BytesIO(r.content)) as out:
                out = out.convert("RGB")
                lay = cs._layout(1, 1, 128, 0.5, [])
                x = lay.pad + lay.cell_px // 2
                top = lay.pad + (lay.area_h - 40) // 2
                red = out.getpixel((x, top + 8))
                blue = out.getpixel((x, top + 32))
                assert red[0] > 200 and red[2] < 80, red
                assert blue[2] > 200 and blue[0] < 80, blue

    run(scenario())


def test_every_opened_image_is_closed(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, imgs = await _three(env)
            made, closed = [], set()
            real_open, real_safe, real_close = cs.Image.open, cs._safe_from, Image.Image.close

            def open_(*a, **k):
                im = real_open(*a, **k)
                made.append(im)
                return im

            def safe(im):
                out = real_safe(im)
                made.append(out)
                return out

            def close(self):
                closed.add(id(self))
                return real_close(self)

            monkeypatch.setattr(cs.Image, "open", open_)
            monkeypatch.setattr(cs, "_safe_from", safe)
            monkeypatch.setattr(Image.Image, "close", close)
            r = await _sheet(env, [i["id"] for i in imgs], region=[0, 0, 0.5, 0.5])
            assert r.status_code == 200
            assert len(made) >= 6  # a raw open and a transposed copy per image
            assert all(id(im) in closed for im in made)
            # And the files can be deleted straight away (no handle held on Windows).
            for i in imgs:
                (await _path(env, i["id"])).unlink()

    run(scenario())


def test_nothing_is_written_under_the_datasets_folder(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, imgs = await _three(env)
            root = Path(settings.datasets_dir)
            before = _tree(root)
            for fmt in ("png", "jpeg"):
                assert (await _sheet(env, [i["id"] for i in imgs], format=fmt)).status_code == 200
            assert _tree(root) == before

    run(scenario())


def test_service_cuts_long_lines_and_survives_unreadable_files(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    cells = [
        cs.SheetCell(path=None, filename="x" * 500, caption="y " * 500, dataset_name="d"),
        cs.SheetCell(path=bad, filename="bad.png"),
    ]
    opts = cs.SheetOptions(columns=2, cell_px=128, fields=("header", "filename", "caption"),
                           background="light")
    data = cs.render_contact_sheet(cells, opts)
    with Image.open(io.BytesIO(data)) as out:
        assert out.size == cs.sheet_size(2, 2, 128, None, opts.fields)
    assert cs.auto_columns(100) == 12 and cs.auto_columns(1) == 1 and cs.auto_columns(3) == 3
