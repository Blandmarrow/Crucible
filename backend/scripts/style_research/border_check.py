"""Measure film border, sleeve or print margin in each file's centre square (test6's border rule).

    python -I border_check.py <fetch dir> [--limit 0.10]

The centre square is the one ``finalize.py`` keeps. From each edge, count the rows (or columns)
whose 256 px greyscale mean is below 40 or above 215 with a standard deviation under 12: a
uniform dark rebate or a light margin. Prints each file whose border share of the square is
over --limit, and writes <fetch dir>/borders.json with every share.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 60_000_000


def run(a: np.ndarray) -> int:
    """Leading rows of ``a`` that look like a uniform border."""
    n = 0
    for row in a:
        m, s = row.mean(), row.std()
        if (m < 40 or m > 215) and s < 12:
            n += 1
        else:
            break
    return n


def share(path: Path) -> float:
    with Image.open(path) as im:
        sq = ImageOps.fit(ImageOps.exif_transpose(im).convert("L"), (256, 256))
    a = np.asarray(sq, dtype=np.float32)
    top, bottom = run(a), run(a[::-1])
    left, right = run(a.T), run(a.T[::-1])
    if top + bottom >= 256 or left + right >= 256:
        return 1.0
    inner = 256 - top - bottom
    return ((top + bottom) * 256 + (left + right) * inner) / 256 ** 2


def main() -> None:
    root = Path(sys.argv[1])
    limit = float(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0.10
    out = {}
    for d in sorted((root / "raw").iterdir()):
        over = []
        for f in sorted(d.glob("*.jpg")):
            out[f.name] = round(share(f), 3)
            if out[f.name] > limit:
                over.append(f"{f.stem.split('_')[-1]}:{out[f.name]:.2f}")
        print(f"{d.name:<10} over {limit:g}: {len(over):>2}  {' '.join(over)}")
    (root / "borders.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
