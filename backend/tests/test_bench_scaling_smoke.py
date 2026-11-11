"""The scaling benchmark still runs against the app as it is (M9 stage 6a).

`backend/scripts/bench_scaling.py` times the real endpoints rather than hand-written
SQL, so a route that M9 stages 4 and 11 change is measured in its new form without
editing the harness. The cost is that a harness nobody runs rots silently when a
route moves. This runs it end to end at a few hundred rows: every case answers 200,
every statement it captured has a plan, and the indexed sorts are read off their
index, so stage 6b finds a working tool.

The main test builds its schema with `create_all`, because the `backend-tests` CI job
does not install Alembic (the `migration-drift` job does, and proves that
`alembic upgrade head` works on an empty database). The migrated path is a second test
that skips where Alembic is missing.
"""
import os
import sqlite3

import pytest

from backend.scripts import bench_scaling as bench
from backend.tests.conftest import run
from backend.tests.test_image_sort_plan import INDEXED


@pytest.fixture(autouse=True)
def _keep_hf_token():
    # The harness imports `backend.main`, whose import projects HF_TOKEN from settings
    # into the environment. Put back whatever was there, as `api_env` does.
    prev = os.environ.get("HF_TOKEN")
    yield
    if prev is None:
        os.environ.pop("HF_TOKEN", None)
    else:
        os.environ["HF_TOKEN"] = prev


def _args(tmp_path, *extra):
    return bench.build_parser().parse_args(
        ["--work-dir", str(tmp_path), "--reps", "0", "--lookups", "10", "--thumbs", "5", *extra]
    )


def test_every_case_runs_and_has_a_plan(tmp_path):
    import backend.database as database
    from backend.routers.images import _ALLOWED_SORT_FIELDS

    session_local = database.AsyncSessionLocal
    args = _args(tmp_path, "--schema", "create_all", "--sweep", "300",
                 "--neighbour-rows", "50", "--optimize")
    report = run(bench.run(args))
    # The harness points the app's own session factory at its scratch file while it
    # runs, and must hand the real one back.
    assert database.AsyncSessionLocal is session_local
    (result,) = report["runs"]
    cases = result["cases"]

    expected = {
        f"gallery {s} {o} {d}" for s in _ALLOWED_SORT_FIELDS for o in ("desc", "asc") for d in ("p1", "deep")
    } | {
        f"gallery aesthetic_score desc p1 subfolder={bench.SMALL_SUBFOLDER}",
        "count dataset", "count subfolder=set_a", f"count subfolder={bench.SMALL_SUBFOLDER}",
        "stats cold", "stats cached poll", "score-values cold", "score-values cached poll",
        "GET /images/{id} x10", "thumbnail x5",
    }
    assert set(cases) == expected
    assert set(result["optimized"]) == expected
    assert result["analyzed"] is True

    for name, case in cases.items():
        assert case["status"] == 200, name
        assert case["payload_bytes"] > 0, name
        assert case["py_peak_mb"] is not None, name
        assert case["statements"], name
        for stmt in case["statements"]:
            assert stmt["plan"], (name, stmt["sql"])

    # A cached poll runs the validator and nothing else on `images`.
    poll = [s["sql"] for s in cases["stats cached poll"]["statements"] if "FROM images" in s["sql"]]
    assert len(poll) == 1 and "max(images.updated_at)" in poll[0]

    # The report labels the same sorts as indexed that the plan guard pins, and they
    # really are read off their index (before ANALYZE, as the app runs).
    assert bench.INDEXED_SORTS == set(INDEXED)
    for sort in INDEXED:
        for order in ("desc", "asc"):
            ids_stmt = cases[f"gallery {sort} {order} p1"]["statements"][0]
            assert not ids_stmt["temp_btree"], (sort, order, ids_stmt["plan"])

    # Not kept: the seeded file and its datasets folder are gone.
    assert not list(tmp_path.glob("bench-*.db*"))
    assert not list((tmp_path / "datasets").glob("bench-*"))


def test_migrated_schema_is_reused_without_reseeding(tmp_path):
    pytest.importorskip("alembic")
    args = _args(tmp_path, "--sweep", "200", "--reuse-db", "--only", "counts")

    first = run(bench.run(args))["runs"][0]
    second = run(bench.run(args))["runs"][0]

    assert first["seeded"] is True and second["seeded"] is False
    assert first["head"] == bench.alembic_head()
    # The three blobs sit where a real install has them, and the columns migrations
    # added later come after them. That is the layout `create_all` does not reproduce.
    positions = sorted(first["blob_columns_at"].values())
    assert len(positions) == 3 and positions[-1] < first["columns"]
    assert {"subfolder", "aesthetic_model", "license"} <= set(first["after_blobs"])
    assert second["cases"]["count dataset"]["status"] == 200

    # A JSON column the seed leaves empty is SQL NULL, as on a real row, not the text
    # 'null' that Core writes for a Python None.
    (db,) = tmp_path.glob("bench-*.db")
    with sqlite3.connect(db) as con:
        texts = con.execute(
            "SELECT count(*) FROM images WHERE source_meta = 'null' "
            "OR processing_history = 'null' OR dino_layer_scores = 'null'"
        ).fetchone()[0]
    assert texts == 0


def test_compare_handles_a_sweep_of_several_sizes(capsys):
    # The loop variable once shadowed the run-matching key(), so the second run of a
    # sweep crashed with "'str' object is not callable" (found by stage 6b's 100k,250k
    # run). Every run of the sweep must be compared, and an unmatched one named.
    def _run(n, best):
        return {"profile": "photo", "n": n, "schema": "alembic", "cold": False,
                "neighbour_rows": 0, "cases": {"count dataset": {"best_ms": best}}}

    before = {"runs": [_run(100, 2.0), _run(200, 4.0)]}
    after = {"runs": [_run(100, 1.0), _run(200, 1.0), _run(300, 1.0)]}
    bench.print_compare(before, after)
    out = capsys.readouterr().out
    assert "| default | count dataset | 2.0 | 1.0 | x0.50 |" in out
    assert "| default | count dataset | 4.0 | 1.0 | x0.25 |" in out
    assert "N=300 (schema alembic, cold False): no matching run" in out


def test_the_apps_own_database_is_refused():
    real = bench._real_db_path()
    assert real is not None
    with pytest.raises(SystemExit, match="Refusing to run"):
        bench.check_scratch_path(real)
