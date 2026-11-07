"""`FakeComfy(varied=True)`: seed-driven images that pass Crucible's technical gates."""
import io
import json

import httpx
from PIL import Image as PilImage

from backend.tests.conftest import needs_cv2
from backend.tests.fake_comfy import FakeComfy, graph_seed, varied_image

SEEDS = (1, 2, 3, 12345)


def _graph(seed=None, prompt="a cat", key="seed"):
    g = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt}},
        "2": {"class_type": "KSampler", "inputs": {"positive": ["1", 0]}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "run/x"}},
    }
    if seed is not None:
        g["2"]["inputs"][key] = seed
    return g


def _run(fake, graph):
    """Submit `graph`, return the pixels of its one output as raw RGB bytes."""
    r = fake.handler(httpx.Request("POST", "http://f/prompt", content=json.dumps({"prompt": graph, "client_id": "c"})))
    assert r.status_code == 200
    pid = r.json()["prompt_id"]
    entry = fake.handler(httpx.Request("GET", f"http://f/history/{pid}")).json()[pid]
    ref = next(iter(entry["outputs"].values()))["images"][0]
    png = fake.images[(ref["filename"], ref["subfolder"], ref["type"])]
    return PilImage.open(io.BytesIO(png)).convert("RGB")


def _fake(**kw):
    return FakeComfy(canvas_running=False, complete=True, **kw)


def test_graph_seed_rule():
    assert graph_seed(_graph()) is None
    assert graph_seed(_graph(7)) == 7
    assert graph_seed(_graph(9, key="noise_seed")) == 9
    assert graph_seed({"1": {"inputs": {"seed": True}}, "2": {"inputs": {"seed": "5"}}}) is None
    assert graph_seed({"1": {"inputs": {"noise_seed": 4}}, "2": {"inputs": {"seed": 8}}}) == 4


def test_same_seed_same_pixels_different_seed_different():
    fake = _fake(varied=True)
    a1, a2, b = _run(fake, _graph(5)), _run(fake, _graph(5)), _run(fake, _graph(6))
    assert a1.tobytes() == a2.tobytes()
    assert a1.tobytes() != b.tobytes()
    assert a1.size == (128, 128)


def test_varied_size_is_configurable():
    assert _run(_fake(varied=True, varied_size=64), _graph(5)).size == (64, 64)


def test_no_seed_falls_back_to_counter():
    fake = _fake(varied=True)
    a, b = _run(fake, _graph()), _run(fake, _graph())
    assert a.tobytes() != b.tobytes()  # counter 1 vs 2
    assert a.tobytes() == varied_image(1).tobytes()
    assert b.tobytes() == varied_image(2).tobytes()


def test_default_stays_flat():
    img = _run(_fake(), _graph(5))
    assert img.size == (32, 32) and len(set(img.getdata())) == 1


@needs_cv2
def test_varied_images_pass_gates_and_are_not_duplicates(tmp_path):
    import imagehash

    from backend.ml.technical_scorer import DUPLICATE_THRESHOLD, score_technical_sync

    fake = _fake(varied=True)
    hashes = {}
    for seed in SEEDS:
        img = _run(fake, _graph(seed))
        path = tmp_path / f"{seed}.png"
        img.save(path)
        s = score_technical_sync(str(path))
        assert not (s["is_blurry"] or s["is_noisy"] or s["is_uniform"]), (seed, s)
        hashes[seed] = imagehash.phash(img)
    for a in SEEDS:
        for b in SEEDS:
            if a < b:
                assert hashes[a] - hashes[b] >= DUPLICATE_THRESHOLD, (a, b)
    assert imagehash.phash(_run(fake, _graph(1))) - hashes[1] == 0


@needs_cv2
def test_blurry_marker_fails_blur_only(tmp_path):
    from backend.ml.technical_scorer import score_technical_sync

    img = _run(_fake(varied=True), _graph(5, prompt="a cat [[fake:blurry]]"))
    img.save(tmp_path / "b.png")
    s = score_technical_sync(str(tmp_path / "b.png"))
    assert s["is_blurry"] and not s["is_uniform"]
