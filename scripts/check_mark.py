#!/usr/bin/env python3
"""Drift check for the Crucible brand mark.

The mark exists in several independent transcriptions of the same 5x5 grid, so
a change to one silently diverges from the rest. This diffs them:
  - docs/images/crucible-mark.svg          static mark (design source of truth)
  - docs/images/crucible-icon.svg          app-icon variant (inverted palette)
  - docs/images/Crucible Logo Animated.html animated reference
  - frontend/src/components/common/CrucibleMark.tsx   what the app renders
  - frontend/public/favicon.svg + PNGs     copies of the docs/images/ exports

Dependency-free (stdlib only). Run from anywhere:

    python scripts/check_mark.py

Checks (any failure sets a non-zero exit code):
  1. the set of grid cells matches crucible-mark.svg
  2. each cell's role (part of the C, or dropped grid) matches
  3. the bright --accent-2 cells match
  4. each cell's animation keep/drop role and A/B variant matches
     "Crucible Logo Animated.html"
  5. the app-icon variant (crucible-icon.svg) draws the same C
  6. the frontend/public/ favicons are byte-identical to their docs/images/
     sources (a re-export that wasn't re-copied would silently ship the old C)
  7. if frontend/dist has been built, its CSS still contains the mark's
     animation rules (Tailwind purges @layer components rules whose selector
     it cannot find verbatim in the source — see docs/dev/frontend-core.md)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK_SVG = ROOT / "docs/images/crucible-mark.svg"
ICON_SVG = ROOT / "docs/images/crucible-icon.svg"
ANIM_HTML = ROOT / "docs/images/Crucible Logo Animated.html"
COMPONENT = ROOT / "frontend/src/components/common/CrucibleMark.tsx"

# Emerald fills in the export denote the resolved C; --accent-2 cells are bright.
FILL_KEPT = "#10b981"
FILL_BRIGHT = "#34d399"
# The app icon inverts the palette: an emerald background with dark ink cells.
# The C is the full-opacity ink; dropped grid cells carry a low opacity.
FILL_ICON_INK = "#03130d"

# Each frontend/public/ icon is a hand-copy of a docs/images/ export — Vite
# serves public/ verbatim, so a re-export that never reached public/ ships the
# stale icon. These must stay byte-identical.
COPY_PAIRS = [
    ("frontend/public/favicon.svg", "docs/images/crucible-icon.svg"),
    ("frontend/public/favicon-16.png", "docs/images/icons/crucible-icon-16.png"),
    ("frontend/public/favicon-32.png", "docs/images/icons/crucible-icon-32.png"),
    ("frontend/public/apple-touch-icon.png", "docs/images/icons/crucible-icon-256.png"),
]


def fail(msg: str) -> None:
    print(f"FAIL  {msg}")


def parse_reference_svg(text: str):
    """-> (all_cells, kept, bright) keyed 'x,y'."""
    all_cells, kept, bright = set(), set(), set()
    for m in re.finditer(r'<rect x="(\d+)" y="(\d+)"[^>]*fill="(#[0-9a-f]{6})"', text):
        x, y, fill = m.groups()
        key = f"{x},{y}"
        all_cells.add(key)
        if fill == FILL_BRIGHT:
            bright.add(key)
            kept.add(key)
        elif fill == FILL_KEPT:
            kept.add(key)
    return all_cells, kept, bright


def parse_icon_svg(text: str):
    """-> set of 'x,y' cells forming the C in the app-icon variant.

    The icon is the mark inverted: an emerald background rect plus dark ink
    cells. C cells are full-opacity ink; dropped grid cells carry an `opacity`
    attribute. The 64x64 background rect (width != 8) is skipped.
    """
    kept = set()
    for m in re.finditer(r"<rect ([^>]*)>", text):
        attrs = m.group(1)
        xm = re.search(r'x="(\d+)"', attrs)
        ym = re.search(r'y="(\d+)"', attrs)
        wm = re.search(r'width="(\d+)"', attrs)
        fm = re.search(r'fill="(#[0-9a-f]{6})"', attrs)
        if not (xm and ym and wm and fm) or wm.group(1) != "8":
            continue
        if fm.group(1) == FILL_ICON_INK and "opacity" not in attrs:
            kept.add(f"{xm.group(1)},{ym.group(1)}")
    return kept


def parse_reference_anim(text: str):
    """-> {'x,y': ('keep'|'drop', 'a'|'b')}."""
    out = {}
    for m in re.finditer(
        r'animation:(c8-keep|c10-drop)([AB])[^"]*"><rect x="(\d+)" y="(\d+)"', text
    ):
        kind, variant, x, y = m.groups()
        out[f"{x},{y}"] = ("keep" if kind == "c8-keep" else "drop", variant.lower())
    return out


def parse_component(text: str):
    """-> (all_cells, kept, bright, {'x,y': (role, variant)}) mirroring the TSX rules."""
    def const_set(name: str) -> set[str]:
        block = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", text, re.S)
        if not block:
            raise SystemExit(f"FAIL  could not find `const {name}` in {COMPONENT.name}")
        return set(re.findall(r'"([^"]+)"', block.group(1)))

    coords_m = re.search(r"const COORDS = \[(.*?)\]", text)
    if not coords_m:
        raise SystemExit(f"FAIL  could not find `const COORDS` in {COMPONENT.name}")
    coords = [int(c) for c in re.findall(r"\d+", coords_m.group(1))]

    kept, bright = const_set("KEPT"), const_set("BRIGHT")
    all_cells, anim = set(), {}
    for y in coords:
        for x in coords:
            key = f"{x},{y}"
            all_cells.add(key)
            # Mirrors the component's checkerboard variant rule.
            variant = "a" if (coords.index(x) + coords.index(y)) % 2 == 0 else "b"
            anim[key] = ("keep" if key in kept else "drop", variant)
    return all_cells, kept, bright, anim


def check_built_css() -> bool | None:
    """-> True/False, or None when there is no build to inspect.

    Guards the purge trap: the class rules are what Tailwind drops, while the
    @keyframes survive — so a purged build still *looks* fine unless the rules
    themselves are checked.
    """
    dist = ROOT / "frontend/dist/assets"
    if not dist.is_dir():
        return None
    css = "".join(p.read_text(encoding="utf-8") for p in dist.glob("*.css"))
    if not css:
        return None
    missing = [c for c in ("cm-keep-a", "cm-keep-b", "cm-drop-a", "cm-drop-b")
               if f".{c}" not in css]
    if missing:
        fail(f"built CSS is missing rules for: {', '.join(missing)}")
        print("      Tailwind purged them — the class name is probably built")
        print("      dynamically instead of written out verbatim.")
        print("      See docs/dev/frontend-core.md (Styling).")
        return False
    return True


def diff(label: str, ref: set, got: set) -> bool:
    if ref == got:
        return True
    fail(f"{label} differs from the export")
    if ref - got:
        print(f"      only in export:    {sorted(ref - got)}")
    if got - ref:
        print(f"      only in component: {sorted(got - ref)}")
    return False


def check_copies() -> bool:
    """Each frontend/public/ icon must be byte-identical to its docs/images/ source."""
    ok = True
    for pub_rel, src_rel in COPY_PAIRS:
        pub, src = ROOT / pub_rel, ROOT / src_rel
        if not src.exists():
            fail(f"missing export {src_rel}")
            ok = False
            continue
        if not pub.exists():
            fail(f"missing copy {pub_rel}")
            ok = False
            continue
        if pub.read_bytes() != src.read_bytes():
            fail(f"{pub_rel} differs from {src_rel}")
            print("      Re-copy the export into frontend/public/ (Vite serves it verbatim).")
            ok = False
    return ok


def main() -> int:
    for p in (MARK_SVG, ICON_SVG, ANIM_HTML, COMPONENT):
        if not p.exists():
            fail(f"missing {p.relative_to(ROOT)}")
            return 1

    ref_all, ref_kept, ref_bright = parse_reference_svg(MARK_SVG.read_text(encoding="utf-8"))
    ref_icon_kept = parse_icon_svg(ICON_SVG.read_text(encoding="utf-8"))
    ref_anim = parse_reference_anim(ANIM_HTML.read_text(encoding="utf-8"))
    got_all, got_kept, got_bright, got_anim = parse_component(COMPONENT.read_text(encoding="utf-8"))

    ok = True
    ok &= diff("grid cells", ref_all, got_all)
    ok &= diff("C shape (KEPT)", ref_kept, got_kept)
    ok &= diff("bright cells (BRIGHT)", ref_bright, got_bright)
    ok &= diff("app-icon C shape", ref_kept, ref_icon_kept)

    if set(ref_anim) != set(got_anim):
        ok = False
        fail("animated export covers a different cell set than the component")
    else:
        drift = {k: (ref_anim[k], got_anim[k]) for k in ref_anim if ref_anim[k] != got_anim[k]}
        if drift:
            ok = False
            fail("animation role/variant differs from the export")
            for k, (r, g) in sorted(drift.items()):
                print(f"      {k}: export={r[0]}-{r[1]} component={g[0]}-{g[1]}")

    ok &= check_copies()

    built = check_built_css()
    ok &= built is not False

    if ok:
        coords = sorted({int(c.split(",")[1]) for c in ref_all})
        xs = sorted({int(c.split(",")[0]) for c in ref_all})
        print("  Crucible mark — component matches docs/images/ exports\n")
        for y in coords:
            row = " ".join(
                ("#" if f"{x},{y}" in ref_bright else "@") if f"{x},{y}" in ref_kept else "."
                for x in xs
            )
            print(f"    {row}")
        print(f"\n  {len(ref_all)} cells   @ = C   # = bright   . = dropped grid")
        print("\n  built CSS: " + (
            "animation rules present" if built else "not built — skipped"))
        print("\nOK")
        return 0
    print("\nThe mark drifted. Update docs/images/ and CrucibleMark.tsx together —")
    print("see docs/dev/frontend-core.md (Styling).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
