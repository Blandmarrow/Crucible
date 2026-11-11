"""The query plan of every Stats statement, and the index pin on the dataset-wide ones.

`test_image_sort_plan.py` guards the gallery's sorts, because PM-036 was a plan that
changed while every result stayed right. The Stats statements had no such guard, and
the same thing happened to them: M9 stage 11 added `ix_images_dataset_updated_at` for
the cache check, SQLite began reading every Stats scan through it, and a scan that
visits a dataset's rows in `updated_at` order reads the file at random. Nothing failed.
The cold row fetch at 100,000 rows went from 8.2 s to 14.6 s (stage 6b).

Decision 15 (`docs/dev/decisions/15-big-columns-stay-in-the-row.md`) pins the
dataset-wide scans to `ix_images_dataset_id`, the one index that lists a dataset's rows
in table order. This file holds that pin in place from three sides:

- **The statement builders**, compiled and explained on an empty table, as the sort
  guard does: the plan does not depend on row counts, because the app never runs
  `ANALYZE`.
- **The app itself.** Both endpoints are called through `api_env` and every statement
  the driver is sent is read. A statement someone adds inline to `get_dataset_stats`,
  past the builders, fails here; and it proves the hint survives the real dialect, not
  just a compile in a test. SQLAlchemy's SQLite dialect drops table hints silently
  unless `backend/database.py` teaches it otherwise.
- **The index.** A pin names it, so dropping or renaming it as "redundant" (nine other
  indexes start with `dataset_id`) would break the Stats page, not slow it.

What is deliberately *not* pinned is asserted too, so nobody "finishes the job": the
cache validator, which reads `ix_images_dataset_updated_at` as a covering index, and a
subfolder-scoped scan, which `ix_images_dataset_subfolder` serves in table order already.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select, text

import backend.models  # noqa: F401 — register every model on Base
from backend.database import Base, indexed_by
from backend.models import Image
from backend.services import dataset_service
from backend.services.dataset_service import (
    STATS_SCAN_INDEX,
    _image_validator_stmt,
    _score_values_rows_stmt,
    _stats_aesthetic_models_stmt,
    _stats_coverage_stmt,
    _stats_embedding_count_stmt,
    _stats_flag_sums_stmt,
    _stats_license_stmt,
    _stats_rows_stmt,
)
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image
from backend.tests.test_image_sort_plan import _plan, _sql

PIN = f"FROM images INDEXED BY {STATS_SCAN_INDEX}"

# Every scan a Stats load makes past the validator. The app-level test below counts
# what the endpoints really send against this, so a new one cannot be left out.
SCANS = {
    "rows": _stats_rows_stmt,
    "coverage": _stats_coverage_stmt,
    "aesthetic models": _stats_aesthetic_models_stmt,
    "flag sums": _stats_flag_sums_stmt,
    "embedding count": _stats_embedding_count_stmt,
    "license": lambda dataset_id, subfolder: _stats_license_stmt(dataset_id, subfolder, "cc0-1.0"),
    "score values": _score_values_rows_stmt,
}
STATS_ENDPOINT_SCANS = 6  # all of SCANS but "score values", which is the other endpoint's


@pytest.fixture()
def conn():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.connect() as c:
        yield c
    engine.dispose()


@pytest.mark.parametrize("name", sorted(SCANS))
def test_a_dataset_wide_scan_is_pinned_to_the_table_order_index(conn, name):
    stmt = SCANS[name]("d", None)
    assert PIN in " ".join(_sql(stmt).split()), (
        "the index hint is not in the SQL: SQLAlchemy drops a SQLite table hint "
        "unless backend/database.py implements get_from_hint_text")
    plan = _plan(conn, stmt)
    assert any(f"USING INDEX {STATS_SCAN_INDEX} (dataset_id=?)" in step for step in plan), plan
    assert not any("ix_images_dataset_updated_at" in step for step in plan), plan


@pytest.mark.parametrize("name", sorted(SCANS))
def test_a_subfolder_scan_keeps_the_subfolder_index(conn, name):
    """`ix_images_dataset_subfolder` holds one subfolder's rows in table order, so
    SQLite's own choice is already the good one. Pinned to the dataset's index, the
    scan would read every row of the dataset to answer for one folder."""
    stmt = SCANS[name]("d", "set_a")
    assert "INDEXED BY" not in _sql(stmt)
    plan = _plan(conn, stmt)
    assert any("ix_images_dataset_subfolder (dataset_id=? AND subfolder=?)" in step for step in plan), plan


@pytest.mark.parametrize("subfolder", [None, "set_a"])
def test_the_cache_validator_is_never_pinned(conn, subfolder):
    """It runs on every poll and reads no table row (`test_image_sort_plan.py` holds
    its covering index in place). Under the scans' index it would read them all."""
    assert "INDEXED BY" not in _sql(_image_validator_stmt("d", subfolder))


def test_the_pinned_index_is_the_plain_dataset_index():
    """The pin is by name. The name comes from `index=True` on `Image.dataset_id`,
    and the order the pin is for comes from the index holding `dataset_id` alone:
    entries with equal keys are kept in `rowid` order. A second column would sort a
    dataset's rows by that column instead."""
    (index,) = [ix for ix in Image.__table__.indexes if ix.name == STATS_SCAN_INDEX]
    assert [c.name for c in index.columns] == ["dataset_id"]
    assert not index.unique


def test_a_pin_to_a_missing_index_is_an_error_not_a_slow_query(conn):
    """Why the index must not be dropped: SQLite refuses the statement outright."""
    stmt = indexed_by(select(Image.id).where(Image.dataset_id == "d"), Image, "ix_images_no_such_index")
    with pytest.raises(Exception, match="no such index"):
        _plan(conn, stmt)


def test_the_pin_changes_the_order_of_the_scan_and_not_its_rows(conn):
    rows = []
    for i in range(40):
        rows.append({
            "id": f"img-{i:03d}", "dataset_id": "d" if i % 4 else "other",
            "filename": f"{i:03d}.png", "original_filename": f"{i:03d}.png",
            "file_path": f"/tmp/{i:03d}.png", "subfolder": "set_a" if i % 3 else "",
            "aesthetic_score": float(i % 7), "aesthetic_model": "laion" if i % 2 else None,
            "license": "cc0-1.0" if i % 5 == 0 else None,
            "quality_flags": {"is_blurry": i % 6 == 0},
        })
    conn.execute(Image.__table__.insert(), rows)
    for name, build in SCANS.items():
        pinned = _sql(build("d", None))
        unpinned = pinned.replace(f" INDEXED BY {STATS_SCAN_INDEX}", "")  # SQLite's own index
        assert unpinned != pinned and "INDEXED BY" not in unpinned, name
        with_pin = sorted(map(tuple, conn.exec_driver_sql(pinned)), key=repr)
        assert with_pin == sorted(map(tuple, conn.exec_driver_sql(unpinned)), key=repr), name
        assert with_pin, name


def test_a_pinned_scan_reads_the_rows_in_table_order(conn):
    """The order the pin is for. `updated_at` runs backwards here, so a scan through
    `ix_images_dataset_updated_at` would hand the rows back reversed."""
    conn.execute(Image.__table__.insert(), [
        {"id": f"img-{i:03d}", "dataset_id": "d", "filename": f"{i:03d}.png",
         "original_filename": f"{i:03d}.png", "file_path": f"/tmp/{i:03d}.png",
         "width": i, "updated_at": datetime(2026, 1, 1) - timedelta(days=i)}
        for i in range(30)
    ])
    assert [row.width for row in conn.execute(_stats_rows_stmt("d", None))] == list(range(30))
    # And it does: the same statement through the index a migrated database's planner
    # picks when left alone. (This schema comes from `create_all`, whose planner makes
    # another choice, which is why the comparison is forced and not left to SQLite.)
    other = _sql(_stats_rows_stmt("d", None)).replace(STATS_SCAN_INDEX, "ix_images_dataset_updated_at")
    assert [row[0] for row in conn.exec_driver_sql(other)] == list(reversed(range(30)))


def test_a_score_mean_does_not_move_when_an_unrelated_edit_touches_a_row(tmp_path):
    """`score_means` is a float sum in row order, so its last digits follow the order
    of the scan. Through `ix_images_dataset_updated_at` that order changed whenever
    any row was written: editing one caption moved that row to the end, and a mean
    could shift in its 14th digit with no score changed. Table order only changes
    when rows come and go. 0.1, 0.2 and 0.3 are the classic three: summed in that
    order they give 0.6000000000000001, and with 0.1 last, 0.6."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = [(await upload_image(env, ds["id"], f"{i}.png", png_bytes((i, i, i))))["id"]
                   for i in range(1, 4)]
            async with env.Session() as db:
                for image_id, score in zip(ids, (0.1, 0.2, 0.3)):
                    (await db.get(Image, image_id)).aesthetic_score = score
                await db.commit()

            async def mean() -> float:
                r = await env.client.get(f"{API}/datasets/{ds['id']}/stats")
                assert r.status_code == 200, r.text
                return r.json()["score_means"]["aesthetic_score"]

            before = await mean()
            async with env.Session() as db:  # any write bumps `updated_at` to now
                (await db.get(Image, ids[0])).source_name = "an unrelated edit"
                await db.commit()
            after = await mean()
            async with env.Session() as db:  # what the scan saw before the pin, after that edit
                by_updated_at = (await db.execute(text(
                    "SELECT aesthetic_score FROM images INDEXED BY ix_images_dataset_updated_at "
                    "WHERE dataset_id = :d"), {"d": ds["id"]})).scalars().all()
            return before, after, by_updated_at

    before, after, by_updated_at = run(scenario())
    assert before == (0.0 + 0.1 + 0.2 + 0.3) / 3
    assert after == before
    assert by_updated_at == [0.2, 0.3, 0.1]
    assert sum(by_updated_at, 0.0) / 3 != before, "these three no longer show the order mattering"


def _images_statements(engine, sink: list[str]):
    def hook(conn, cursor, statement, parameters, context, executemany):
        if "FROM images" in statement:
            sink.append(" ".join(statement.split()))

    event.listen(engine.sync_engine, "before_cursor_execute", hook)


@pytest.mark.parametrize("endpoint,scans", [("stats", STATS_ENDPOINT_SCANS), ("score-values", 1)])
def test_every_scan_the_app_sends_is_pinned_and_only_those(tmp_path, endpoint, scans):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            sent: list[str] = []
            _images_statements(env.Session.kw["bind"], sent)

            dataset_service._stats_cache.clear()
            r = await env.client.get(f"{API}/datasets/{ds['id']}/{endpoint}")
            assert r.status_code == 200, r.text
            wide = list(sent)

            sent.clear()
            dataset_service._stats_cache.clear()
            r = await env.client.get(f"{API}/datasets/{ds['id']}/{endpoint}", params={"subfolder": "set_a"})
            assert r.status_code == 200, r.text
            return wide, list(sent)

    wide, scoped = run(scenario())

    validators = [s for s in wide if "max(images.updated_at)" in s]
    others = [s for s in wide if s not in validators]
    assert len(validators) == 1 and "INDEXED BY" not in validators[0], wide
    # A statement added to the endpoint without `_stats_scan` shows up here unpinned.
    assert all(PIN in s for s in others), [s for s in others if PIN not in s]
    assert len(others) == scans, (
        f"{endpoint} sent {len(others)} scans, not {scans}: add the new one to SCANS above")

    assert len(scoped) == len(wide)
    assert not [s for s in scoped if "INDEXED BY" in s], scoped
    assert all("images.subfolder = ?" in s for s in scoped), scoped
