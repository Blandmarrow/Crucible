"""Collect FSA-era public-domain photographs per photographer from Wikimedia Commons (test6).

    python -I fetch_photos.py list  <outdir>                 # crawl categories -> candidates.json
    python -I fetch_photos.py fetch <outdir> --per 45        # download scaled copies + manifest.json

``fetch_commons.py`` with test6's roots and limits (docs/dev/roadmap/style-media-runs/
PREREGISTRATION.md): 1920 px wide copies of originals whose short side is at least 1024.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))   # -I leaves the script's own folder off the path
import fetch_commons as fc  # noqa: E402

fc.ARTISTS = {   # the pre-registered order; hine is the reserve
    "delano": ["Category:Photographs by Jack Delano"],
    "lange": ["Category:Photographs by Dorothea Lange"],
    "wolcott": ["Category:Photographs by Marion Post Wolcott"],
    "rothstein": ["Category:Photographs by Arthur Rothstein"],
    "vachon": ["Category:Photographs by John Vachon"],
    "shahn": ["Category:Photographs by Ben Shahn"],
    "evans": ["Category:Photographs by Walker Evans"],
    "mydans": ["Category:Photographs by Carl Mydans"],
    "hine": ["Category:Photographs by Lewis Hine"],
}
# fetch_commons' own list drops "portrait" and "photograph", which would drop every root here.
fc.SKIP_WORDS = ("portrait of", "grave", "signature", "stamp", "exhibition", "museum", "statue",
                 "plaque")
fc.THUMB_WIDTH = 1920               # a standard Commons width: 2048 drew 429s (amendment 5)
fc.MIN_SIDE = 1024
fc.INFO_BATCH = 15                  # Hine's long NARA titles overflow the URL at 40

if __name__ == "__main__":
    from pathlib import Path
    cmd, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    if cmd == "list":
        fc.cmd_list(out)
    else:
        fc.cmd_fetch(out, int(sys.argv[sys.argv.index("--per") + 1]) if "--per" in sys.argv else 45)
