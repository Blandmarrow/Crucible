"""Cut one frame per shot from Blender's open films (test8).

    python -I fetch_films.py fetch  <outdir>               # download the films -> sources.json
    python -I fetch_films.py frames <outdir> --per 45      # frames + manifest.json

The rules are test8's in docs/dev/roadmap/style-media-runs/PREREGISTRATION.md: shot cuts from
ffmpeg's scene score (> 0.3), one frame at the middle of each shot of at least 1 s, an even
spread of --per over those shots, letterboxing found by ``cropdetect`` and cut off first. Each
frame is saved as its centre square at 800 px, so ``finalize.py``'s 1024 resize gives every
film the same effective resolution.

Same layout as the other fetchers (``raw/<film>/<film>_NNNN.jpg`` plus ``manifest.json``), so
``screen.py`` and ``finalize.py`` run on it unchanged. Films go in <outdir>/src/; ffmpeg only
decodes them. Films and frames are not committed.
"""
import hashlib
import json
import re
import subprocess
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import requests

S = requests.Session()
S.headers["User-Agent"] = "CrucibleStyleTest/0.1 (local research script; github.com/Blandmarrow)"
IA = "https://archive.org/download/{}/{}"
BL = "https://download.blender.org/demo/movies/"

# film -> (page, download URL); the pre-registered order. Coffee Run is left out (536 px).
FILMS = {
    "elephants_dream": ("ElephantsDream", "ed_hd.avi"),
    "big_buck_bunny": (None, BL + "BBB/bbb_sunflower_2160p_30fps_normal.mp4.zip"),
    "sintel": ("Sintel", "sintel-2048-surround.mp4"),
    "cosmos_laundromat": ("CosmosLaundromatFirstCycle", "Cosmos Laundromat - First Cycle (1080p).mp4"),
    "glass_half": ("GlassHalf1080p", "Glass Half-1080p.mp4"),
    "caminandes_3": ("CaminandesLlamigos", "Caminandes_ Llamigos-1080p.mp4"),
    "agent_327": ("agent327operationbarbershop", "agent327.mkv"),
    "spring": ("spring_blenderopenmovie", "Spring - Blender Open Movie - YouTube.mkv"),
    "sprite_fright": ("sprite-fright-blender-open-movie", "Sprite Fright - Blender Open Movie.mkv"),
    "charge": ("charge-blender-open-movie-1608p", "Charge - Blender Open Movie 1608p.mp4"),
    "wing_it": ("wing_it", "wing_it.mp4"),
}
SCENE = 0.15                     # amendment 1: 0.3 missed most cuts
MIN_SHOT = 1.0
SIDE = 800


def url_of(film: str) -> tuple[str, str]:
    item, name = FILMS[film]
    if item is None:
        return BL + "BBB/", name
    return f"https://archive.org/details/{item}", IA.format(item, quote(name))


def cmd_fetch(outdir: Path) -> None:
    src = outdir / "src"
    src.mkdir(parents=True, exist_ok=True)
    spath = outdir / "sources.json"
    sources = json.loads(spath.read_text(encoding="utf-8")) if spath.exists() else {}
    for film in FILMS:
        if film in sources:
            print(f"{film:<18} (cached)", flush=True)
            continue
        page, url = url_of(film)
        ext = url.rsplit(".", 1)[-1]
        dst = src / f"{film}.{ext}"
        t0 = time.time()
        with S.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dst, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        h = hashlib.sha256(dst.read_bytes()).hexdigest()
        if ext == "zip":   # the BBB zip holds one mp4
            with zipfile.ZipFile(dst) as z:
                inner = next(n for n in z.namelist() if n.endswith(".mp4"))
                video = src / f"{film}.mp4"
                video.write_bytes(z.read(inner))
            dst.unlink()
            dst = video
        sources[film] = {"page": page, "url": url, "sha256": h, "file": dst.name}
        spath.write_text(json.dumps(sources, indent=1), encoding="utf-8")
        print(f"{film:<18} {dst.stat().st_size / 1e6:>7.0f} MB | {time.time() - t0:.0f} s", flush=True)


def ffmpeg(*args: str) -> str:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stderr


def duration(video: Path) -> float:
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", ffmpeg("-i", str(video)))
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def letterbox(video: Path) -> str:
    """The most common cropdetect result over one frame every 10 s."""
    log = ffmpeg("-i", str(video), "-vf", "fps=1/10,cropdetect=limit=24:round=2:reset=1",
                 "-an", "-f", "null", "-")
    crops = re.findall(r"crop=(\d+:\d+:\d+:\d+)", log)
    return Counter(crops).most_common(1)[0][0]


def cuts(video: Path) -> list[float]:
    log = ffmpeg("-i", str(video), "-vf", f"scale=480:-2,select='gt(scene,{SCENE})',showinfo",
                 "-an", "-f", "null", "-")
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", log)]


def cmd_frames(outdir: Path, per: int) -> None:
    import numpy as np
    sources = json.loads((outdir / "sources.json").read_text(encoding="utf-8"))
    manifest, shots_log = [], {}
    for film, s in sources.items():
        video = outdir / "src" / s["file"]
        dur = duration(video)
        crop = letterbox(video)
        bounds = [0.0, *cuts(video), dur]
        mids = [(a + b) / 2 for a, b in zip(bounds, bounds[1:]) if b - a >= MIN_SHOT]
        idx = np.linspace(0, len(mids) - 1, min(per, len(mids))).round().astype(int)
        d = outdir / "raw" / film
        d.mkdir(parents=True, exist_ok=True)
        vf = (f"crop={crop},crop='min(iw,ih)':'min(iw,ih)',"
              f"scale={SIDE}:{SIDE}:flags=lanczos")
        got = 0
        for i in dict.fromkeys(idx.tolist()):
            dst = d / f"{film}_{i:04d}.jpg"
            if not dst.exists():
                ffmpeg("-ss", f"{mids[i]:.3f}", "-i", str(video), "-frames:v", "1", "-vf", vf,
                       "-q:v", "2", str(dst))
            if dst.exists():
                manifest.append({"file": dst.name, "artist": film, "page": s["page"],
                                 "license": "CC-BY", "title": f"{film} @ {mids[i]:.2f} s",
                                 "time": round(mids[i], 3), "crop": crop})
                got += 1
        shots_log[film] = {"duration": round(dur, 1), "crop": crop, "cuts": len(bounds) - 2,
                           "shots_ge_1s": len(mids), "frames": got}
        print(f"{film:<18} {dur:>6.0f} s | crop {crop:<18} | {len(mids):>4} shots >= {MIN_SHOT:g} s "
              f"· {got} frames", flush=True)
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (outdir / "shots.json").write_text(json.dumps(shots_log, indent=1), encoding="utf-8")


if __name__ == "__main__":
    cmd, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    if cmd == "fetch":
        cmd_fetch(out)
    else:
        cmd_frames(out, int(sys.argv[sys.argv.index("--per") + 1]) if "--per" in sys.argv else 45)
