"""Every ML inference path opens through `ml/image_utils.py`, EXIF-transposed.

CLAUDE.md states this as an invariant and `docs/dev/ml-models.md` restates it,
but for a long time it held only where someone remembered it: `aesthetic_scorer`
(four sites), `dino_scorer` (two) and `nsfw_scorer` (one) all used a bare
`Image.open(...).convert("RGB")` with no transpose. Nothing broke, because the
images this app curates are overwhelmingly generated (PNG/WebP out of ComfyUI)
and carry no orientation tag — but a folder import of phone photos is all it
takes.

Two tests, covering the two ways it goes wrong:

- **The rule itself**, enforced structurally over the module source, so the next
  scorer added with a bare open fails here rather than scoring sideways pixels
  in the field.
- **The path/bytes pair.** `extract_clip_embedding_sync` and
  `extract_clip_embedding_from_bytes_sync` feed the *same* style-similarity
  comparison from two entry points. Fixing only the path form would leave a
  reference image uploaded as bytes being compared against stored embeddings in
  a different orientation — a worse bug than the one being fixed, and invisible
  because both sides still return a well-formed vector.

Pixel-level, not model-level: the scorers need multi-GB weights, so these drive
`open_rgb`/`open_rgb_bytes` directly. That is where the orientation decision is
made; what CLIP then does with the pixels is not this module's business.
"""

import ast
from pathlib import Path

from PIL import Image

from backend.ml.image_utils import open_rgb, open_rgb_bytes

ML_DIR = Path(__file__).resolve().parents[1] / "ml"

# Inference paths only. `lut_processor` and `upscaler` are image-*processing*
# paths — they transpose inline and write the result to disk, which is governed
# by the `image_service._open_safe` half of the same invariant, not this one.
#
# The list covered four scorers for a long time, which left the rule unenforced
# in exactly the place a new module would break it: the captioners. All five of
# them, both SAM predictors and `nudenet_scorer` are inference paths by the same
# definition and are now in scope. None of them was an offender when they were
# added here — they route through `image_utils.open_rgb` or
# `image_utils.preprocess_for_caption` — so this is a guard against the next one,
# which is the only kind of guard that helps.
INFERENCE_MODULES = [
    "aesthetic_scorer.py",
    "dino_scorer.py",
    "florence_captioner.py",
    "joycaption_captioner.py",
    "nsfw_scorer.py",
    "nudenet_scorer.py",
    "ollama_captioner.py",
    "openai_compat_captioner.py",
    "paligemma_captioner.py",
    "sam2_predictor.py",
    "sam3_predictor.py",
    "wd14_tagger.py",
]

# Modules matching the naming patterns above that are deliberately out of scope,
# with the reason. Anything else new must be triaged into one list or the other —
# see `test_the_module_list_covers_every_inference_module`.
NOT_INFERENCE = {
    # cv2.imread, not PIL; its scores are computed on the stored frame and the
    # `_unmeasured()` contract, not orientation, is what governs it.
    "technical_scorer.py",
    # Operates on stored embeddings; never opens a file.
    "similarity_scorer.py",
}


def _exif_rotated_jpeg(tmp_path: Path) -> Path:
    """A landscape JPEG tagged orientation=6 ("rotate 90° CW to display").

    A viewer honouring the tag shows it portrait; a bare `Image.open` sees it
    landscape. The two are therefore distinguishable by `.size` alone, with no
    pixel comparison needed.
    """
    path = tmp_path / "rotated.jpg"
    img = Image.new("RGB", (64, 32), (10, 120, 200))
    exif = img.getexif()
    exif[274] = 6  # Orientation
    img.save(path, "JPEG", exif=exif)
    return path


def test_open_rgb_honours_the_exif_orientation_tag(tmp_path):
    """The fixture is only meaningful if a bare open really does differ."""
    path = _exif_rotated_jpeg(tmp_path)

    with Image.open(path) as raw:
        assert raw.size == (64, 32), "stored pixels are landscape"

    transposed = open_rgb(str(path))
    try:
        assert transposed.size == (32, 64), "open_rgb must apply orientation=6"
    finally:
        transposed.close()


def test_the_path_and_bytes_opens_agree_on_orientation(tmp_path):
    """The pair behind style similarity must not disagree about which way is up."""
    path = _exif_rotated_jpeg(tmp_path)
    raw_bytes = path.read_bytes()

    from_path = open_rgb(str(path))
    from_bytes = open_rgb_bytes(raw_bytes)
    try:
        assert from_path.size == from_bytes.size
        assert from_path.tobytes() == from_bytes.tobytes()
    finally:
        from_path.close()
        from_bytes.close()


def test_no_inference_module_opens_an_image_without_the_helper():
    """Structural guard: a bare `Image.open` in an inference module is the bug.

    Matches attribute calls named `open` on any PIL alias (`Image`, `PILImage`),
    which is every spelling these modules have used. `image_utils` itself is
    excluded — it is where the sanctioned open lives.
    """
    offenders = []
    for name in INFERENCE_MODULES:
        source = (ML_DIR / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (
                isinstance(fn, ast.Attribute)
                and fn.attr == "open"
                and isinstance(fn.value, ast.Name)
                and fn.value.id in {"Image", "PILImage"}
            ):
                offenders.append(f"{name}:{node.lineno}")

    assert offenders == [], (
        "these ML inference sites open an image without EXIF transposition; "
        "use backend.ml.image_utils.open_rgb / open_rgb_bytes instead: "
        + ", ".join(offenders)
    )


def test_the_module_list_covers_every_inference_module():
    """The list above is only a guard if adding a module updates it.

    A new captioner or predictor dropped into `backend/ml/` is invisible to the
    structural check until someone remembers to list it — which is how the four
    original entries stayed four while eight more inference paths shipped. This
    fails on the *next* one instead, and takes either answer: list it, or say why
    it is not an inference path in `NOT_INFERENCE`.
    """
    suffixes = ("_scorer.py", "_captioner.py", "_predictor.py", "_tagger.py")
    on_disk = {
        p.name for p in ML_DIR.glob("*.py")
        if p.name.endswith(suffixes)
    }
    untriaged = sorted(on_disk - set(INFERENCE_MODULES) - NOT_INFERENCE)
    assert untriaged == [], (
        "new ML modules are covered by neither list; add each to "
        "INFERENCE_MODULES, or to NOT_INFERENCE with the reason: "
        + ", ".join(untriaged)
    )
    stale = sorted(set(INFERENCE_MODULES) - on_disk)
    assert stale == [], "INFERENCE_MODULES names files that no longer exist: " + ", ".join(stale)
