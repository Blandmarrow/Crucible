"""`plan_normalize` from migration e7d1c5a9b3f2 — saved library categories become
normalized `/` paths (M6 §8c). Loaded with importlib, as
`test_migration_rebase_snapshot_paths.py` does: CI's pytest install has no alembic, which
is why the migration imports `op` inside `upgrade()` only.
"""
import importlib.util
from pathlib import Path

from backend.services.comfy_library import normalize_category

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "e7d1c5a9b3f2_nest_library_categories.py"
)
_spec = importlib.util.spec_from_file_location("_nest_library_categories", _MIGRATION)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
plan_normalize = _mod.plan_normalize


def test_categories_normalize_and_new_duplicates_merge_into_the_oldest():
    rows = [
        ("1", "portraits/studio", "Red knight"),
        ("2", " portraits / studio ", "red  KNIGHT"),   # same category once normalized: dup
        ("3", "portraits\\studio", "blue knight"),      # renamed, no clash
        ("4", "landscapes", "fen"),                     # untouched
        ("5", " / ", "orphan"),                         # nothing left: Uncategorized
    ]
    updates, deletes = plan_normalize(rows)
    assert deletes == ["2"]
    assert updates == [("3", "portraits/studio"), ("5", "Uncategorized")]


def test_the_inlined_rule_matches_the_app_rule():
    """The migration copies `normalize_category` rather than importing it; the two must
    agree on every category that has a segment left."""
    for raw in [" a / b ", "a\\b", "/a//./b/", "a/../B", "Case/Kept", "single"]:
        assert _mod.normalize(raw) == normalize_category(raw)
