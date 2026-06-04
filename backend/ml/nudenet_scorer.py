import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_detector = None
_detector_lock = threading.Lock()


def _load_detector():
    """Load and cache the NudeNet NudeDetector (ONNX, CPU-only)."""
    global _detector
    if _detector is not None:
        return _detector
    with _detector_lock:
        if _detector is not None:
            return _detector
        from nudenet import NudeDetector
        logger.info("Loading NudeNet detector...")
        _detector = NudeDetector()
        logger.info("NudeNet detector loaded")
    return _detector


def detect_sync(image_path: str, min_prob: float = 0.5) -> list[dict]:
    """Run NudeNet body-part detection on a single image.

    Returns a list of dicts: {label, bbox [x1,y1,x2,y2] normalized 0-1, score}.
    Detections below min_prob are filtered out.
    """
    from PIL import Image

    detector = _load_detector()

    # NudeNet returns list[{"class": str, "score": float, "box": [x, y, w, h]}]
    raw = detector.detect(image_path)

    # Get image dimensions for normalization
    with Image.open(image_path) as img:
        W, H = img.size

    results = []
    for det in raw:
        score = float(det.get("score", 0.0))
        if score < min_prob:
            continue
        x, y, w, h = det.get("box", [0, 0, 0, 0])
        x1 = max(0.0, x / W)
        y1 = max(0.0, y / H)
        x2 = min(1.0, (x + w) / W)
        y2 = min(1.0, (y + h) / H)
        results.append({
            "label": det.get("class", "unknown"),
            "bbox": [x1, y1, x2, y2],
            "score": round(score, 4),
        })
    return results
