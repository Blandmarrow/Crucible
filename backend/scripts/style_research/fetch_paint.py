"""Collect digital paintings per artist from Danbooru (test7).

    python -I fetch_paint.py list  <outdir>                 # post metadata only -> candidates.json
    python -I fetch_paint.py fetch <outdir> --per 45        # download originals + manifest.json

``fetch_booru.py`` with test7's rules (docs/dev/roadmap/style-media-runs/PREREGISTRATION.md):
each artist is queried with the medium tag it was found under, and ``solo`` alone is required.
Rating general is checked per post, as the medium tag uses the second anonymous tag slot.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))   # -I leaves the script's own folder off the path
import fetch_booru as fb  # noqa: E402

MEDIUM = {   # artist -> the tag it was found under; pre-registered order, reserves after hatsburg
    "tenjin_hidetaka": "realistic", "yuming_li": "painterly", "chan8016": "painterly",
    "nyte_tyde": "painterly", "vlfdus_0": "realistic", "fuyumurasaki": "painterly",
    "bm94199": "realistic", "hatsburg": "realistic",
    "kuto_(kuroto)": "painterly", "koriarredondo": "painterly", "john_tedrick": "realistic",
    "usagimarumei": "painterly", "maomi_jita": "painterly", "pluvium_grandis": "realistic",
    "hua_bucheng": "realistic",
}
fb.ARTISTS = list(MEDIUM)
fb.REQUIRED = {"solo"}


def crawl(artist: str) -> tuple[int, list[dict]]:
    seen, rows, before = 0, [], None
    for _ in range(fb.MAX_PAGES):
        params = {"tags": f"{artist} {MEDIUM[artist]}", "limit": 200}
        if before:
            params["page"] = f"b{before}"
        page = fb.get(params)
        if not page:
            break
        seen += len(page)
        rows += [{"id": p["id"], "ext": p["file_ext"], "width": p["image_width"],
                  "height": p["image_height"], "created": p["created_at"][:10],
                  "url": p["file_url"], "page": fb.PAGE.format(p["id"]), "md5": p.get("md5")}
                 for p in page if fb.eligible(p)]   # eligible() checks rating "g"
        before = min(p["id"] for p in page)
    return seen, rows


fb.crawl = crawl

if __name__ == "__main__":
    from pathlib import Path
    cmd, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    if cmd == "list":
        fb.cmd_list(out)
    else:
        fb.cmd_fetch(out, int(sys.argv[sys.argv.index("--per") + 1]) if "--per" in sys.argv else 45)
