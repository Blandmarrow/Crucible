"""Screen downloaded plates: drop near-monochrome scans, then draw labelled contact sheets.

    python -I screen.py <fetch dir> [--min-colour 18]

Writes <fetch dir>/screen.json (per file: colourfulness, kept) and
<fetch dir>/sheets/<artist>.jpg (kept files only, labelled with their index).
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

Image.MAX_IMAGE_PIXELS = 60_000_000


def colourfulness(im: Image.Image) -> float:
    """Hasler & Süsstrunk (2003) on a 256 px copy: ~0 grey, >~30 clearly coloured."""
    a = np.asarray(im.convert("RGB").resize((256, 256)), dtype=np.float32)
    rg = a[..., 0] - a[..., 1]
    yb = 0.5 * (a[..., 0] + a[..., 1]) - a[..., 2]
    return float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))


def main() -> None:
    root = Path(sys.argv[1])
    min_c = float(sys.argv[sys.argv.index("--min-colour") + 1]) if "--min-colour" in sys.argv else 18.0
    # Per-artist override, e.g. "dulac=28,rackham=28": book scans whose yellowed text pages
    # clear the default bar.
    per = dict(kv.split("=") for kv in sys.argv[sys.argv.index("--per-artist") + 1].split(",")) \
        if "--per-artist" in sys.argv else {}
    base_c = min_c
    out, sheets = {}, root / "sheets"
    sheets.mkdir(exist_ok=True)
    for d in sorted((root / "raw").iterdir()):
        min_c = float(per.get(d.name, base_c))
        kept = []
        for f in sorted(d.glob("*.jpg")):
            try:
                with Image.open(f) as im:
                    im.verify()
                with Image.open(f) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    c = colourfulness(im)
                    size = im.size
            except Exception as e:  # truncated / not an image: drop it
                out[f.name] = {"artist": d.name, "error": repr(e)[:120], "kept": False}
                continue
            ok = c >= min_c
            out[f.name] = {"artist": d.name, "colour": round(c, 1), "size": size, "kept": ok}
            if ok:
                kept.append(f)
        # contact sheet: 12 columns of 150 px tiles, index label on each
        cols, tile = 12, 150
        rows = (len(kept) + cols - 1) // cols or 1
        sheet = Image.new("RGB", (cols * tile, rows * (tile + 16)), "white")
        draw = ImageDraw.Draw(sheet)
        for i, f in enumerate(kept):
            with Image.open(f) as im:
                th = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (tile, tile))
            x, y = (i % cols) * tile, (i // cols) * (tile + 16)
            sheet.paste(th, (x, y))
            draw.text((x + 3, y + tile + 2), f.stem.split("_")[-1], fill="black")
        sheet.save(sheets / f"{d.name}.jpg", quality=80)
        n = sum(1 for v in out.values() if v["artist"] == d.name)
        print(f"{d.name:<8} downloaded {n:>3} · colour ≥ {min_c:g}: {len(kept):>3}")
    (root / "screen.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
