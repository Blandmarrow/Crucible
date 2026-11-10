"""The query plan of every gallery sort: an indexed sort must stay on its index.

`test_image_sort_paging.py` proves the pages are disjoint and complete. It
cannot see what a page *costs*, and that is how PM-036 shipped: a tie-breaker
added for stable paging (`created_at ASC, id ASC`) was in no index after a score
column, so SQLite stopped using the score's index and sorted every row of the
dataset for each page. Every page was still right, so nothing failed. A score
sort went from 1.4 ms to 323 ms on page 1 at 100,000 rows.

So this file reads `EXPLAIN QUERY PLAN` for the statement the route really
runs (`_page_ids_query`) and fails on a sort step. The plan does not depend on
how many rows there are — the app never runs `ANALYZE` — so an empty table is
enough to read it. It says nothing about the cost; that is the benchmark's job.

Every sort field is classified below, the way `NOT_MIRRORED` works in
`test_video_lineage_mirrors.py`: a new one fails here until someone decides
which set it belongs in, and both sets are checked, so an index added later
moves its field across rather than leaving a stale claim.

Two things this file records because they were measured rather than assumed:

- **A filter does not put the sort step back.** The subfolder filter is
  `sql_subtree`, an `OR` over `substr`, which no index serves. SQLite walks the
  score's index and filters as it goes. For a subfolder of 100 rows in a
  dataset of 100,000 that took 57 ms, against 59 ms for the old order, which
  also read every row and then sorted them. Neither is fast; the new order is
  not the slower one.
- **Ties keep their order when `rowid` is renumbered.** It is not a permanent
  number. A table rebuild — what an Alembic batch migration does — hands out
  new ones, but it copies the rows in `rowid` order, so tied rows keep their
  places. SQLite says a `VACUUM` may renumber too; 3.49.1 does not.
"""
import re

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.dialects import sqlite

import backend.models  # noqa: F401 — register every model on Base
from backend.database import Base
from backend.models import Image
from backend.routers.images import (
    _ALLOWED_SORT_FIELDS,
    _ordered_ids_query,
    _page_ids_query,
)
from backend.schemas.image import ImageIdsParams, ImageListParams
from backend.services.dataset_service import _image_validator_stmt

# Sort field -> the index its ordering must read from. `filename` rides the
# automatic index of `uq_dataset_filename`, whose name SQLite generates, so
# there is no stable name to look for.
INDEXED: dict[str, str | None] = {
    "aesthetic_score": "ix_images_dataset_aesthetic",
    "blur_score": "ix_images_dataset_blur",
    "style_similarity_score": "ix_images_dataset_similarity",
    "created_at": "ix_images_dataset_created_at",
    "caption_token_count": "ix_images_dataset_caption_tokens",
    # `ASC NULLS LAST` over an ascending index: SQLite reads the non-NULL
    # entries first and the NULLs after them, with no sort.
    "sort_order": "ix_images_dataset_sort_order",
    # Added for the Stats cache check (M9 stage 11); the sort rides along.
    "updated_at": "ix_images_dataset_updated_at",
    "filename": None,
}

# No index starts `(dataset_id, <column>)`, so SQLite sorts the dataset. That
# is accepted for these — they are rare sorts — and it is why `list_images`
# sorts ids and not rows. `ix_images_source_video_timeline` does not help the
# two lineage sorts here: it leads with `source_video_id`, not `dataset_id`.
UNINDEXED = frozenset({
    "file_size_bytes", "width", "height", "nsfw_score",
    "noise_score", "uniformity_score", "watermark_score", "color_score",
    "saturation_score", "luminance_score",
    "source_timestamp_ms", "source_shot_index",
})

SORT_STEP = "TEMP B-TREE"


@pytest.fixture()
def conn():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.connect() as c:
        yield c
    engine.dispose()


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))


def _plan(conn, stmt) -> list[str]:
    return [row[3] for row in conn.exec_driver_sql("EXPLAIN QUERY PLAN " + _sql(stmt))]


def _page(sort: str, order: str, **filters) -> ImageListParams:
    # A deep page: the offset is where a full sort hurts most, and it must not
    # change the plan.
    return ImageListParams(dataset_id="d", sort=sort, order=order, page=40, limit=50, **filters)


def test_every_sort_field_is_classified():
    assert set(INDEXED) | UNINDEXED == _ALLOWED_SORT_FIELDS, (
        "a sort field is in neither set: read its plan, then add it to INDEXED "
        "(with its index) or to UNINDEXED")
    assert not set(INDEXED) & UNINDEXED


@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("sort", sorted(_ALLOWED_SORT_FIELDS) + ["no_such_field"])
def test_every_ordering_ends_with_rowid_in_its_own_direction(sort, order):
    """The tail is read off the SQL, because nothing else can see it go missing
    on an ascending sort: an index walk and SQLite's sorter both happen to hand
    ties back in `rowid` order, so every paging test passes with no tie-breaker
    at all. "Happens to" is scan order, which is what the tail exists to replace.
    And the direction is the half PM-036 turned on — `DESC, ASC` matches an
    index in neither direction."""
    sql = _sql(_ordered_ids_query(ImageIdsParams(dataset_id="d", sort=sort, order=order)))
    terms = re.search(r"ORDER BY (.+)$", sql, re.S).group(1).split(",")
    # Custom order ignores `order`: it is ascending by definition.
    descending = order == "desc" and sort != "sort_order"
    assert terms[-1].strip() == f"images.rowid {'DESC' if descending else 'ASC'}", sql
    assert len(terms) == 2, f"a key between the sort column and rowid costs the index: {sql}"


@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("sort", sorted(INDEXED))
def test_an_indexed_sort_reads_its_index_and_never_sorts(conn, sort, order):
    plan = _plan(conn, _page_ids_query(_page(sort, order)))
    label = f"{sort}/{order}: {plan}"
    assert not any(SORT_STEP in step for step in plan), (
        f"{label} — the ordering no longer matches an index, so every page "
        "sorts the whole dataset (PM-036)")
    if INDEXED[sort]:
        assert any(INDEXED[sort] in step for step in plan), label


@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("sort", sorted(UNINDEXED))
def test_an_unindexed_sort_is_a_known_full_sort(conn, sort, order):
    plan = _plan(conn, _page_ids_query(_page(sort, order)))
    assert any(SORT_STEP in step for step in plan), (
        f"{sort}/{order} now reads an index: move it to INDEXED. {plan}")


def test_the_statement_that_sorts_carries_ids_not_rows():
    """What makes a full sort bearable: the sorter holds one id per row. Select
    `Image` here and it holds every column of every row in the dataset, 55 KB of
    `generation_metadata` each on a ComfyUI dataset."""
    stmt = _page_ids_query(_page("width", "desc"))
    assert [c.name for c in stmt.selected_columns] == ["id"]


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_the_ids_endpoint_shares_the_indexed_plan(conn, order):
    """`GET /images/ids` caps `_ordered_ids_query`, the statement
    `_page_ids_query` pages, so select-all cannot order differently from the
    grid. `test_image_sort_paging.py` checks the two routes agree row for row."""
    f = ImageIdsParams(dataset_id="d", sort="aesthetic_score", order=order)
    plan = _plan(conn, _ordered_ids_query(f).limit(20_001))
    assert not any(SORT_STEP in step for step in plan), plan


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_a_subfolder_filter_does_not_bring_the_sort_step_back(conn, order):
    plan = _plan(conn, _page_ids_query(_page("aesthetic_score", order, subfolder="plan/row_7")))
    assert not any(SORT_STEP in step for step in plan), plan
    assert any("ix_images_dataset_aesthetic" in step for step in plan), plan


def test_the_stats_cache_check_reads_only_its_index(conn):
    """The Stats page runs this on every poll, twice. Without
    `ix_images_dataset_updated_at` it read every row of the dataset for
    `max(updated_at)`: 294 ms at 100,000 rows, against 7.1 ms off the index
    (M9 stage 11). A covering index means no table row is touched."""
    plan = _plan(conn, _image_validator_stmt("d", None))
    assert any("COVERING INDEX ix_images_dataset_updated_at" in step for step in plan), plan


def _seed_tied_rows_with_gaps(engine) -> None:
    with engine.begin() as c:
        c.execute(Image.__table__.insert(), [
            {
                # Ids run against the insertion order, so an order that fell
                # back to `id` could not pass for `rowid` order.
                "id": f"img-{999 - i:03d}", "dataset_id": "d", "filename": f"{i:03d}.png",
                "original_filename": f"{i:03d}.png", "file_path": f"/tmp/d/{i:03d}.png",
                # Three values over sixty rows: nearly every row is tied.
                "aesthetic_score": (None, 1.0, 2.0)[i % 3],
            }
            for i in range(60)
        ])
        # Gaps in the numbering, for a renumbering to close up.
        c.execute(delete(Image).where(Image.filename.in_(
            [f"{i:03d}.png" for i in range(0, 60, 4)])))


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_tied_rows_keep_their_order_across_a_table_rebuild(tmp_path, order):
    """`rowid` breaks the ties and is not permanent: a migration that rebuilds
    `images` (Alembic's batch mode: new table, `INSERT … SELECT`, drop, rename)
    numbers the rows afresh. The copy reads the old table in `rowid` order, so
    the new numbers run the same way. If that ever stopped holding, a migration
    would reshuffle every run of tied images in every gallery."""
    engine = create_engine(f"sqlite:///{tmp_path / 'rebuild.db'}")
    Base.metadata.create_all(engine)
    try:
        _seed_tied_rows_with_gaps(engine)
        stmt = _ordered_ids_query(ImageIdsParams(dataset_id="d", sort="aesthetic_score", order=order))

        with engine.connect() as c:
            before = list(c.execute(stmt).scalars())
            old_rowids = [r[0] for r in c.exec_driver_sql("SELECT rowid FROM images ORDER BY rowid")]

        with engine.begin() as c:
            # The rebuild's copy step, then the swap. Indexes are left off: no
            # plan is read here, only the order.
            c.exec_driver_sql("CREATE TABLE _rebuilt AS SELECT * FROM images")
            c.exec_driver_sql("DROP TABLE images")
            c.exec_driver_sql("ALTER TABLE _rebuilt RENAME TO images")

        with engine.connect() as c:
            after = list(c.execute(stmt).scalars())
            new_rowids = [r[0] for r in c.exec_driver_sql("SELECT rowid FROM images ORDER BY rowid")]

        assert len(before) == 45
        assert new_rowids != old_rowids, "the rebuild renumbered nothing, so this proves nothing"
        assert after == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_tied_rows_keep_their_order_across_a_vacuum(tmp_path, order):
    """SQLite's documentation lets a `VACUUM` renumber the rows of a table with
    no INTEGER PRIMARY KEY. 3.49.1 keeps every `rowid` as it was, so on that
    version this compares an order with itself; it is here for the SQLite that
    does renumber. The rebuild test above is the one that can fail today."""
    engine = create_engine(f"sqlite:///{tmp_path / 'vacuum.db'}")
    Base.metadata.create_all(engine)
    try:
        _seed_tied_rows_with_gaps(engine)
        stmt = _ordered_ids_query(ImageIdsParams(dataset_id="d", sort="aesthetic_score", order=order))

        with engine.connect() as c:
            before = list(c.execute(stmt).scalars())
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.exec_driver_sql("VACUUM")
        with engine.connect() as c:
            after = list(c.execute(stmt).scalars())

        assert len(before) == 45
        assert after == before
    finally:
        engine.dispose()
