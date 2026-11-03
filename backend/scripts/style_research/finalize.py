"""Turn screened downloads into the import folder.

    python -I -X utf8 finalize.py <fetch dir> <out dir> [--cap 30] [--min-colour 18]

Keeps files that passed the colour screen and are not in exclude.json, drops perceptual
near-duplicates within an artist (pHash distance <= 8, first kept), takes an even spread of
at most --cap per artist, and writes every one as a 1024x1024 centre-cropped JPEG q90, so
format, size and aspect are identical across artists. Writes <out>/MANIFEST.json with each
file's Commons page, licence and artist.
"""
import json
import sys
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 60_000_000


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    cap = int(sys.argv[sys.argv.index("--cap") + 1]) if "--cap" in sys.argv else 30
    grey = "--grey" in sys.argv   # test6: so print toning cannot stand in for the photographer
    screen = json.loads((src / "screen.json").read_text(encoding="utf-8"))
    exclude = json.loads((src / "exclude.json").read_text(encoding="utf-8"))
    meta = {m["file"]: m for m in json.loads((src / "manifest.json").read_text(encoding="utf-8"))}
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for artist_dir in sorted((src / "raw").iterdir()):
        artist = artist_dir.name
        skip = set(exclude.get(artist, []))
        files = [f for f in sorted(artist_dir.glob("*.jpg"))
                 if screen.get(f.name, {}).get("kept") and f.stem.split("_")[-1] not in skip]
        uniq, hashes, dropped = [], [], []
        for f in files:
            with Image.open(f) as im:
                h = imagehash.phash(ImageOps.exif_transpose(im).convert("RGB"))
            if any(h - g <= 8 for g in hashes):
                dropped.append(f.stem.split("_")[-1])
                continue
            hashes.append(h)
            uniq.append(f)
        idx = np.linspace(0, len(uniq) - 1, min(cap, len(uniq))).round().astype(int)
        chosen = [uniq[i] for i in dict.fromkeys(idx.tolist())]
        for f in chosen:
            with Image.open(f) as im:
                sq = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (1024, 1024),
                                  Image.LANCZOS, centering=(0.5, 0.5))
            if grey:
                sq = sq.convert("L").convert("RGB")
            dst = out / f.name
            sq.save(dst, "JPEG", quality=90)
            m = meta.get(f.name, {})
            manifest.append({"file": f.name, "artist": artist, "page": m.get("page"),
                             "license": m.get("license"), "title": m.get("title")})
        print(f"{artist:<8} passed {len(files):>3} · pHash dupes dropped {len(dropped):>2} {dropped} "
              f"· unique {len(uniq):>3} · chosen {len(chosen):>3}")
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
