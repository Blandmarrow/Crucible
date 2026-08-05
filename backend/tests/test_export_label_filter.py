"""Exporting with a label filter — and the invariant that keeps labels from
re-becoming the tags system.

Two things are pinned here:

- The filter **narrows** the export the way "Limit to subfolders" does, rather
  than joining the exclusion tally. It says which images the export is about, so
  `image_count` shrinks and there is no "excluded by label" counter. The preview
  and the export therefore have to agree on the same number.
- **No caption interaction, ever.** A labelled image's exported `.txt` is
  byte-identical to its `caption_text`, and no label name appears in any sidecar,
  in `captions.jsonl`, or in `CREDITS.md`. `_write_image` and the caption sidecar
  writer are untouched by this feature and must stay that way.
"""
from pathlib import Path

from backend.models import Image, ImageLabel, Label
from backend.services import export_service
from backend.tests.conftest import api_env, run


async def _seed(env, dataset_id: str):
    """Six images: two `fx`, two `reject`, one both, one bare. Every caption
    carries a token no label name shares, so a leak would be unmissable."""
    ds_dir = env.datasets_dir / dataset_id
    (ds_dir / "images").mkdir(parents=True, exist_ok=True)

    async with env.Session() as db:
        rows = []
        for i in range(6):
            fp = ds_dir / "images" / f"i{i}.png"
            fp.write_bytes(b"PNGDATA" + bytes([i]))
            rows.append(Image(
                dataset_id=dataset_id,
                filename=f"i{i}.png",
                original_filename=f"i{i}.png",
                subfolder="",
                file_path=str(fp),
                thumbnail_path=str(ds_dir / "thumbnails" / f"i{i}.webp"),
                caption_text=f"a photograph of subject {i}",
            ))
        fx = Label(name="fx")
        reject = Label(name="reject")
        db.add_all([*rows, fx, reject])
        await db.flush()
        db.add_all([
            ImageLabel(image_id=rows[0].id, label_id=fx.id),
            ImageLabel(image_id=rows[1].id, label_id=fx.id),
            ImageLabel(image_id=rows[2].id, label_id=fx.id),      # both
            ImageLabel(image_id=rows[2].id, label_id=reject.id),
            ImageLabel(image_id=rows[3].id, label_id=reject.id),
            ImageLabel(image_id=rows[4].id, label_id=reject.id),
        ])
        await db.commit()
        return {"fx": fx.id, "reject": reject.id, "rows": [r.id for r in rows]}


def test_export_writes_only_matching_images(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            seed = await _seed(env, ds["id"])

            async with env.Session() as db:
                out = tmp_path / "out-any"
                result = await export_service.export_plain(
                    db, ds["id"], str(out),
                    label_filter=[seed["fx"], seed["reject"]],
                )
                assert result["exported"] == 5

                out_all = tmp_path / "out-all"
                result = await export_service.export_plain(
                    db, ds["id"], str(out_all),
                    label_filter=[seed["fx"], seed["reject"]], label_match="all",
                )
                assert result["exported"] == 1
                assert [p.name for p in (out_all / "images").iterdir()] == ["i2.png"]

    run(scenario())


def test_label_missing_exports_the_unlabelled(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _seed(env, ds["id"])

            async with env.Session() as db:
                out = tmp_path / "out"
                result = await export_service.export_plain(
                    db, ds["id"], str(out), label_missing=True
                )
                assert result["exported"] == 1
                assert [p.name for p in (out / "images").iterdir()] == ["i5.png"]

    run(scenario())


def test_preview_count_equals_the_actual_export_count(tmp_path):
    """The filter narrows the query in both, so `image_count` — not an exclusion
    counter — is what moves."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            seed = await _seed(env, ds["id"])

            async with env.Session() as db:
                preview = await export_service.preview_export(
                    db, ds["id"], label_filter=[seed["fx"]]
                )
                assert preview["image_count"] == 3
                # Nothing landed in an exclusion bucket: the filter is not one.
                assert preview["excluded_low_aesthetic"] == 0
                assert preview["excluded_flagged"] == 0

                out = tmp_path / "out"
                result = await export_service.export_plain(
                    db, ds["id"], str(out), label_filter=[seed["fx"]]
                )
                assert result["exported"] == preview["will_export"] == preview["image_count"]

    run(scenario())


def test_no_label_name_reaches_any_written_artifact(tmp_path):
    """The invariant. Labels never touch caption text and are never exported as
    caption tokens — which is what keeps them from re-becoming the tags system."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d", license="CC-BY-4.0", attribution="Someone")
            seed = await _seed(env, ds["id"])

            async with env.Session() as db:
                out = tmp_path / "out"
                # kohya with `caption_format="txt"`, because that is the layout
                # that writes a `.txt` beside each image — `export_plain` puts
                # captions in captions.jsonl instead, and both are checked below.
                await export_service.export_kohya(
                    db, ds["id"], str(out), 10, "concept",
                    caption_format="txt",
                    label_filter=[seed["fx"], seed["reject"]],
                )

                # The sidecar is byte-identical to `caption_text` — no separator,
                # no appended token, nothing.
                sidecar = next(out.rglob("i0.txt")).read_bytes()
                assert sidecar == b"a photograph of subject 0"

                # And no label name appears anywhere the export wrote.
                for path in Path(out).rglob("*"):
                    if not path.is_file() or path.suffix == ".png":
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    assert "fx" not in text, f"label name leaked into {path.name}"
                    assert "reject" not in text, f"label name leaked into {path.name}"

                # Named explicitly, so the sweep above is not passing because
                # nothing was written. The jsonl comes from a second, plain
                # export of the same selection.
                assert (out / "CREDITS.md").exists()
                assert (out / "licenses.csv").exists()

                plain = tmp_path / "out-plain"
                await export_service.export_plain(
                    db, ds["id"], str(plain), label_filter=[seed["fx"], seed["reject"]]
                )
                jsonl = (plain / "captions.jsonl").read_text(encoding="utf-8")
                assert jsonl and "fx" not in jsonl and "reject" not in jsonl

    run(scenario())
