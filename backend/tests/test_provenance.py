"""Tests for source & license provenance.

Covers the failure classes the feature is most likely to regress into:
- inheritance resolving the wrong way round (image must win over dataset)
- a snapshot restore silently wiping provenance (VersionImageState mirrors ~20
  Image columns by hand — the classic place to forget a new one)
- a cross-dataset copy re-inheriting the destination's unrelated default
- a scraper sidecar with unexpected shape breaking an import
- an export manifest recording the raw (NULL) license instead of the resolved one
"""
import asyncio
import csv
import json
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import undefer

from backend.database import Base
import backend.models  # noqa: F401 — register all models on Base
from backend.licenses import (
    allows_commercial,
    license_info,
    materialize_by_source,
    materialize_provenance,
    merge_provenance,
    normalize_license,
    resolve_provenance,
)
from backend.models.dataset import Dataset
from backend.models.image import Image
from backend.models.threshold_settings import ThresholdSettings
from backend.routers.images import _provenance_values
from backend.schemas.image import (
    INHERIT_SENTINEL,
    BulkProvenanceRequest,
    ImageListItem,
    ImageOut,
    ImageProvenanceUpdate,
)
from backend.services import export_service, version_service
from backend.services.image_service import read_provenance_sidecar
from backend.utils import parse_license_filter_param


def run(coro):
    return asyncio.run(coro)


# --- resolve_provenance / vocabulary -------------------------------------


class _Stub:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_resolve_provenance_inherits_when_image_field_empty():
    img = _Stub(source_name=None, source_url=None, license=None, attribution=None, source_meta=None)
    ds = _Stub(source_name="Danbooru", source_url="https://x", license="CC-BY-4.0", attribution="alice")
    out = resolve_provenance(img, ds)
    assert out["license"] == "CC-BY-4.0"
    assert out["source_name"] == "Danbooru"
    assert set(out["inherited"]) == {"source_name", "source_url", "license", "attribution"}


def test_resolve_provenance_image_overrides_dataset():
    img = _Stub(source_name="", source_url=None, license="CC0-1.0", attribution="bob", source_meta=None)
    ds = _Stub(source_name="Danbooru", source_url="https://x", license="CC-BY-4.0", attribution="alice")
    out = resolve_provenance(img, ds)
    assert out["license"] == "CC0-1.0"
    assert out["attribution"] == "bob"
    # Only the fields that actually fell back are reported as inherited.
    assert out["inherited"] == ["source_name", "source_url"]


def test_resolve_provenance_tolerates_missing_sides():
    assert resolve_provenance(None, None)["license"] == ""
    img = _Stub(source_name=None, source_url=None, license="owned", attribution=None, source_meta=None)
    assert resolve_provenance(img, None)["license"] == "owned"


def test_normalize_license_known_alias_and_other():
    assert normalize_license("cc-by-4.0") == "CC-BY-4.0"
    assert normalize_license("cc0") == "CC0-1.0"
    assert normalize_license("  ") == ""
    # Unrecognised strings are preserved, never dropped.
    assert normalize_license("Weird Studio EULA") == "other:Weird Studio EULA"
    assert normalize_license("other:Weird") == "other:Weird"


def test_allows_commercial_is_conservative_about_unknowns():
    assert allows_commercial("CC-BY-4.0") is True
    assert allows_commercial("CC-BY-NC-4.0") is False
    assert allows_commercial("unknown") is False   # None → treated as "no"
    assert allows_commercial("") is False
    assert allows_commercial("other:some EULA") is False
    assert license_info("other:some EULA").label == "some EULA"


def test_merge_provenance_left_wins_and_omits_blanks():
    merged = merge_provenance(
        {"license": "owned", "source_name": ""},
        {"license": "CC-BY-4.0", "source_name": "Flickr"},
    )
    assert merged == {"license": "owned", "source_name": "Flickr"}
    assert merge_provenance(None, {}) == {}


# --- sidecar capture -----------------------------------------------------


def test_read_provenance_sidecar_gallery_dl_shape(tmp_path):
    img = tmp_path / "pic.png"
    img.write_bytes(b"x")
    (tmp_path / "pic.json").write_text(json.dumps({
        "category": "danbooru",
        "post_url": "https://danbooru.donmai.us/posts/1",
        "user": {"name": "someartist"},
        "license": "cc-by-4.0",
        "id": 1234,
        "tags": ["a", "b"],
    }), encoding="utf-8")

    out = read_provenance_sidecar(img)
    assert out["source_name"] == "danbooru"
    assert out["source_url"] == "https://danbooru.donmai.us/posts/1"
    assert out["attribution"] == "someartist"
    assert out["license"] == "CC-BY-4.0"          # normalized, not raw
    assert out["source_meta"]["id"] == 1234        # long tail preserved


def test_read_provenance_sidecar_double_extension(tmp_path):
    """gallery-dl's default layout is `pic.png.json`, not `pic.json`."""
    img = tmp_path / "pic.png"
    img.write_bytes(b"x")
    (tmp_path / "pic.png.json").write_text(
        json.dumps({"category": "danbooru", "license": "cc0"}), encoding="utf-8")

    out = read_provenance_sidecar(img)
    assert out["source_name"] == "danbooru"
    assert out["license"] == "CC0-1.0"

    # When both layouts exist, filename + .json wins (it is the more specific one).
    (tmp_path / "pic.json").write_text(
        json.dumps({"category": "elsewhere"}), encoding="utf-8")
    assert read_provenance_sidecar(img)["source_name"] == "danbooru"


def test_read_provenance_sidecar_unknown_license_survives_as_other(tmp_path):
    img = tmp_path / "pic.png"
    img.write_bytes(b"x")
    (tmp_path / "pic.json").write_text(
        json.dumps({"license": "Studio internal use only"}), encoding="utf-8")
    assert read_provenance_sidecar(img)["license"] == "other:Studio internal use only"


def test_read_provenance_sidecar_garbage_returns_none(tmp_path):
    img = tmp_path / "pic.png"
    img.write_bytes(b"x")
    assert read_provenance_sidecar(img) is None          # no sidecar at all

    (tmp_path / "pic.json").write_text("{not json", encoding="utf-8")
    assert read_provenance_sidecar(img) is None          # unparseable

    (tmp_path / "pic.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert read_provenance_sidecar(img) is None          # not an object


# --- cross-dataset copy --------------------------------------------------


def test_materialize_provenance_writes_inherited_values_concretely():
    """A copy into another dataset must not silently re-inherit its default."""
    src_ds = _Stub(source_name="Danbooru", source_url="https://a", license="CC-BY-NC-4.0", attribution="alice")
    img = _Stub(source_name=None, source_url=None, license=None, attribution=None, source_meta=None)

    values = materialize_provenance(img, src_ds)
    assert values["license"] == "CC-BY-NC-4.0"
    assert values["source_name"] == "Danbooru"

    # Landing in a dataset with a different default must not change the license.
    dest_ds = _Stub(source_name="Mine", source_url="", license="owned", attribution="")
    copied = _Stub(**values)
    assert resolve_provenance(copied, dest_ds)["license"] == "CC-BY-NC-4.0"


def test_materialize_by_source_uses_each_rows_own_dataset():
    """A selection spanning datasets must not resolve every row against row 0's.

    The gallery lets you select across datasets (the toolbar shows a per-dataset
    breakdown), and materialized values are concrete — resolving them all against
    one dataset writes an unrelated license in permanently.
    """
    ds_a = _Stub(source_name="Danbooru", source_url="", license="CC-BY-NC-4.0", attribution="alice")
    ds_b = _Stub(source_name="Client X", source_url="", license="owned", attribution="")
    rows = [
        _Stub(id="a1", dataset_id="A", source_name=None, source_url=None,
              license=None, attribution=None, source_meta=None),
        _Stub(id="b1", dataset_id="B", source_name=None, source_url=None,
              license=None, attribution=None, source_meta=None),
        # An image with its own value keeps it regardless of either default.
        _Stub(id="b2", dataset_id="B", source_name=None, source_url=None,
              license="CC0-1.0", attribution=None, source_meta=None),
    ]

    out = materialize_by_source(rows, {"A": ds_a, "B": ds_b})
    assert out["a1"]["license"] == "CC-BY-NC-4.0"
    assert out["a1"]["source_name"] == "Danbooru"
    assert out["b1"]["license"] == "owned"
    assert out["b1"]["source_name"] == "Client X"
    assert out["b2"]["license"] == "CC0-1.0"

    # An unknown dataset id resolves against nothing rather than raising.
    orphan = _Stub(id="z", dataset_id="Z", source_name=None, source_url=None,
                   license=None, attribution=None, source_meta=None)
    assert materialize_by_source([orphan], {})["z"]["license"] is None


# --- versioning round-trip ----------------------------------------------


async def _make_env(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    ds_dir = tmp_path / "ds"
    (ds_dir / "images").mkdir(parents=True)
    (ds_dir / "thumbnails").mkdir(parents=True)

    async with Session() as db:
        ds = Dataset(name="t", folder_path=str(ds_dir), license="CC-BY-4.0", source_name="Dataset default")
        db.add(ds)
        db.add(ThresholdSettings(id=1, versioning_mode="auto"))
        await db.flush()
        fp = ds_dir / "images" / "1.png"
        fp.write_bytes(b"AAAA")
        db.add(Image(
            dataset_id=ds.id, filename="1.png", original_filename="1.png", subfolder="",
            file_path=str(fp),
            thumbnail_path=str(ds_dir / "thumbnails" / "1.webp"),
            license="CC-BY-SA-4.0", source_name="Flickr",
            source_url="https://flickr/1", attribution="alice",
            source_meta={"post_id": 7},
        ))
        await db.commit()
        dataset_id = ds.id
    return engine, Session, ds_dir, dataset_id


def test_snapshot_restore_preserves_provenance(tmp_path):
    """The VersionImageState mirror trap: a restore must not wipe provenance."""
    async def scenario():
        engine, Session, ds_dir, ds_id = await _make_env(tmp_path)
        async with Session() as db:
            snap = await version_service.create_snapshot(db, ds_id, "s1", "")

            img = (await db.execute(select(Image).where(Image.dataset_id == ds_id))).scalar_one()
            img.license = "CC-BY-NC-4.0"
            img.source_name = "Changed"
            img.attribution = "bob"
            img.source_meta = {"post_id": 99}
            await db.commit()

            await version_service.restore_snapshot(
                db, ds_id, snap.id, pre_restore_snapshot=False)
            await db.refresh(img)

            assert img.license == "CC-BY-SA-4.0"
            assert img.source_name == "Flickr"
            assert img.source_url == "https://flickr/1"
            assert img.attribution == "alice"
            assert img.source_meta == {"post_id": 7}
        await engine.dispose()

    run(scenario())


# --- response schemas ----------------------------------------------------
#
# Both of these were live-only 500s that the service-level tests above could
# not reach: they only fail when a real ORM row is serialized through the
# response model.


def test_image_list_item_validates_with_null_license(tmp_path):
    """ImageListItem is validated from the raw row, where license is NULL."""
    async def scenario():
        engine, Session, ds_dir, ds_id = await _make_env(tmp_path)
        async with Session() as db:
            fp = ds_dir / "images" / "inherits.png"
            fp.write_bytes(b"X")
            db.add(Image(
                dataset_id=ds_id, filename="inherits.png", original_filename="inherits.png",
                subfolder="", file_path=str(fp),
                thumbnail_path=str(ds_dir / "thumbnails" / "inherits.webp"),
            ))
            await db.commit()

            img = (await db.execute(
                select(Image).where(Image.filename == "inherits.png")
            )).scalar_one()
            item = ImageListItem.model_validate(img)   # must not raise
            assert item.license is None                # raw value; router resolves it
        await engine.dispose()

    run(scenario())


def test_image_out_validates_without_lazy_loading_deferred_columns(tmp_path):
    """ImageOut reads has_dino_layer_embeddings, which touches a deferred column.

    Serializing an instance whose deferred column was never loaded raises
    MissingGreenlet on an async session — the provenance PATCH endpoint must
    undefer it, exactly as GET /images/{id} does.
    """
    async def scenario():
        engine, Session, ds_dir, ds_id = await _make_env(tmp_path)
        async with Session() as db:
            img = (await db.execute(
                select(Image)
                .where(Image.dataset_id == ds_id)
                .options(undefer(Image.dino_layer_embeddings))
            )).scalar_one()
            img.license = "CC0-1.0"
            await db.commit()
            out = ImageOut.model_validate(img)         # must not raise
            assert out.license == "CC0-1.0"
            assert out.has_dino_layer_embeddings is False
        await engine.dispose()

    run(scenario())


# --- export manifests ----------------------------------------------------


def test_export_writes_manifests_with_resolved_license(tmp_path):
    """licenses.csv records the *resolved* license, not the raw NULL."""
    async def scenario():
        engine, Session, ds_dir, ds_id = await _make_env(tmp_path)
        async with Session() as db:
            # A second image with no license of its own — inherits CC-BY-4.0.
            fp = ds_dir / "images" / "2.png"
            fp.write_bytes(b"BBBB")
            db.add(Image(
                dataset_id=ds_id, filename="2.png", original_filename="2.png",
                subfolder="", file_path=str(fp),
                thumbnail_path=str(ds_dir / "thumbnails" / "2.webp"),
            ))
            await db.commit()

            out_dir = tmp_path / "out"
            result = await export_service.export_plain(db, ds_id, str(out_dir))
            assert result["exported"] == 2

            rows = list(csv.DictReader((out_dir / "licenses.csv").open(encoding="utf-8")))
            by_file = {r["file"]: r for r in rows}
            assert by_file["1.png"]["license"] == "CC-BY-SA-4.0"   # own value
            assert by_file["1.png"]["attribution"] == "alice"
            # Inherited from the dataset — the whole point of resolving at export.
            assert by_file["2.png"]["license"] == "CC-BY-4.0"
            assert by_file["2.png"]["source_name"] == "Dataset default"

            credits = (out_dir / "CREDITS.md").read_text(encoding="utf-8")
            assert "CC BY-SA 4.0" in credits and "attribution required" in credits
            assert result["unlicensed_count"] == 0
        await engine.dispose()

    run(scenario())


def test_export_commercial_only_excludes_nc_and_unknown(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await _make_env(tmp_path)
        async with Session() as db:
            for name, lic in (("nc.png", "CC-BY-NC-4.0"), ("unk.png", "unknown"), ("ok.png", "CC0-1.0")):
                fp = ds_dir / "images" / name
                fp.write_bytes(b"X")
                db.add(Image(
                    dataset_id=ds_id, filename=name, original_filename=name,
                    subfolder="", file_path=str(fp),
                    thumbnail_path=str(ds_dir / "thumbnails" / (Path(name).stem + ".webp")),
                    license=lic,
                ))
            await db.commit()

            out_dir = tmp_path / "out_commercial"
            result = await export_service.export_plain(
                db, ds_id, str(out_dir), commercial_only=True)

            exported = {p.name for p in (out_dir / "images").iterdir()}
            assert "ok.png" in exported                 # CC0 permits commercial use
            assert "1.png" in exported                  # CC-BY-SA-4.0 does too
            assert "nc.png" not in exported             # NC does not
            assert "unk.png" not in exported            # unknown → conservative no
            assert result["exported"] == 2

            preview = await export_service.preview_export(
                db, ds_id, commercial_only=True)
            assert preview["will_export"] == 2
            assert preview["excluded_license"] == 2
            assert preview["license_breakdown"]["CC-BY-NC-4.0"] == 1
        await engine.dispose()

    run(scenario())


def test_export_exclude_unlicensed_keeps_other_licenses(tmp_path):
    """`other:<free text>` is a recorded license — "exclude unlicensed" must keep it.

    Regression: the UI used to express this filter as an allowlist of the curated
    ids, which silently dropped every free-text license too.
    """
    async def scenario():
        engine, Session, ds_dir, ds_id = await _make_env(tmp_path)
        async with Session() as db:
            ds = await db.get(Dataset, ds_id)
            ds.license = ""                       # so `bare.png` resolves to ""
            for name, lic in (("bare.png", None),
                              ("known.png", "CC0-1.0"),
                              ("custom.png", "other:Studio internal use only")):
                fp = ds_dir / "images" / name
                fp.write_bytes(b"X")
                db.add(Image(
                    dataset_id=ds_id, filename=name, original_filename=name,
                    subfolder="", file_path=str(fp),
                    thumbnail_path=str(ds_dir / "thumbnails" / (Path(name).stem + ".webp")),
                    license=lic,
                ))
            await db.commit()

            out_dir = tmp_path / "out_unlicensed"
            result = await export_service.export_plain(
                db, ds_id, str(out_dir), exclude_unlicensed=True)

            exported = {p.name for p in (out_dir / "images").iterdir()}
            assert "custom.png" in exported       # free text is still a license
            assert "known.png" in exported
            assert "1.png" in exported            # own CC-BY-SA-4.0
            assert "bare.png" not in exported     # the only genuinely unlicensed one
            assert result["exported"] == 3

            preview = await export_service.preview_export(
                db, ds_id, exclude_unlicensed=True)
            assert preview["will_export"] == 3
            assert preview["excluded_license"] == 1
            assert preview["license_breakdown"]["other:Studio internal use only"] == 1
        await engine.dispose()

    run(scenario())


def test_export_preview_counts_unlicensed(tmp_path):
    async def scenario():
        engine, Session, ds_dir, ds_id = await _make_env(tmp_path)
        async with Session() as db:
            # Clear the dataset default so the second image resolves to "".
            ds = await db.get(Dataset, ds_id)
            ds.license = ""
            fp = ds_dir / "images" / "bare.png"
            fp.write_bytes(b"X")
            db.add(Image(
                dataset_id=ds_id, filename="bare.png", original_filename="bare.png",
                subfolder="", file_path=str(fp),
                thumbnail_path=str(ds_dir / "thumbnails" / "bare.webp"),
            ))
            await db.commit()

            preview = await export_service.preview_export(db, ds_id)
            assert preview["unlicensed_count"] == 1
            assert preview["license_breakdown"][""] == 1
            # Never hard-blocks: the unlicensed image still exports.
            assert preview["will_export"] == 2
        await engine.dispose()

    run(scenario())


# --- license_filter encoding --------------------------------------------


def test_parse_license_filter_param_json_array():
    """A JSON array, not comma-separated: `other:` ids may contain commas."""
    assert parse_license_filter_param("") is None
    assert parse_license_filter_param('["CC0-1.0"]') == ["CC0-1.0"]

    # The reason the encoding exists at all.
    commad = 'other:Client X, Ltd'
    assert parse_license_filter_param(json.dumps([commad])) == [commad]

    # "" is a meaningful entry (images with no license), an empty list is not.
    assert parse_license_filter_param('[""]') == [""]
    assert parse_license_filter_param("[]") is None

    for bad in ("not json", '{"a": 1}', "[1, 2]"):
        try:
            parse_license_filter_param(bad)
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError(f"expected HTTP 400 for {bad!r}")


# --- image-level provenance edits ---------------------------------------


def test_provenance_values_round_trips_other_free_text():
    """The `other:<free text>` license a user types must survive PATCH intact.

    Exercises the router helper the endpoint is built from — the whole body of
    `PATCH /images/{id}/provenance` past validation is this call plus an UPDATE.
    """
    body = ImageProvenanceUpdate(license="other:Client X, Ltd — internal use")
    values = _provenance_values(body)
    assert values == {"license": "other:Client X, Ltd — internal use"}

    # An unrecognised bare string is bucketed rather than dropped...
    assert _provenance_values(ImageProvenanceUpdate(license="Weird Studio EULA")) == {
        "license": "other:Weird Studio EULA"
    }
    # ...but an `other:` with no body carries no information: clear to inherit.
    assert _provenance_values(ImageProvenanceUpdate(license="other:")) == {"license": None}

    # Sentinel and omission keep their distinct meanings.
    assert _provenance_values(ImageProvenanceUpdate(license=INHERIT_SENTINEL)) == {"license": None}
    assert _provenance_values(ImageProvenanceUpdate()) == {}


def test_image_provenance_schemas_cap_field_lengths():
    """Caps mirror the column widths, so an over-long value 422s instead of
    being silently truncated by a non-SQLite backend."""
    for schema, extra in ((ImageProvenanceUpdate, {}), (BulkProvenanceRequest, {"dataset_id": "d1"})):
        for field, limit in (("source_name", 255), ("source_url", 1024), ("license", 64)):
            schema(**extra, **{field: "x" * limit})          # at the cap: fine
            try:
                schema(**extra, **{field: "x" * (limit + 1)})
            except ValidationError:
                pass
            else:
                raise AssertionError(f"{schema.__name__}.{field} accepted {limit + 1} chars")

    # The sentinel must fit under every cap or "inherit" would stop validating.
    assert len(INHERIT_SENTINEL) <= 64
