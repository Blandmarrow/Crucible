"""Shared mask → polygon/bbox helpers used by the segmentation predictors."""

import json

import numpy as np
from PIL import Image as PilImage, ImageDraw, ImageOps


def masks_to_polygons(masks: np.ndarray, img_w: int, img_h: int) -> list[list[list[float]]]:
    import cv2
    polygons = []
    for mask in masks:
        uint8 = (mask > 0).astype(np.uint8) * 255
        contours, _ = cv2.findContours(uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            epsilon = 0.005 * cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, epsilon, True)
            if len(approx) < 3:
                continue
            poly = [
                [round(float(pt[0][0]) / img_w, 4), round(float(pt[0][1]) / img_h, 4)]
                for pt in approx
            ]
            polygons.append(poly)
    return polygons


def rasterize_detections(
    detections: list[tuple[str | None, list[float] | None]],
    width: int,
    height: int,
    invert: bool = False,
) -> PilImage.Image:
    """Rasterize detections to a grayscale ("L") mask image.

    Each detection is a ``(mask_json, bbox)`` pair with normalized 0–1
    coordinates: ``mask_json`` is the Detection.mask polygon JSON
    (``{"polygons": [[[x, y], ...], ...]}``) or None; ``bbox`` is
    ``[x1, y1, x2, y2]``. Detections with polygons are filled as polygons;
    bbox-only detections (Florence-2, NudeNet) are filled as rectangles.
    White (255) marks detected regions; ``invert=True`` flips the result so
    white marks the background instead.
    """
    canvas = PilImage.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    for mask_json, bbox in detections:
        polygons = []
        if mask_json:
            try:
                polygons = json.loads(mask_json).get("polygons") or []
            except (ValueError, AttributeError):
                polygons = []
        polygons = [p for p in polygons if len(p) >= 3]
        if polygons:
            for poly in polygons:
                draw.polygon([(x * width, y * height) for x, y in poly], fill=255)
        elif bbox and len(bbox) == 4:
            x1, y1, x2, y2 = bbox
            draw.rectangle((x1 * width, y1 * height, x2 * width, y2 * height), fill=255)
    if invert:
        canvas = ImageOps.invert(canvas)
    return canvas


def detection_crop_rect(
    bboxes: list[list[float]],
    img_w: int,
    img_h: int,
    mode: str = "union",
    padding_pct: float = 0.0,
    target_ar: float | None = None,
) -> tuple[int, int, int, int] | None:
    """Compute a pixel-space crop rect (x, y, w, h) from normalized detection bboxes.

    ``bboxes`` are normalized ``[x1, y1, x2, y2]`` lists (already filtered to the
    labels of interest). ``mode`` picks the subject box: ``"union"`` is the
    envelope of all boxes, ``"largest"`` the single largest-area box. The box is
    then padded by ``padding_pct`` percent of its own extent per side and, when
    ``target_ar`` (width/height) is given, grown — never shrunk — toward that
    aspect ratio, clamped to the image. If the padded subject cannot fit inside
    any legal rect of the target ratio, the exact ratio is sacrificed rather
    than cutting the subject. Returns None when no usable bbox survives
    sanitizing or the result would be degenerate (< 1 px on a side).
    """
    boxes = []
    for b in bboxes:
        if not b or len(b) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(v) for v in b)
        except (TypeError, ValueError):
            continue
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1
        boxes.append([
            min(max(x1, 0.0), 1.0), min(max(y1, 0.0), 1.0),
            min(max(x2, 0.0), 1.0), min(max(y2, 0.0), 1.0),
        ])
    if not boxes:
        return None

    if mode == "largest":
        x1, y1, x2, y2 = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    else:
        x1 = min(b[0] for b in boxes)
        y1 = min(b[1] for b in boxes)
        x2 = max(b[2] for b in boxes)
        y2 = max(b[3] for b in boxes)

    pad_x = (x2 - x1) * padding_pct / 100.0
    pad_y = (y2 - y1) * padding_pct / 100.0
    x1 = max(x1 - pad_x, 0.0)
    y1 = max(y1 - pad_y, 0.0)
    x2 = min(x2 + pad_x, 1.0)
    y2 = min(y2 + pad_y, 1.0)

    min_w = min((x2 - x1) * img_w, float(img_w))
    min_h = min((y2 - y1) * img_h, float(img_h))
    cx = (x1 + x2) / 2.0 * img_w
    cy = (y1 + y2) / 2.0 * img_h

    if target_ar is not None:
        # Grow-only snap: smallest rect at target_ar containing the subject,
        # scaled to fit the image; then restore either subject minimum the
        # scaling violated (that is the best-effort break point — exact AR is
        # unreachable without cutting the subject).
        base_w = max(min_w, 1.0)
        base_h = max(min_h, 1.0)
        w0 = max(base_w, base_h * target_ar)
        h0 = w0 / target_ar
        s = min(1.0, img_w / w0, img_h / h0)
        w = min(max(w0 * s, min_w), float(img_w))
        h = min(max(h0 * s, min_h), float(img_h))
    else:
        w, h = min_w, min_h

    x = min(max(cx - w / 2.0, 0.0), img_w - w)
    y = min(max(cy - h / 2.0, 0.0), img_h - h)

    xi, yi, wi, hi = round(x), round(y), round(w), round(h)
    xi = min(max(xi, 0), img_w - 1)
    yi = min(max(yi, 0), img_h - 1)
    wi = min(wi, img_w - xi)
    hi = min(hi, img_h - yi)
    if wi < 1 or hi < 1:
        return None
    return (xi, yi, wi, hi)


def polygons_to_mask_input(
    mask_json: str | None,
    bbox: list[float] | None = None,
    size: int = 256,
    logit: float = 8.0,
) -> np.ndarray | None:
    """Rasterize a detection's polygon/bbox to a SAM2 ``mask_input`` logit map.

    SAM2's ``SAM2ImagePredictor.predict`` accepts ``mask_input`` as a
    ``(1, 256, 256)`` float32 array of *low-res logits*. SAM2 resizes the
    input image to a square (no aspect-preserving padding), so the normalized
    0–1 polygon coordinates map straight onto a ``size×size`` square canvas —
    that is the frame SAM2 expects. Returns ``None`` when the detection has no
    fillable geometry.

    NOTE (square-frame assumption): verified against the SAM2 source that the
    image is square-resized, not letterbox-padded. If an installed sam2 build
    pads instead, refined masks would be offset — fix here in isolation.
    """
    canvas = rasterize_detections([(mask_json, bbox)], size, size)
    arr = np.asarray(canvas)
    binary = arr > 127
    if not binary.any():
        return None
    out = np.where(binary, logit, -logit).astype(np.float32)
    return out.reshape(1, size, size)


def merge_detection_geometry(
    entries: list[tuple[str | None, list[float]]],
) -> tuple[str | None, list[float]]:
    """Merge several detections' geometry into one ``(mask_json, bbox)`` pair.

    ``entries`` is a list of ``(mask_json, bbox)`` pairs (normalized 0–1). The
    merged bbox is the union envelope of every sanitized/clamped/ordered box.
    Polygons from every entry are concatenated (no boolean union — overlapping
    polygons render/rasterize fine because each fills independently). When at
    least one entry contributes polygons, bbox-only entries contribute a
    rectangle polygon derived from their box so their region is not lost. When
    no entry has any polygon, the result is ``(None, union_bbox)``.
    """
    boxes: list[list[float]] = []
    polygons: list[list[list[float]]] = []
    any_poly = False
    parsed: list[tuple[list[list[float]], list[float] | None]] = []

    for mask_json, bbox in entries:
        polys: list[list[float]] = []
        if mask_json:
            try:
                polys = json.loads(mask_json).get("polygons") or []
            except (ValueError, AttributeError):
                polys = []
        polys = [p for p in polys if len(p) >= 3]

        clean_box: list[float] | None = None
        if bbox and len(bbox) == 4:
            try:
                x1, y1, x2, y2 = (float(v) for v in bbox)
            except (TypeError, ValueError):
                x1 = y1 = x2 = y2 = None  # type: ignore[assignment]
            else:
                if x1 > x2:
                    x1, x2 = x2, x1
                if y1 > y2:
                    y1, y2 = y2, y1
                clean_box = [
                    min(max(x1, 0.0), 1.0), min(max(y1, 0.0), 1.0),
                    min(max(x2, 0.0), 1.0), min(max(y2, 0.0), 1.0),
                ]
                boxes.append(clean_box)

        if polys:
            any_poly = True
        parsed.append((polys, clean_box))

    for polys, clean_box in parsed:
        if polys:
            polygons.extend(polys)
        elif any_poly and clean_box is not None:
            x1, y1, x2, y2 = clean_box
            polygons.append([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])

    if not boxes:
        union_bbox = [0.0, 0.0, 0.0, 0.0]
    else:
        union_bbox = [
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        ]

    mask_out = json.dumps({"polygons": polygons}) if polygons else None
    return mask_out, union_bbox


def bbox_from_mask(mask: np.ndarray, img_w: int, img_h: int) -> list[float]:
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any():
        return [0.0, 0.0, 0.0, 0.0]
    y1, y2 = np.where(rows)[0][[0, -1]]
    x1, x2 = np.where(cols)[0][[0, -1]]
    return [
        round(float(x1) / img_w, 4),
        round(float(y1) / img_h, 4),
        round(float(x2) / img_w, 4),
        round(float(y2) / img_h, 4),
    ]
