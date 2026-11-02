"""Guards for the pure half of `backend/scripts/style_crossed_probe.py`.

The harness itself is offline and its findings live in the roadmap, so this is not trying
to pin any AUC. It pins the three things that would silently corrupt a future run instead
of failing it: the tie handling in `auc` (DINOv2's low layers tie *most* pairs, so a naive
rank would report a made-up ordering as signal), the base sampler taking a spread rather
than a head (a head is one scene of one film), and the restyles leaving geometry untouched
— the whole design rests on a restyle changing style *and nothing else*.

Imports must stay torch-free: the module is numpy + Pillow at module scope and imports
torch only inside the extraction path, which is what lets `combos`/`refs` run on a machine
that has no torch — and what lets this file collect in CI.
"""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from backend.scripts import style_crossed_probe as probe


def test_auc_is_ordering_not_scale():
    assert probe.auc(np.array([3.0, 4.0]), np.array([1.0, 2.0])) == 1.0
    assert probe.auc(np.array([1.0, 2.0]), np.array([3.0, 4.0])) == 0.0
    # The same ordering, compressed into a hundredth — the low-layer case. A metric that
    # moved here would be comparing layers by spread rather than by ordering.
    assert probe.auc(np.array([0.981, 0.982]), np.array([0.979, 0.980])) == 1.0


def test_auc_counts_ties_as_half():
    """Every pair tied is 0.5, not 1.0 — the difference between "no signal" and "perfect"."""
    assert probe.auc(np.array([1.0, 1.0]), np.array([1.0, 1.0])) == 0.5
    # Three clear wins and one tie across the four pairs: (1 + 1 + 0.5 + 1) / 4.
    assert probe.auc(np.array([2.0, 1.0]), np.array([1.0, 0.0])) == 0.875


def test_auc_is_nan_for_an_empty_side():
    assert np.isnan(probe.auc(np.array([]), np.array([1.0])))


def test_spread_samples_across_the_range_not_the_head():
    items = list(range(100))
    picked = probe.spread(items, 5)
    assert picked[0] == 0 and picked[-1] == 99
    assert len(picked) == 5
    assert probe.spread(items, None) == items
    assert probe.spread(items, 500) == items


@pytest.mark.parametrize("grade", sorted(probe.GRADES))
def test_every_restyle_preserves_geometry(grade):
    """A restyle that resized or cropped would confound the two axes it exists to separate."""
    src = Image.fromarray(
        (np.random.default_rng(0).random((23, 31, 3)) * 255).astype(np.uint8), "RGB"
    )
    out = probe.GRADES[grade](src)
    assert out.size == src.size
    assert out.convert("RGB").mode == "RGB"


def test_the_two_grade_families_share_only_the_unmodified_original():
    """`orig` is in both families on purpose — it is the shared anchor a restyle is
    measured against — and nothing else may be, or a family's table would mix axes."""
    assert set(probe.TONE_FAMILY) & set(probe.RENDER_FAMILY) == {"orig"}
    assert set(probe.TONE_FAMILY) | set(probe.RENDER_FAMILY) <= set(probe.GRADES)


def test_build_variants_refuses_colliding_stems(tmp_path):
    """Variants are named by stem, so two bases sharing one would overwrite each other's
    twenty-one files and score one picture twice under two source labels."""
    for name in ("a", "b"):
        d = tmp_path / name
        d.mkdir()
        Image.new("RGB", (8, 8)).save(d / "0001.png")
    bases = [("a", tmp_path / "a/0001.png"), ("b", tmp_path / "b/0001.png")]
    with pytest.raises(SystemExit, match="collide"):
        probe.build_variants(bases, tmp_path / "work")


def test_build_variants_covers_the_grid_and_reuses_existing_files(tmp_path):
    src = tmp_path / "base.png"
    Image.new("RGB", (40, 30), (200, 30, 30)).save(src)
    work = tmp_path / "work"
    rows = probe.build_variants([("src", src)], work)
    assert len(rows) == len(probe.GRADES) * len(probe.FRAMINGS)
    assert {(r["grade"], r["framing"]) for r in rows} == {
        (g, f) for g in probe.GRADES for f in probe.FRAMINGS
    }
    written = sorted(p.name for p in work.iterdir())
    stamps = {p: p.stat().st_mtime_ns for p in work.iterdir()}
    probe.build_variants([("src", src)], work)                 # second pass reuses
    assert sorted(p.name for p in work.iterdir()) == written
    assert {p: p.stat().st_mtime_ns for p in work.iterdir()} == stamps


@pytest.mark.parametrize("count", ["", ":2"])
def test_resolve_source_keeps_a_windows_drive_colon(tmp_path, count):
    """`@C:/dir` used to split at the drive letter and die parsing "/dir" as a count."""
    for name in ("x1.png", "x2.png", "x3.png"):
        Image.new("RGB", (4, 4)).save(tmp_path / name)
    label, files = probe.resolve_source(None, f"@{tmp_path}{count}")
    assert Path(label) == tmp_path
    assert len(files) == (2 if count else 3)


def test_at_depth_is_identity_on_twelve_layers_and_relative_on_deeper_ones():
    """Identity at 12 is what keeps the published DINOv2 / DINOv3 ViT-B tables
    reproducible; 24 is DINOv3 ViT-L, where L7 of 12 sits at L14."""
    assert [probe.at_depth(x, 12) for x in range(1, 13)] == list(range(1, 13))
    assert [probe.at_depth(x, 24) for x in probe.BLEND_LAYERS] == [10, 14, 18, 24]


def test_combos_at_twelve_reproduces_the_published_rows():
    assert probe.combos_at(12) == [
        ("L7 alone", (7,), "raw"),
        ("L9 alone", (9,), "raw"),
        ("L12 alone", (12,), "raw"),
        ("all 12, plain mean", tuple(range(1, 13)), "raw"),
        ("all 12, standardised", tuple(range(1, 13)), "z"),
        ("L5-L9, plain mean", (5, 6, 7, 8, 9), "raw"),
        ("L5-L9, standardised", (5, 6, 7, 8, 9), "z"),
        ("L3-L7, standardised", (3, 4, 5, 6, 7), "z"),
        ("L1-L6, standardised", (1, 2, 3, 4, 5, 6), "z"),
    ]


def test_combos_at_twenty_four_covers_the_same_spans_without_gaps():
    rows = {name: idx for name, idx, _ in probe.combos_at(24)}
    assert rows["L14 alone"] == (14,)
    assert rows["all 24, plain mean"] == tuple(range(1, 25))
    assert rows["L9-L18, standardised"] == tuple(range(9, 19))
    assert rows["L1-L12, standardised"] == tuple(range(1, 13))


def test_signal_table_rescales_its_blend_rows_to_a_deeper_cache():
    rng = np.random.default_rng(0)
    n = 5
    layers = rng.random((n, 24, 8)).astype(np.float16)
    a, b = np.triu_indices(n, k=1)
    sig = probe.signal_table(layers, rng.random((n, 8)), rng.random((n, 8)), a, b)
    assert {f"L{i}" for i in range(1, 25)} <= set(sig)
    assert {"blend @L10", "blend @L14", "blend @L18", "blend @L24"} <= set(sig)
    assert "blend @L7" not in sig


def test_reframes_change_the_frame_and_full_does_not(tmp_path):
    src = tmp_path / "base.png"
    Image.new("RGB", (100, 100)).save(src)
    work = tmp_path / "work"
    probe.build_variants([("src", src)], work)
    with Image.open(work / "base__orig__full.jpg") as full:
        assert full.size == (100, 100)
    for frame in ("cropA", "cropB"):
        with Image.open(work / f"base__orig__{frame}.jpg") as cropped:
            assert cropped.size == (62, 62)
