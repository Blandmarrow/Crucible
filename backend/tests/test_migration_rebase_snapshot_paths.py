"""`plan_rebase` from migration e3b7a9c2d4f1 — the legacy repair for GPT-021.

Snapshot rows written before `rename_dataset` learned to rebase them still name
the pre-rename root. The migration rewrites them only when that is provably
what they are; everything else is left for restore's path gate to refuse. The
planner is pure path arithmetic, so it is tested directly: the migration file is
loaded with importlib (CI's pytest install has no alembic, which is why the
module imports `op` inside `upgrade()` only).
"""

import importlib.util
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "e3b7a9c2d4f1_rebase_snapshot_paths_after_dataset_rename.py"
)
_spec = importlib.util.spec_from_file_location("_rebase_snapshot_paths", _MIGRATION)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
plan_rebase = _mod.plan_rebase

ROOT = Path("/data/datasets")
F = ROOT / "new-name"


def _p(*parts: str) -> str:
    return str(ROOT.joinpath(*parts))


def test_a_single_old_root_is_rebased():
    rows = [
        (1, "v1", _p("old-name", "images", "a.png"), "a.png"),
        (2, "v1", _p("old-name", "images", "sub", "b.png"), "b.png"),
    ]
    updates, left = plan_rebase(str(F), rows)
    assert sorted(updates) == [
        (1, str(F / "images" / "a.png")),
        (2, str(F / "images" / "sub" / "b.png")),
    ]
    assert left == []


def test_name_reuse_is_still_rebased():
    """The old slug now being *another* dataset's folder does not matter: these
    rows belong to this dataset's snapshot, and leaving them is what clobbered
    the other dataset."""
    rows = [(1, "v1", _p("reused-name", "images", "x.png"), "x.png")]
    updates, left = plan_rebase(str(F), rows)
    assert updates == [(1, str(F / "images" / "x.png"))] and left == []


def test_already_current_rows_are_left_alone():
    rows = [(1, "v1", str(F / "images" / "a.png"), "a.png")]
    assert plan_rebase(str(F), rows) == ([], [])


def test_versions_are_decided_independently():
    """A dataset renamed twice has one era per version; each is rebased from its
    own old slug."""
    rows = [
        (1, "v1", _p("first", "images", "a.png"), "a.png"),
        (2, "v2", _p("second", "images", "a.png"), "a.png"),
        (3, "v3", str(F / "images" / "a.png"), "a.png"),
    ]
    updates, left = plan_rebase(str(F), rows)
    assert sorted(updates) == [(1, str(F / "images" / "a.png")), (2, str(F / "images" / "a.png"))]
    assert left == []


def test_a_path_outside_the_parent_is_left():
    rows = [
        (1, "v1", _p("old-name", "images", "a.png"), "a.png"),
        (2, "v1", "/elsewhere/old-name/images/b.png", "b.png"),
    ]
    updates, left = plan_rebase(str(F), rows)
    assert updates == [], "one unprovable row leaves the whole version"
    assert [(v, n) for v, n, _ in left] == [("v1", 2)]


def test_two_old_roots_in_one_version_are_left():
    rows = [
        (1, "v1", _p("old-a", "images", "a.png"), "a.png"),
        (2, "v1", _p("old-b", "images", "b.png"), "b.png"),
    ]
    updates, left = plan_rebase(str(F), rows)
    assert updates == [] and left[0][0] == "v1" and "2 old roots" in left[0][2]


def test_a_collision_with_a_current_row_is_left():
    rows = [
        (1, "v1", str(F / "images" / "a.png"), "a.png"),
        (2, "v1", _p("old-name", "images", "a.png"), "a.png"),
    ]
    updates, left = plan_rebase(str(F), rows)
    assert updates == [] and "collide" in left[0][2]


def test_a_filename_mismatch_is_left():
    rows = [(1, "v1", _p("old-name", "images", "a.png"), "b.png")]
    updates, left = plan_rebase(str(F), rows)
    assert updates == [] and "filename" in left[0][2]


def test_a_path_not_under_images_is_left():
    rows = [(1, "v1", _p("old-name", "a.png"), "a.png")]
    updates, left = plan_rebase(str(F), rows)
    assert updates == [] and left[0][1] == 1
