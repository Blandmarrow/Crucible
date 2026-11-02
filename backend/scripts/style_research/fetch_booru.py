"""Collect one-medium anime illustrations per artist from Danbooru, politely.

    python -I fetch_booru.py list  <outdir>                 # post metadata only -> candidates.json
    python -I fetch_booru.py fetch <outdir> --per 60        # download originals + manifest.json

The same layout as ``fetch_commons.py`` (``raw/<artist>/<artist>_NNNN.jpg`` plus
``manifest.json``), so ``screen.py`` and ``finalize.py`` run on it unchanged.

Eligibility is fixed here, before any image is seen (see the run's PREREGISTRATION.md):
rating general, ``1girl`` and ``solo`` (so the subject is the same for every artist), no
comic/monochrome/sketch/traditional/3D/pixel-art/animated tags, not a variant (no parent, no
children), not deleted or banned, a still image, and the short side at least 1024 px so the
1024² crop in ``finalize.py`` never upscales. Downloads go only into <outdir>/raw/<artist>/;
nothing is executed from them. Images are research inputs and are not committed; the
manifest keeps each post's id and page URL.
"""
import json
import sys
import time
from pathlib import Path

import requests

API = "https://danbooru.donmai.us/posts.json"
PAGE = "https://danbooru.donmai.us/posts/{}"
S = requests.Session()
S.headers["User-Agent"] = "CrucibleStyleTest/0.1 (local research script; github.com/Blandmarrow)"

# Artist tag per style, in the pre-registered order. The first ARTISTS_WANTED that clear
# MIN_ELIGIBLE are used; the rest are reserves.
ARTISTS = ["tetenka", "edomura_niniko", "suzuneiroekaki", "jinri_shijie", "premium_(prmsub)",
           "pappi", "nashi_(shi_ra_nu_i)", "apricotlemontea"]   # absent from Kaloscope's classes
ARTISTS_WANTED = 8
MIN_ELIGIBLE = 25
MAX_PAGES = 15                   # 200 posts a page, newest first

REQUIRED = {"1girl", "solo"}
EXCLUDED = {"comic", "4koma", "monochrome", "greyscale", "sketch", "lineart", "partially_colored",
            "spot_color", "traditional_media", "3d", "pixel_art", "animated", "video",
            "photo_(medium)", "multiple_views", "character_sheet", "reference_sheet", "chibi",
            "oekaki", "screencap", "cosplay_photo", "ai-generated", "ai-assisted"}
EXTS = {"jpg", "png", "webp"}
MIN_SIDE = 1024


def get(params: dict) -> list[dict]:
    last = ""
    for attempt in range(6):
        time.sleep(1.0)
        try:
            r = S.get(API, params=params, timeout=60)
        except requests.RequestException as e:
            last = repr(e)
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        last = f"{r.status_code} {r.text[:200]}"
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"API kept failing: {last}")


def eligible(p: dict) -> bool:
    tags = set(p.get("tag_string_general", "").split()) | set(p.get("tag_string_meta", "").split())
    return (p.get("rating") == "g" and REQUIRED <= tags and not (EXCLUDED & tags)
            and not p.get("parent_id") and not p.get("has_children")
            and not p.get("is_deleted") and not p.get("is_banned")
            and p.get("file_ext") in EXTS and p.get("file_url")
            and min(p.get("image_width") or 0, p.get("image_height") or 0) >= MIN_SIDE)


def crawl(artist: str) -> tuple[int, list[dict]]:
    seen, rows, before = 0, [], None
    for _ in range(MAX_PAGES):
        params = {"tags": f"{artist} rating:g", "limit": 200}   # two tags: the anonymous limit
        if before:
            params["page"] = f"b{before}"
        page = get(params)
        if not page:
            break
        seen += len(page)
        rows += [{"id": p["id"], "ext": p["file_ext"], "width": p["image_width"],
                  "height": p["image_height"], "created": p["created_at"][:10],
                  "url": p["file_url"], "page": PAGE.format(p["id"]), "md5": p.get("md5")}
                 for p in page if eligible(p)]
        before = min(p["id"] for p in page)
    return seen, rows


def cmd_list(outdir: Path) -> None:
    path = outdir / "candidates.json"
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    chosen = []
    for artist in ARTISTS:
        if artist not in result:
            seen, rows = crawl(artist)
            result[artist] = rows
            path.write_text(json.dumps(result, indent=1), encoding="utf-8")
            print(f"{artist:<28} {seen:>5} rating:g posts · {len(rows):>4} eligible", flush=True)
        else:
            print(f"{artist:<28} (cached) {len(result[artist]):>4} eligible", flush=True)
        if len(result[artist]) >= MIN_ELIGIBLE and len(chosen) < ARTISTS_WANTED:
            chosen.append(artist)
    (outdir / "chosen.json").write_text(json.dumps(chosen, indent=1), encoding="utf-8")
    print(f"chosen ({len(chosen)}): {', '.join(chosen)}")


def cmd_fetch(outdir: Path, per: int) -> None:
    import numpy as np
    cands = json.loads((outdir / "candidates.json").read_text(encoding="utf-8"))
    chosen = json.loads((outdir / "chosen.json").read_text(encoding="utf-8"))
    manifest = []
    for artist in chosen:
        rows = sorted(cands[artist], key=lambda r: r["id"])
        idx = np.linspace(0, len(rows) - 1, min(per, len(rows))).round().astype(int)
        d = outdir / "raw" / artist
        d.mkdir(parents=True, exist_ok=True)
        got = 0
        for i in dict.fromkeys(idx.tolist()):
            r = rows[i]
            dst = d / f"{artist}_{i:04d}.jpg"
            if not dst.exists():
                time.sleep(1.0)
                resp = S.get(r["url"], timeout=120)
                if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
                    continue
                # Saved under .jpg whatever the format, as screen.py globs *.jpg; PIL reads by
                # content. The original extension is kept in the manifest for the format check.
                dst.write_bytes(resp.content)
            manifest.append({"file": dst.name, "artist": artist, "license": None,
                             "title": f"danbooru #{r['id']}", **r})
            got += 1
        print(f"{artist}: {got} downloaded", flush=True)
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")


if __name__ == "__main__":
    cmd, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    if cmd == "list":
        cmd_list(out)
    else:
        cmd_fetch(out, int(sys.argv[sys.argv.index("--per") + 1]) if "--per" in sys.argv else 60)
