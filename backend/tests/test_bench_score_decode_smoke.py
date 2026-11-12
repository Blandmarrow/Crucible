"""The decode-timing harness keeps its torch-free parts working (M10 T4).

`backend/scripts/bench_score_decode.py` is run by hand on a real card, since no CI job
installs torch. What CI can hold is everything around the GPU: the module imports without
torch, the fixtures it times are the ones it says, the dataset reader cannot write, and
the numbers it reports (stage shares, T5's ceiling, the full-run line) are computed
right. The twins' match with the app's scorers is checked by the script itself, at run
time, by its parity guard.
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from backend.scripts import bench_score_decode as bench
from backend.tests.conftest import needs_torch

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_imports_without_torch():
    # A fresh interpreter: this session may already have torch loaded by another test.
    code = (
        "import sys, backend.scripts.bench_score_decode; "
        "sys.exit(1 if 'torch' in sys.modules else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stderr or "importing the script imported torch"


def test_fixtures_are_built_then_reused(tmp_path):
    sets = bench.make_fixtures(tmp_path, jpegs=3, pngs=2, jpeg_size=(64, 48),
                               png_size=(80, 80))
    assert list(sets) == ["jpeg-64x48", "png-80x80"]
    jpegs, pngs = sets["jpeg-64x48"], sets["png-80x80"]
    assert len(jpegs) == 3 and len(pngs) == 2
    with Image.open(jpegs[0]) as img:
        assert (img.format, img.size, img.mode) == ("JPEG", (64, 48), "RGB")
    with Image.open(pngs[1]) as img:
        assert (img.format, img.size, img.mode) == ("PNG", (80, 80), "RGB")

    # The same recipe reuses the files untouched; a different one rebuilds them.
    stamp = pngs[0].stat().st_mtime_ns
    bench.make_fixtures(tmp_path, jpegs=3, pngs=2, jpeg_size=(64, 48), png_size=(80, 80))
    assert pngs[0].stat().st_mtime_ns == stamp
    again = bench.make_fixtures(tmp_path, jpegs=1, pngs=0, jpeg_size=(64, 48),
                                png_size=(80, 80))
    assert list(again) == ["jpeg-64x48"]
    assert not pngs[0].exists()


def test_fixtures_never_clear_a_folder_they_did_not_build(tmp_path):
    keep = tmp_path / "png" / "mine.png"
    keep.parent.mkdir()
    keep.write_bytes(b"not a fixture")
    with pytest.raises(SystemExit, match="refusing to clear"):
        bench.make_fixtures(tmp_path, jpegs=1, pngs=1, jpeg_size=(8, 8), png_size=(8, 8))
    assert keep.read_bytes() == b"not a fixture"


def test_fixtures_are_seeded(tmp_path):
    a = bench.make_fixtures(tmp_path / "a", jpegs=0, pngs=1, png_size=(32, 32))
    b = bench.make_fixtures(tmp_path / "b", jpegs=0, pngs=1, png_size=(32, 32))
    c = bench.make_fixtures(tmp_path / "c", jpegs=0, pngs=1, seed=1, png_size=(32, 32))
    read = lambda s: next(iter(s.values()))[0].read_bytes()  # noqa: E731
    assert read(a) == read(b)
    assert read(a) != read(c)


def _stage(decode, preprocess, transfer, forward):
    # Seconds in, as the twins report them.
    return {"decode": decode / 1000, "preprocess": preprocess / 1000,
            "transfer": transfer / 1000, "forward": forward / 1000}


def test_summary_shares_and_ceiling():
    samples = [_stage(6, 2, 1, 11), _stage(2, 2, 1, 15)]
    s = bench.summarize(samples, e2e=[0.019, 0.019])
    assert s["n"] == 2
    assert s["stages"]["decode"]["mean_ms"] == pytest.approx(4.0)
    assert s["stages"]["decode"]["share"] == pytest.approx(8 / 40)
    assert s["stages"]["forward"]["share"] == pytest.approx(26 / 40)
    assert sum(v["share"] for v in s["stages"].values()) == pytest.approx(1.0)
    assert s["total"]["mean_ms"] == pytest.approx(20.0)
    assert s["images_per_s"] == pytest.approx(50.0)
    assert (s["cpu_ms"], s["gpu_ms"]) == (pytest.approx(6.0), pytest.approx(14.0))
    # Serial 20 ms; overlap is bound by the 14 ms GPU half for k = 1 and k = 4 alike.
    assert s["t5_ceiling"]["1"] == pytest.approx(20 / 14)
    assert s["t5_ceiling"]["4"] == pytest.approx(20 / 14)
    assert s["overhead"] == pytest.approx(20 / 19 - 1)


def test_ceiling_when_the_cpu_half_dominates():
    # 30 ms of CPU against 10 of GPU: one worker leaves the CPU half the bound (40/30);
    # four workers make it 7.5 ms, so the GPU half binds (40/10).
    assert bench.t5_ceiling(30.0, 10.0, 1) == pytest.approx(40 / 30)
    assert bench.t5_ceiling(30.0, 10.0, 4) == pytest.approx(40 / 10)
    assert bench.t5_ceiling(0.0, 0.0, 1) == 1.0


def test_simulation_on_identical_images():
    # 6 ms CPU, 14 ms GPU, one worker: after the first CPU half the GPU never waits, so
    # 100 images take 6 + 100 * 14 ms against 2,000 ms serial.
    assert bench.simulate_t5([6.0] * 100, [14.0] * 100, 1, 4) == pytest.approx(2000 / 1406)
    assert bench.simulate_t5([6.0], [14.0], 1, 4) == pytest.approx(1.0)
    assert bench.simulate_t5([], [], 1, 4) == 1.0
    # CPU-heavy: four workers make the GPU the bound, close to the 4x ceiling.
    assert 3.5 < bench.simulate_t5([30.0] * 200, [10.0] * 200, 4, 4) <= 4.0


def test_simulation_feels_a_slow_image_the_mean_hides():
    # One 4K-sized CPU half among small ones stalls a queue only four deep.
    cs = ([2.0] * 9 + [200.0]) * 10
    gs = [10.0] * 100
    mean_bound = bench.t5_ceiling(sum(cs) / 100, sum(gs) / 100, 4)
    assert bench.simulate_t5(cs, gs, 4, 4) < mean_bound
    # A deeper queue absorbs more of it.
    assert bench.simulate_t5(cs, gs, 4, 4) < bench.simulate_t5(cs, gs, 4, 40)


def test_full_run_line():
    by_check = {
        "aesthetic": bench.summarize([_stage(10, 2, 1, 7)]),
        "clip": bench.summarize([_stage(10, 2, 1, 7)]),
        "aesthetic_v2_5": bench.summarize([_stage(10, 2, 1, 7)]),  # not in a full run
    }
    fr = bench.full_run(by_check)
    assert fr["checks"] == ["aesthetic", "clip"]
    assert fr["decode_ms_per_image"] == pytest.approx(20.0)
    assert fr["total_ms_per_image"] == pytest.approx(40.0)
    assert fr["shared_saving_ms"] == pytest.approx(10.0)
    assert fr["shared_saving_share"] == pytest.approx(0.25)
    # One image per check: nothing to overlap, so the run gains nothing.
    assert fr["t5_sim"]["4"] == pytest.approx(1.0)
    assert bench.full_run({"aesthetic_v2_5": by_check["aesthetic_v2_5"]}) is None


def test_full_run_weights_checks_by_time():
    # A 100 ms check that pipelines to 2x and a 10 ms one that cannot: the run gains
    # 110 / 60, not the 1.5x an average of the two speed-ups would claim.
    slow = {"n": 1, "stages": {"decode": {"mean_ms": 50.0}}, "total": {"mean_ms": 100.0},
            "t5_sim": {"1": 2.0, "4": 2.0}}
    fast = {"n": 1, "stages": {"decode": {"mean_ms": 1.0}}, "total": {"mean_ms": 10.0},
            "t5_sim": {"1": 1.0, "4": 1.0}}
    fr = bench.full_run({"aesthetic": slow, "nsfw": fast})
    assert fr["t5_sim"]["4"] == pytest.approx(110 / 60)


def test_same_output_reports_embedding_drift():
    import numpy as np

    a = np.array([0.5, 0.25], dtype=np.float16).tobytes()
    b = np.array([0.5, 0.375], dtype=np.float16).tobytes()
    assert bench.same_output(a, a) == (True, "")
    ok, why = bench.same_output(a, b)
    assert not ok and "0.125" in why
    assert bench.same_output(5.123, 5.123)[0]
    assert not bench.same_output({"watermark_score": 0.1}, {"watermark_score": 0.2})[0]


@needs_torch
def test_parity_hooks_see_a_lost_autocast_the_rounding_hides():
    import torch

    model = torch.nn.Linear(4, 2).eval()
    x = torch.ones(1, 4)

    def plain(_):
        with torch.no_grad():
            return round(float(model(x).sum()), 0)

    def autocast(_):
        with torch.no_grad(), torch.autocast("cpu", dtype=torch.bfloat16):
            return round(float(model(x).sum()), 0)

    a, rec_a = bench._captured(plain, ("",), [model])
    b, rec_b = bench._captured(autocast, ("",), [model])
    c, rec_c = bench._captured(plain, ("",), [model])
    assert bench.same_records(rec_a, rec_c) == (True, "")
    assert a == c
    ok, why = bench.same_records(rec_a, rec_b)
    assert not ok and "autocast" in why
    # Hooks come off again, so a timed call afterwards carries none.
    assert not model._forward_hooks and not model._forward_pre_hooks


def _scratch_db(path: Path, image_dir: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE datasets (id TEXT PRIMARY KEY, name TEXT);"
        "CREATE TABLE images (id TEXT, dataset_id TEXT, filename TEXT, file_path TEXT);"
    )
    conn.execute("INSERT INTO datasets VALUES ('d1', 'set-one')")
    for i, name in enumerate(["b.png", "a.png", "gone.png"]):
        conn.execute("INSERT INTO images VALUES (?, 'd1', ?, ?)",
                     (f"i{i}", name, str(image_dir / name)))
    conn.commit()
    conn.close()


def test_dataset_reader_is_read_only_and_ordered(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    for name in ("a.png", "b.png"):
        Image.new("RGB", (8, 8)).save(images / name)
    db = tmp_path / "scratch.db"
    _scratch_db(db, images)

    name, paths, missing = bench.load_dataset_paths(db, "set-one")
    assert name == "set-one"
    assert [p.name for p in paths] == ["a.png", "b.png"]
    assert missing == 1
    assert [p.name for p in bench.load_dataset_paths(db, "d1", limit=1)[1]] == ["a.png"]

    with pytest.raises(SystemExit, match="set-one"):
        bench.load_dataset_paths(db, "nope")

    conn = bench.ro_connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM images")
    finally:
        conn.close()


def test_checks_argument():
    parser = bench.build_parser()
    assert parser.parse_args([]).checks == bench.CHECKS
    assert parser.parse_args(["--checks", "dino, nsfw"]).checks == ("dino", "nsfw")
    with pytest.raises(SystemExit):
        parser.parse_args(["--checks", "dino,nope"])
    # Every check has a twin, and every grouped check is a real one.
    assert set(bench.TWINS) == set(bench.CHECKS)
    grouped = [c for _, checks in bench.MODEL_GROUPS for c in checks]
    assert sorted(grouped) == sorted(bench.CHECKS)
