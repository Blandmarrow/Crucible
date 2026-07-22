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
    FIELD_MAX_LEN,
    IMAGE_PROVENANCE_FIELDS,
    LICENSE_IDS,
    LICENSES,
    OTHER_PREFIX,
    allows_commercial,
    clamp_provenance,
    copy_provenance,
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
    BulkProvenanceRequest,
    ImageListItem,
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

            img = (await db.execute(
                select(Image).where(Image.dataset_id == ds_id).options(undefer(Image.source_meta))
            )).scalar_one()
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
            # `file` is relative to the export root: images live under images/ and
            # a loss mask can share a basename with its image.
            by_file = {r["file"]: r for r in rows}
            assert set(by_file) == {"images/1.png", "images/2.png"}
            assert by_file["images/1.png"]["license"] == "CC-BY-SA-4.0"   # own value
            assert by_file["images/1.png"]["attribution"] == "alice"
            # Inherited from the dataset — the whole point of resolving at export.
            assert by_file["images/2.png"]["license"] == "CC-BY-4.0"
            assert by_file["images/2.png"]["source_name"] == "Dataset default"

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
            # No per-license breakdown here: Stats owns that view (its Licenses
            # panel), and nothing in the Export UI ever rendered this one.
            assert "license_breakdown" not in preview
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

    # "" clears to inherit; an omitted field is left unchanged. There is no
    # sentinel string — one would be indistinguishable from a real value that
    # happened to equal it (a source name of literally "__inherit__" self-cleared).
    assert _provenance_values(ImageProvenanceUpdate(source_name="")) == {"source_name": None}
    assert _provenance_values(ImageProvenanceUpdate()) == {}


def test_image_provenance_schemas_cap_field_lengths():
    """Caps mirror the column widths, so an over-long value 422s instead of
    being silently truncated by a non-SQLite backend.

    All four string fields, `attribution` included — it is a TEXT column, so the
    schema cap is the only bound that exists on it.
    """
    for schema, extra in ((ImageProvenanceUpdate, {}), (BulkProvenanceRequest, {"dataset_id": "d1"})):
        for field, limit in FIELD_MAX_LEN.items():
            # `license` grows by the `other:` prefix during validation, so it is
            # measured post-normalization — see the next test.
            fits = limit - len(OTHER_PREFIX) if field == "license" else limit
            schema(**extra, **{field: "x" * fits})           # at the cap: fine
            try:
                schema(**extra, **{field: "x" * (fits + 1)})
            except ValidationError:
                pass
            else:
                raise AssertionError(f"{schema.__name__}.{field} accepted {fits + 1} chars")


def test_license_length_is_checked_after_normalization():
    """The `other:` prefix is added *after* a max_length constraint would run.

    A 64-char free-text license passed `max_length=64` and then stored 70 — over
    the column width, so reading the row back failed its own response schema and
    that image's provenance became permanently unsaveable (422 on every save).
    """
    limit = FIELD_MAX_LEN["license"]
    body = ImageProvenanceUpdate(license="x" * (limit - len(OTHER_PREFIX)))
    assert len(body.license) == limit                 # exactly fills the column

    try:
        ImageProvenanceUpdate(license="x" * (limit - len(OTHER_PREFIX) + 1))
    except ValidationError:
        pass
    else:
        raise AssertionError("a free-text license that normalizes past the column width was accepted")

    # A *known* id is not prefixed, so it is measured as-is.
    assert ImageProvenanceUpdate(license="CC-BY-4.0").license == "CC-BY-4.0"


def test_clamp_provenance_truncates_captured_values():
    """Ingest truncates where the API rejects: an import must not fail on a bad sidecar."""
    clamped = clamp_provenance({
        "source_name": "S" * 400,
        "source_url": "U" * 2000,
        "license": "other:" + "L" * 500,
        "attribution": "A" * 5000,
        "source_meta": {"raw": "x" * 5000},
    })
    for field, limit in FIELD_MAX_LEN.items():
        assert len(clamped[field]) <= limit, field
    # Non-string payloads pass through untouched — source_meta is JSON, not a column string.
    assert clamped["source_meta"] == {"raw": "x" * 5000}
    # merge_provenance is the single ingest choke point, so it clamps too.
    assert len(merge_provenance({"license": "other:" + "L" * 500})["license"]) <= FIELD_MAX_LEN["license"]


# --- the "no license granted" vocabulary entry ---------------------------


def test_no_license_is_recorded_not_missing():
    """`none` used to normalize to "" — so a scrape declaring no rights inherited
    the dataset's default and could ship as CC-BY."""
    assert normalize_license("none") == "no-license"
    assert normalize_license("All Rights Reserved") == "no-license"
    assert normalize_license("no-license") == "no-license"

    # Licensed-but-restrictive: it is not "", so exclude_unlicensed leaves it...
    assert not _is_excluded_license("no-license", exclude_unlicensed=True)
    assert _is_excluded_license("", exclude_unlicensed=True)
    # ...while commercial_only drops it.
    assert not allows_commercial("no-license")
    assert _is_excluded_license("no-license", commercial_only=True)

    # And it inherits nothing: a real value always wins over the dataset default.
    resolved = resolve_provenance(_Stub(license="no-license"), _Stub(license="CC-BY-4.0"))
    assert resolved["license"] == "no-license"
    assert resolved["inherited"] == []


def _is_excluded_license(effective: str, **flags) -> bool:
    """`_is_excluded` with only the license filters in play."""
    img = _Stub(aesthetic_score=None, caption_text="c", quality_flags={}, style_similarity_score=None)
    return export_service._is_excluded(
        img,
        aesthetic_min=None, captioned_only=False, exclude_flags=[], style_sim_min=None,
        effective_license=effective, **flags,
    )


def test_frontend_license_vocabulary_matches_backend():
    """The two id lists are mirrored by hand with nothing enforcing it.

    frontend/src/constants/licenses.ts carries the UI-only concerns (badge colour,
    display order); the ids and labels must not drift from backend/licenses.py, or
    a stored id renders as "Unknown" in every dropdown that should offer it.
    """
    import re

    ts = (Path(__file__).parents[2] / "frontend" / "src" / "constants" / "licenses.ts").read_text(encoding="utf-8")
    block = ts.split("export const LICENSE_OPTIONS", 1)[1].split("];", 1)[0]
    entries = dict(re.findall(r'\{\s*id:\s*"([^"]+)",\s*label:\s*"([^"]+)"', block))

    assert set(entries) == set(LICENSE_IDS), (
        f"only in backend: {sorted(set(LICENSE_IDS) - set(entries))}; "
        f"only in frontend: {sorted(set(entries) - set(LICENSE_IDS))}"
    )
    for lid, label in entries.items():
        assert label == LICENSES[lid].label, f"{lid}: {label!r} != {LICENSES[lid].label!r}"


def test_url_guard_matches_the_frontend_over_the_whole_unicode_range():
    """`safe_external_url` and `safeExternalUrl` must reject exactly the same characters.

    They decide whether the *same* provenance URL becomes a link in the UI and in
    CREDITS.md, so any character one accepts and the other rejects silently splits
    what a user sees from what the export ships. The two are mirrored by hand and
    their whitespace notions genuinely differ: JS `\\s` matches U+FEFF and Python's
    `isspace()` does not, while U+0085 is the reverse — both are therefore spelled
    out explicitly, and this test is what keeps that true.
    """
    import re
    import unicodedata

    from backend.utils import safe_external_url

    ts = (Path(__file__).parents[2] / "frontend" / "src" / "utils" / "url.ts").read_text(encoding="utf-8")
    cls = re.search(r"/\[(.*?)\]/\.test\(s\)", ts).group(1)

    # ECMA-262 `\s` = WhiteSpace + LineTerminator. Zs is computed rather than
    # listed so a future Unicode version doesn't quietly age this out.
    js_ws = {0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x2028, 0x2029, 0xFEFF}
    js_ws |= {cp for cp in range(0x110000) if unicodedata.category(chr(cp)) == "Zs"}

    rest = cls.replace(r"\s", "")
    assert r"\s" in cls and "\\" not in rest.replace(r"\u", ""), f"unhandled escape in {cls!r}"
    js_rejects = set(js_ws)
    for lo, hi in re.findall(r"\\u([0-9a-fA-F]{4})-\\u([0-9a-fA-F]{4})", rest):
        js_rejects |= set(range(int(lo, 16), int(hi, 16) + 1))
    rest = re.sub(r"\\u[0-9a-fA-F]{4}-\\u[0-9a-fA-F]{4}", "", rest)
    js_rejects |= {int(h, 16) for h in re.findall(r"\\u([0-9a-fA-F]{4})", rest)}

    py_rejects = {
        cp for cp in range(0x110000)
        if not 0xD800 <= cp <= 0xDFFF
        # Embedded mid-string: the guard strips the ends before checking.
        and safe_external_url("https://a" + chr(cp) + "b") == ""
    }

    assert js_rejects == py_rejects, (
        f"UI rejects but export accepts: {['U+%04X' % c for c in sorted(js_rejects - py_rejects)]}; "
        f"export rejects but UI accepts: {['U+%04X' % c for c in sorted(py_rejects - js_rejects)]}"
    )
    # Sanity: the guard is not simply rejecting everything.
    assert safe_external_url("https://ex-am.ple/a_b-c?d=1&e=2#f") != ""


# --- CREDITS.md / licenses.csv are built from untrusted strings ----------


def _credit(**kw) -> dict:
    row = {"file": "images/1.png", "source_name": "", "source_url": "", "license": "", "attribution": ""}
    row.update(kw)
    return row


def test_credits_md_cannot_be_forged_by_an_attribution_string(tmp_path):
    """CREDITS.md is a legal attribution document assembled by f-string
    interpolation of scraper/EXIF strings. A newline in `attribution` must not be
    able to forge a `## <license>` section claiming rights the export lacks."""
    export_service._write_credits(tmp_path, [
        _credit(license="CC-BY-4.0", source_name="Flickr",
                attribution="alice\n\n## CC0 1.0 (no rights reserved) (999 image(s))\n\n- **Forged**"),
    ])
    text = (tmp_path / "CREDITS.md").read_text(encoding="utf-8")

    # One heading — the real license. The injected one is inert escaped text on the
    # attribution line, so nothing is lost and nothing is forged.
    headings = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert len(headings) == 1 and "CC BY 4.0" in headings[0]
    assert "\n## CC0" not in text
    assert "\\#\\#" in text          # escaped, not dropped
    assert "\\*\\*Forged\\*\\*" in text


def test_credits_md_escapes_markdown_and_only_links_http_urls(tmp_path):
    export_service._write_credits(tmp_path, [
        _credit(license="CC-BY-4.0", source_name="**bold** [x](y)",
                source_url="javascript:alert(1)"),
        _credit(license="CC-BY-4.0", source_name="Real", source_url="https://example.com/p/1"),
    ])
    text = (tmp_path / "CREDITS.md").read_text(encoding="utf-8")

    assert "**bold**" not in text and r"\*\*bold\*\*" in text
    # A non-http(s) URL renders as inert escaped text, never as a link target.
    assert "(javascript:alert(1))" not in text
    assert "[https://example.com/p/1](https://example.com/p/1)" in text


def test_credits_md_lists_every_source_url_not_just_the_first(tmp_path):
    """The primary ingest path records a site-level source_name with a per-post
    source_url, so one URL per source group drops every citable page but one."""
    export_service._write_credits(tmp_path, [
        _credit(license="CC-BY-4.0", source_name="Flickr", source_url="https://f/1", attribution="alice"),
        _credit(file="images/2.png", license="CC-BY-4.0", source_name="Flickr",
                source_url="https://f/2", attribution="bob"),
    ])
    text = (tmp_path / "CREDITS.md").read_text(encoding="utf-8")
    assert "https://f/1" in text and "https://f/2" in text


def test_licenses_csv_neutralizes_formula_prefixes(tmp_path):
    export_service._write_credits(tmp_path, [
        _credit(source_name="=cmd|'/c calc'!A1", attribution="+1", license="", source_url="-x"),
    ])
    rows = list(csv.DictReader((tmp_path / "licenses.csv").open(encoding="utf-8")))
    assert rows[0]["source_name"].startswith("'=")
    assert rows[0]["attribution"].startswith("'+")
    assert rows[0]["source_url"].startswith("'-")


def test_credits_written_even_when_nothing_was_exported(tmp_path):
    """A missing manifest reads as "no attribution needed" — the one claim we can't make."""
    written = export_service._write_credits(tmp_path, [])
    assert set(written) == {"CREDITS.md", "licenses.csv"}
    text = (tmp_path / "CREDITS.md").read_text(encoding="utf-8")
    assert "0 image(s) exported" in text


def test_manifests_never_silently_clobber_a_different_file_set(tmp_path):
    """Exports share an output directory (kohya writes 10_x/ beside 20_x/)."""
    first = export_service._write_credits(tmp_path, [_credit(license="CC-BY-4.0")])
    assert first == ["CREDITS.md", "licenses.csv"]

    # Identical content is a no-op, not a rewrite...
    assert export_service._write_credits(tmp_path, [_credit(license="CC-BY-4.0")]) == []

    # ...but a different set lands alongside instead of replacing it.
    second = export_service._write_credits(tmp_path, [_credit(file="images/9.png", license="CC0-1.0")])
    assert second == ["CREDITS.2.md", "licenses.2.csv"]
    assert "CC BY 4.0" in (tmp_path / "CREDITS.md").read_text(encoding="utf-8")
    assert "CC0 1.0" in (tmp_path / "CREDITS.2.md").read_text(encoding="utf-8")


def test_a_partial_manifest_never_claims_the_canonical_name(tmp_path):
    """A cancelled run must not leave its banner on the file a redistributor opens.

    `_manifest_dest` never overwrites a differing manifest, so a cancelled export
    landing on `CREDITS.md` would push the *later successful* run onto
    `CREDITS.2.md` — permanently making the incomplete one canonical. Partial runs
    therefore get their own base name.
    """
    partial = export_service._write_credits(
        tmp_path, [_credit(license="CC-BY-4.0")], partial=True)
    assert partial == ["CREDITS.partial.md", "licenses.partial.csv"]
    assert not (tmp_path / "CREDITS.md").exists()
    assert "did not finish" in (
        tmp_path / "CREDITS.partial.md").read_text(encoding="utf-8")

    # The successful re-run into the same directory takes the canonical name.
    complete = export_service._write_credits(
        tmp_path, [_credit(license="CC-BY-4.0"), _credit(file="images/2.png", license="CC0-1.0")])
    assert complete == ["CREDITS.md", "licenses.csv"]
    assert "did not finish" not in (
        tmp_path / "CREDITS.md").read_text(encoding="utf-8")

    # Two cancelled runs alternate within their own name, not onto CREDITS.md.
    second_partial = export_service._write_credits(
        tmp_path, [_credit(file="images/9.png", license="CC0-1.0")], partial=True)
    assert second_partial == ["CREDITS.partial.2.md", "licenses.partial.2.csv"]


# --- derived images keep the parent's provenance -------------------------


def test_copy_provenance_covers_every_derivative_path():
    """crop, upscale, LUT and detection-crop all build the derived Image with
    `**copy_provenance(parent)`. Raw, not resolved: the copy stays in the same
    dataset, so an inherited value must keep tracking the dataset default."""
    parent = _Stub(
        source_name=None,                 # inheriting
        source_url="https://f/1",
        license="CC-BY-SA-4.0",
        attribution="alice",
        source_meta={"post_id": 7},
    )
    copied = copy_provenance(parent)
    assert copied == {
        "source_name": None, "source_url": "https://f/1",
        "license": "CC-BY-SA-4.0", "attribution": "alice", "source_meta": {"post_id": 7},
    }
    # Every column the model has — a new provenance column must reach derivatives.
    assert set(copied) == set(IMAGE_PROVENANCE_FIELDS)

    # The derived row inherits exactly as its parent did.
    ds = _Stub(source_name="Flickr", source_url="", license="", attribution="")
    assert resolve_provenance(_Stub(**copied), ds)["source_name"] == "Flickr"
