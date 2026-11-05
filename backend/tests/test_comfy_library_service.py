"""Pure helpers behind the prompt library's nested categories (M6 §8c)."""
from backend.services.comfy_library import (
    in_category,
    normalize_category,
    plan_category_move,
    remap_category,
)


def test_normalize_category_trims_segments_and_folds_slashes():
    assert normalize_category(" portraits / studio ") == "portraits/studio"
    assert normalize_category("portraits\\studio") == "portraits/studio"
    assert normalize_category("/a//b/./c/") == "a/b/c"
    # A label, not a path: `..` is just a name, and case is kept.
    assert normalize_category("a/../B") == "a/../B"
    assert normalize_category(" / ") == ""


def test_in_category_is_an_exact_case_sensitive_prefix():
    assert in_category("knight", "knight")
    assert in_category("knight/a", "knight")
    assert not in_category("Knight/a", "knight")
    assert not in_category("knights", "knight")


def test_remap_category_keeps_the_tail():
    assert remap_category("a/b/c", "a/b", "x") == "x/c"
    assert remap_category("a", "a", "x/a") == "x/a"


def test_plan_category_move_moves_the_subtree_and_merges_collisions():
    rows = [
        ("occ", "b/x", "Red knight"),        # occupant of the destination
        ("m1", "a/x", "red  KNIGHT"),        # collides with the occupant: merged
        ("m2", "a/x", "blue knight"),        # moves to b/x
        ("m3", "a", "castle"),               # moves to b
        ("m4", "a/y", "castle"),             # moves to b/y: a different category
        ("m5", "a/y", "Castle "),            # collides with m4 once moved: merged
        ("out", "ab/x", "stays"),            # `ab` is not below `a`
    ]
    moves, merges = plan_category_move(rows, "a", "b")
    assert moves == {"m2": "b/x", "m3": "b", "m4": "b/y"}
    assert merges == ["m1", "m5"]


def test_plan_category_move_onto_an_ancestor_merges_into_it():
    rows = [("p", "a", "same"), ("c", "a/b", "same"), ("d", "a/b/z", "deep")]
    moves, merges = plan_category_move(rows, "a/b", "a")
    assert merges == ["c"]
    assert moves == {"d": "a/z"}
