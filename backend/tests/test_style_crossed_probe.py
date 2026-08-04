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
