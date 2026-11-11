"""The row-layout experiment still runs, and its layouts still agree (M9 stage 7).

`backend/scripts/bench_split_layout.py` rebuilds a seeded file with the big columns
somewhere else and replays the Stats statements on each copy. Its numbers decide whether
the embeddings leave the `images` row, so two things must hold: every layout holds the
same data (the script's own cross-check, asserted here), and the rewrites it applies
(a join onto a side table, an index hint) reach the SQL that is timed. The 2026-11-09
version of this experiment was a set of scratch files nobody kept; this keeps it runnable.

Schema from `create_all`, as in `test_bench_scaling_smoke.py`: the `backend-tests` CI job
has no Alembic. A migrated file stores its DDL differently (columns appended after the
table constraints), so the rebuild is run on one too, where Alembic is installed.
"""
import os
import sqlite3

import pytest

from backend.scripts import bench_scaling as bench
from backend.scripts import bench_split_layout as split
from backend.tests.conftest import run

ROWS = 300


@pytest.fixture(autouse=True)
def _keep_hf_token():
    # Capturing imports `backend.main`, which projects HF_TOKEN into the environment.
    prev = os.environ.get("HF_TOKEN")
    yield
    if prev is None:
        os.environ.pop("HF_TOKEN", None)
    else:
        os.environ["HF_TOKEN"] = prev


def _seed(tmp_path, schema):
    args = bench.build_parser().parse_args(
        ["--work-dir", str(tmp_path), "--schema", schema, "--sweep", str(ROWS),
         "--neighbour-rows", "40", "--keep-db"]
    )
    db_path, _datasets_dir, was_seeded, _ = bench.prepare_db(args, ROWS, tmp_path)
    assert was_seeded
    return db_path


@pytest.fixture
def seeded(tmp_path):
    return _seed(tmp_path, "create_all")


def _report(db_path, *extra):
    argv = ["--db", str(db_path), "--reps", "1", "--settle", "0", *extra]
    return run(split.run(split.build_parser().parse_args(argv)))


def _by(report, layout):
    return {(r["label"], r["index"]): r for r in report["layouts"][layout]["results"]}


def _images_objects(db_path):
    with sqlite3.connect(db_path) as con:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE tbl_name = 'images'")}


def test_every_layout_holds_the_same_rows(seeded, capsys):
    import backend.database as database
    from backend.routers.quality import _STYLE_PAGE_SIZE

    session_local = database.AsyncSessionLocal
    report = _report(seeded)
    assert database.AsyncSessionLocal is session_local  # capture hands the real factory back

    assert list(report["layouts"]) == list(split.LAYOUTS)
    assert report["mismatches"] == []

    row = _by(report, "row")
    labels = {label for (label, index) in row if index == "default"}
    stats = {"validator", "row fetch (stats)", "row fetch (score-values)", "coverage counts",
             "aesthetic_model group", "flag sums", "embedding count", "license group"}
    gallery = {c.removeprefix("gallery ") for c in split.GALLERY_CASES} | {split.ROWS_LABEL}
    pages = {f"keyset page of {_STYLE_PAGE_SIZE}, {cols}, {where}"
             for cols in ("clip + dino", "layers") for where in ("first", "middle")}
    assert labels == stats | gallery | pages | {"legacy: whole set in one statement (bench_similarity)"}
    # Only the Stats statements take an index hint: a hinted sort would be another query.
    assert {label for (label, index) in row if index != "default"} == stats

    content = report["layouts"]["row"]["content"]
    assert content["rows"] == ROWS + 40  # every dataset's rows, not just the measured one
    for layout in split.LAYOUTS:
        assert report["layouts"][layout]["content"] == content, layout
        for (label, index), r in _by(report, layout).items():
            assert r["status"] == "ok", (layout, label, index, r)
            assert r["plan"] and r["warm_ms"] is not None, (layout, label, index)
            assert (r["rows"], r["checksum"]) == (row[(label, "default")]["rows"],
                                                  row[(label, "default")]["checksum"])
    # A page that comes back empty would agree with itself on every layout.
    assert row[("keyset page of 2000, clip + dino, first", "default")]["rows"] > 0
    assert row[("keyset page of 2000, layers, middle", "default")]["rows"] > 0
    assert row[(split.ROWS_LABEL, "default")]["rows"] == 100

    # The rows really moved: a layout that left them in place would also "agree".
    with sqlite3.connect(seeded) as con:
        (with_blobs,) = con.execute("SELECT count(*) FROM images WHERE clip_embedding IS NOT NULL").fetchone()
        order = split._columns(con)
        by_rowid = [r[0] for r in con.execute(
            "SELECT id FROM images WHERE clip_embedding IS NOT NULL ORDER BY rowid")]
    assert 0 < with_blobs < ROWS + 40
    for layout in ("split3", "split3_shuffled", "split4"):
        gone = split.MOVED[layout]
        with sqlite3.connect(split.layout_path(seeded, layout)) as con:
            cols = split._columns(con)
            side = [r[0] for r in con.execute("SELECT image_id FROM image_embeddings ORDER BY rowid")]
            if layout == "split4":
                assert con.execute("SELECT count(*) FROM image_generation_metadata").fetchone() == (ROWS + 40,)
        assert not set(gone) & set(cols)
        assert [c for c in order if c not in gone] == cols
        # Best case and worst case for a join: the side table in the images table's
        # order, and in id order, which is random against it.
        assert side == (sorted(by_rowid) if layout == "split3_shuffled" else by_rowid)
        assert report["layouts"][layout]["big_columns_at"].keys() == set(split.BIG_COLUMNS) - set(gone)
    assert sorted(by_rowid) != by_rowid

    with sqlite3.connect(split.layout_path(seeded, "blobs_last")) as con:
        cols = split._columns(con)
        # The rebuild drops and renames `images`; a child's foreign key must still name it.
        parents = {r[2] for r in con.execute("PRAGMA foreign_key_list(image_labels)")}
    assert tuple(cols[-4:]) == split.BIG_COLUMNS and sorted(cols) == sorted(order)
    assert "images" in parents
    assert _images_objects(split.layout_path(seeded, "blobs_last")) == _images_objects(seeded)
    assert report["layouts"]["blobs_last"]["after_big"] == []

    # The rewrites reach the SQL that is timed.
    for layout in ("split3", "split3_shuffled", "split4"):
        for label in ("embedding count", "keyset page of 2000, layers, first"):
            joined = _by(report, layout)[(label, "default")]
            assert " JOIN image_embeddings ON" in joined["sql"] and joined["note"], (layout, label)
            assert "images.clip_embedding" not in joined["sql"]
    assert "LEFT JOIN image_generation_metadata ON" in _by(report, "split4")[(split.ROWS_LABEL, "default")]["sql"]
    assert "JOIN" not in _by(report, "split3")[(split.ROWS_LABEL, "default")]["sql"]
    for layout in split.LAYOUTS:
        forced = _by(report, layout)[("row fetch (stats)", "dataset_id")]
        assert "INDEXED BY ix_images_dataset_id" in forced["sql"]
        assert any("ix_images_dataset_id" in step for step in forced["plan"]), forced["plan"]
        scan = _by(report, layout)[("row fetch (stats)", "table_order")]
        assert any(step.startswith("SCAN images") for step in scan["plan"]), scan["plan"]
        hinted_join = _by(report, layout)[("embedding count", "dataset_id")]
        assert any("ix_images_dataset_id" in step for step in hinted_join["plan"]), hinted_join["plan"]

    split.print_markdown(report)
    out = capsys.readouterr().out
    assert "Every statement returned the same rows" in out and "MISMATCH" not in out

    # A second run finds the copies and the captured statements, and rebuilds neither.
    again = _report(seeded, "--layouts", "row,split3")
    assert again["layouts"]["split3"]["build"] == {"reused": True}
    assert again["mismatches"] == [] and again["settled_s"] is None


def test_a_migrated_file_is_rebuilt_too(tmp_path):
    pytest.importorskip("alembic")
    db_path = _seed(tmp_path, "alembic")
    with sqlite3.connect(db_path) as con:
        order = split._columns(con)
    # The layout a real install has: columns that later migrations added sit after the blobs.
    assert order.index("aesthetic_model") > order.index("dino_layer_embeddings")

    report = _report(db_path, "--layouts", "row,blobs_last,split4", "--no-dbstat")
    assert report["mismatches"] == []
    with sqlite3.connect(split.layout_path(db_path, "blobs_last")) as con:
        cols = split._columns(con)
    assert tuple(cols[-4:]) == split.BIG_COLUMNS and sorted(cols) == sorted(order)
    assert _images_objects(split.layout_path(db_path, "blobs_last")) == _images_objects(db_path)
    for layout in ("blobs_last", "split4"):
        assert all(r["status"] == "ok" for r in report["layouts"][layout]["results"])


def test_a_build_only_run_checks_and_times_nothing(seeded, capsys):
    report = _report(seeded, "--layouts", "row,split3", "--build-only")
    assert report["mismatches"] == []
    assert report["layouts"]["split3"]["content"] == report["layouts"]["row"]["content"]
    assert all("results" not in d for d in report["layouts"].values())
    split.print_markdown(report)
    assert "a build-only run" in capsys.readouterr().out


def test_a_cold_pass_evicts_and_times(seeded):
    if not bench.cold_cache_supported(bench.ALEMBIC_INI):
        pytest.skip("this platform cannot evict a file from the OS cache")
    report = _report(seeded, "--cold", "--layouts", "row,split3", "--no-dbstat")
    assert report["evictions"]["tried"] > 0 and report["evictions"]["failed"] == 0
    for layout in ("row", "split3"):
        for key, r in _by(report, layout).items():
            assert r["cold_ms"] is not None, (layout, key)


def test_a_broken_rewrite_stops_the_run(seeded):
    # "n/a" is for a hint SQLite cannot honour. A statement that no longer parses must
    # not be filed there: it would drop out of the cross-check and the run would pass.
    broken = [{"group": "stats", "label": "broken", "sql": "SELECT images.nope FROM images",
               "params": [], "joins": {}}]
    with pytest.raises(SystemExit, match="no such column"):
        split.measure_layout(seeded, "row", broken, 1, 1, None)


def test_only_a_seeded_bench_file_is_accepted(tmp_path):
    # The path guard knows this checkout's database and no other, so a file is also
    # refused unless the harness seeded it.
    other = tmp_path / "someone-elses.db"
    with sqlite3.connect(other) as con:
        con.execute("CREATE TABLE images (id TEXT PRIMARY KEY)")
    with pytest.raises(SystemExit, match="not seeded by bench_scaling"):
        _report(other)
    with pytest.raises(SystemExit, match="Refusing to run"):
        _report(bench._real_db_path())


def test_a_mismatch_is_reported():
    def layout(rows, content=5):
        return {"content": {"group": "content", "label": "big columns", "index": "default",
                            "status": "ok", "rows": 10, "checksum": content},
                "results": [{"group": "stats", "label": "row fetch (stats)", "index": "default",
                             "status": "ok", "rows": rows, "checksum": 7}]}

    assert split.cross_check({"row": layout(10), "split3": layout(10)}) == []
    (problem,) = split.cross_check({"row": layout(10), "split3": layout(9)})
    assert "row fetch (stats)" in problem and "split3/default" in problem
    (problem,) = split.cross_check({"row": layout(10), "split3": layout(10, content=6)})
    assert "big columns" in problem
    # Before anything is timed there are no results yet, only the content check.
    assert split.cross_check({"row": {"content": layout(1)["content"]}}) == []


def test_reordering_keeps_constraints_after_the_columns():
    # A migrated file's stored DDL: `ALTER TABLE ADD COLUMN` appends to the last column's
    # line, and a default can hold a comma inside parentheses or quotes.
    ddl = (
        'CREATE TABLE "images" (\n\tid VARCHAR(36) NOT NULL, \n\tclip_embedding BLOB, \n\t'
        "generation_metadata JSON, \n\tsubfolder VARCHAR(512) DEFAULT ('a,b') NOT NULL, \n\t"
        "note TEXT DEFAULT 'x, y', luminance_score FLOAT, \n\tPRIMARY KEY (id), \n\t"
        "CONSTRAINT uq UNIQUE (id, subfolder)\n)"
    )
    columns = ["id", "clip_embedding", "generation_metadata", "subfolder", "note", "luminance_score"]
    new = split.reordered_ddl(ddl, columns, split.BIG_COLUMNS, "images_new")

    con = sqlite3.connect(":memory:")
    con.execute(new)
    assert split._columns(con, "images_new") == [
        "id", "subfolder", "note", "luminance_score", "clip_embedding", "generation_metadata"]
    con.execute("INSERT INTO images_new (id) VALUES ('1')")
    assert con.execute("SELECT subfolder, note FROM images_new").fetchone() == ("a,b", "x, y")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO images_new (id) VALUES ('1')")


_EMB = {"image_embeddings": "JOIN"}
_BOTH = {"image_embeddings": "LEFT JOIN", "image_generation_metadata": "LEFT JOIN"}
_ROW_FETCH = "SELECT images.width \nFROM images \nWHERE images.dataset_id = ?"
_COUNT = "SELECT count(images.id) FROM images WHERE images.clip_embedding IS NOT NULL"


@pytest.mark.parametrize("sql,joins,layout,hint,expected", [
    # Nothing moved, no hint: the statement as captured.
    (_ROW_FETCH, {}, "split3", None, _ROW_FETCH),
    (_ROW_FETCH, {}, "row", "NOT INDEXED", "SELECT images.width \nFROM images NOT INDEXED \nWHERE images.dataset_id = ?"),
    # In place on the layouts that keep the column, joined on the ones that move it.
    (_COUNT, _EMB, "blobs_last", None, _COUNT),
    (_COUNT, _EMB, "split3", "INDEXED BY ix_images_dataset_id",
     "SELECT count(images.id) FROM images INDEXED BY ix_images_dataset_id JOIN image_embeddings "
     "ON image_embeddings.image_id = images.id WHERE image_embeddings.clip_embedding IS NOT NULL"),
    # A side table per moved column, the embeddings' first, and only the ones this layout has.
    ("SELECT images.clip_embedding, images.generation_metadata FROM images", _BOTH, "split4", None,
     "SELECT image_embeddings.clip_embedding, image_generation_metadata.generation_metadata FROM images "
     "LEFT JOIN image_embeddings ON image_embeddings.image_id = images.id "
     "LEFT JOIN image_generation_metadata ON image_generation_metadata.image_id = images.id"),
    ("SELECT images.clip_embedding, images.generation_metadata FROM images", _BOTH, "split3", None,
     "SELECT image_embeddings.clip_embedding, images.generation_metadata FROM images "
     "LEFT JOIN image_embeddings ON image_embeddings.image_id = images.id"),
    # No known side-table form: skipped, never guessed at.
    ("SELECT images.clip_embedding FROM images", {}, "split3", None, None),
    ("SELECT clip_embedding FROM images", _EMB, "split3", None, None),
    ("SELECT images.generation_metadata FROM images", _EMB, "split4", None, None),
])
def test_statement_rewrites(sql, joins, layout, hint, expected):
    got, note = split.statement_for({"label": "x", "sql": sql, "joins": joins}, layout, hint)
    assert got == expected
    if expected is None:
        assert note
