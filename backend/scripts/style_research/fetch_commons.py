"""Collect public-domain illustrations per artist from Wikimedia Commons, politely.

    python -I fetch_commons.py list  <outdir>                 # crawl categories -> candidates.json
    python -I fetch_commons.py fetch <outdir> --per 60        # download scaled copies + manifest.json

Downloads go only into <outdir>/raw/<artist>/; nothing is executed from them.
"""
import json
import sys
import time
from pathlib import Path

import requests

API = "https://commons.wikimedia.org/w/api.php"
S = requests.Session()
S.headers["User-Agent"] = "CrucibleStyleTest/0.1 (local research script; github.com/Blandmarrow)"

ARTISTS = {
    "rackham": ["Category:Illustrations by Arthur Rackham"],
    "dulac": ["Category:Illustrations by Edmund Dulac"],
    "nielsen": ["Category:Kay Nielsen"],
    "bilibin": ["Category:Art works by Ivan Bilibin"],
    "goble": ["Category:Illustrations by Warwick Goble", "Category:Warwick Goble"],
    "bauer": ["Category:Illustrations by John Bauer", "Category:John Bauer"],
    "clarke": ["Category:Illustrations by Harry Clarke", "Category:Harry Clarke"],
}
SKIP_WORDS = ("portrait", "photograph", "grave", "signature", "cover", "title page", "stamp",
              "exhibition", "house", "museum", "statue", "plaque")


def api(params: dict) -> dict:
    params = {**params, "format": "json", "maxlag": 5}
    last = ""
    for attempt in range(6):
        time.sleep(1.0)
        try:
            r = S.get(API, params=params, timeout=60)
        except requests.RequestException as e:   # timeouts and resets: back off and retry
            last = repr(e)
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            d = r.json()
            if "error" not in d:
                return d
        last = f"{r.status_code} {r.text[:200]}"
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"API kept failing: {last}")


def crawl(root: str, depth: int = 3) -> list[str]:
    seen, files, queue = set(), [], [(root, 0)]
    while queue:
        cat, d = queue.pop(0)
        if cat in seen:
            continue
        seen.add(cat)
        cont = {}
        while True:
            res = api({"action": "query", "list": "categorymembers", "cmtitle": cat,
                       "cmtype": "file|subcat", "cmlimit": 500, **cont})
            for m in res.get("query", {}).get("categorymembers", []):
                if m["ns"] == 6:
                    files.append(m["title"])
                elif m["ns"] == 14 and d < depth and not any(w in m["title"].lower() for w in SKIP_WORDS):
                    queue.append((m["title"], d + 1))
            if "continue" not in res:
                break
            cont = res["continue"]
    return list(dict.fromkeys(files))


def info(titles: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(titles), 40):
        res = api({"action": "query", "titles": "|".join(titles[i:i + 40]), "prop": "imageinfo",
                   "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1280,
                   "iiextmetadatafilter": "LicenseShortName|Artist"})
        for p in res.get("query", {}).get("pages", {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            meta = ii.get("extmetadata", {})
            out.append({"title": p["title"], "mime": ii.get("mime"), "width": ii.get("width"),
                        "height": ii.get("height"), "thumb": ii.get("thumburl"),
                        "page": ii.get("descriptionurl"),
                        "license": meta.get("LicenseShortName", {}).get("value"),
                        "artist_meta": meta.get("Artist", {}).get("value", "")[:200]})
    return out


def cmd_list(outdir: Path) -> None:
    path = outdir / "candidates.json"
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for artist, roots in ARTISTS.items():
        if artist in result:
            print(f"{artist:<8} (cached) {len(result[artist])} usable", flush=True)
            continue
        titles = []
        for root in roots:
            titles += crawl(root, depth=2)
        titles = [t for t in dict.fromkeys(titles)
                  if not any(w in t.lower() for w in SKIP_WORDS)]
        rows = [r for r in info(titles)
                if r["mime"] in ("image/jpeg", "image/png", "image/tiff")
                and min(r["width"] or 0, r["height"] or 0) >= 700]
        result[artist] = rows
        lic = {}
        for r in rows:
            lic[r["license"]] = lic.get(r["license"], 0) + 1
        print(f"{artist:<8} {len(titles):>4} files · {len(rows):>4} usable · licences {lic}", flush=True)
        path.write_text(json.dumps(result, indent=1), encoding="utf-8")   # per artist, so a failure keeps the rest


def cmd_fetch(outdir: Path, per: int) -> None:
    import numpy as np
    cands = json.loads((outdir / "candidates.json").read_text(encoding="utf-8"))
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    mpath = outdir / "manifest.json"
    manifest = [m for m in (json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else [])
                if only and m["artist"] not in only]          # keep other artists' entries
    for artist, rows in cands.items():
        if only and artist not in only:
            continue
        # "No restrictions" is Flickr Commons' label for public-domain book scans.
        rows = [r for r in rows if (r["license"] or "").lower().startswith(
            ("public domain", "pd", "cc0", "no restrictions"))]
        if not rows:
            print(f"{artist}: no public-domain rows", flush=True)
            continue
        idx = np.linspace(0, len(rows) - 1, min(per, len(rows))).round().astype(int)
        d = outdir / "raw" / artist
        d.mkdir(parents=True, exist_ok=True)
        got = 0
        for i in dict.fromkeys(idx.tolist()):
            r = rows[i]
            dst = d / f"{artist}_{i:04d}.jpg"
            if not dst.exists():
                time.sleep(0.5)
                resp = S.get(r["thumb"], timeout=60)
                if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
                    continue
                dst.write_bytes(resp.content)
            manifest.append({"file": dst.name, "artist": artist, **r})
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
