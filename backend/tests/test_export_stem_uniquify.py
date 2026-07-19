"""Tests for export_service._unique_stem — per-run stem collision avoidance."""

from backend.services.export_service import _unique_stem


def test_no_collision_returns_stem_unchanged():
    used: set[str] = set()
    assert _unique_stem("photo", used) == "photo"
    assert used == {"photo"}


def test_one_collision_appends_001():
    used: set[str] = set()
    assert _unique_stem("same", used) == "same"
    assert _unique_stem("same", used) == "same_001"
    assert used == {"same", "same_001"}


def test_repeated_collisions_increment():
    used: set[str] = set()
    stems = [_unique_stem("dup", used) for _ in range(4)]
    assert stems == ["dup", "dup_001", "dup_002", "dup_003"]


def test_independent_stems_do_not_interfere():
    used: set[str] = set()
    assert _unique_stem("a", used) == "a"
    assert _unique_stem("b", used) == "b"
    assert _unique_stem("a", used) == "a_001"
