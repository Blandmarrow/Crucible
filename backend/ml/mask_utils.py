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


def detection_mask_area(mask_json: str | None, bbox: list | None) -> float | None:
    """Estimate a detection's area as a fraction (0–1) of the whole image.

    Coordinates in both ``mask_json`` polygons and ``bbox`` are normalized 0–1,
    so the shoelace polygon area *is* the image-area fraction directly. When
    ``mask_json`` has one or more valid polygons (≥3 points each), returns the
    summed absolute shoelace area of those polygons (overlapping polygons
    overcount — acceptable for the coverage-QA histogram, which is explicitly an
    approximation, not a rasterized union). Falls back to the bbox rectangle area
    ``(x2-x1)*(y2-y1)`` when there are no usable polygons and ``bbox`` has four
    elements. Returns ``None`` when neither yields geometry. Tolerates malformed
    JSON like :func:`rasterize_detections`. The result is clamped to [0, 1].
    """
    polygons: list = []
    if mask_json:
        try:
            polygons = json.loads(mask_json).get("polygons") or []
        except (ValueError, AttributeError):
            polygons = []
    polygons = [p for p in polygons if len(p) >= 3]

    if polygons:
        total = 0.0
        for poly in polygons:
            area = 0.0
            n = len(poly)
            for i in range(n):
                x1, y1 = poly[i][0], poly[i][1]
                x2, y2 = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
                area += x1 * y2 - x2 * y1
            total += abs(area) / 2.0
        return min(max(total, 0.0), 1.0)

    if bbox and len(bbox) == 4:
        try:
            x1, y1, x2, y2 = (float(v) for v in bbox)
        except (TypeError, ValueError):
            return None
        area = abs(x2 - x1) * abs(y2 - y1)
        return min(max(area, 0.0), 1.0)

    return None


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


def compose_loss_mask(
    include: list[tuple[str | None, list[float] | None]],
    exclude: list[tuple[str | None, list[float] | None]],
    width: int,
    height: int,
    invert: bool = False,
) -> PilImage.Image:
    """Compose a loss mask from include detections minus excluded regions.

    The base mask marks the trainable region: ``rasterize_detections(include,
    w, h, invert)`` when ``include`` is non-empty, else a full-white
    ``PilImage.new("L", (w, h), 255)`` regardless of ``invert`` — an all-black
    mask would zero the image's loss entirely, so an image with no include
    geometry trains unmasked.

    Excluded regions are then punched out of that base: when ``exclude`` is
    non-empty, ``rasterize_detections(exclude, w, h)`` (never inverted) is
    pasted as black (0). Because rasterized draws are binary 0/255, the paste
    is an exact hole-punch. Applied *after* the (possibly inverted) base, so
    exclusion is subtracted after invert.
    """
    if include:
        base = rasterize_detections(include, width, height, invert)
    else:
        base = PilImage.new("L", (width, height), 255)
    if exclude:
        excl = rasterize_detections(exclude, width, height)
        base.paste(0, mask=excl)
    return base


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


def remap_detection_geometry(
    mask_json: str | None,
    bbox: list[float] | None,
    rect: tuple[int, int, int, int],
    old_size: tuple[int, int],
) -> tuple[str | None, list[float]] | None:
    """Remap a detection's normalized geometry into a crop's frame.

    ``rect`` is the crop rectangle ``(x, y, w, h)`` in pixels of the OLD image's
    EXIF-transposed frame (the frame detection coordinates live in — see
    ``open_rgb`` / ``_open_safe``); ``old_size`` is ``(old_w, old_h)`` of that
    frame. A normalized coordinate ``n`` on an axis maps to
    ``(n * old_dim - offset) / extent`` (``offset``/``extent`` = the rect's origin
    and size on that axis), clamped to [0, 1] — i.e. re-expressed as a fraction of
    the crop.

    The bbox is the primary, non-nullable geometry: when its remapped extent falls
    below 0.002 on either axis (mirroring ``_sanitize_bbox``) the detection lies
    outside the crop and the function returns ``None`` to signal the row should be
    deleted. Polygon vertices are remapped and clamped (polygons crossing the crop
    edge flatten against the border — accepted); a remapped polygon whose shoelace
    area drops below 1e-4 is dropped. When a masked detection loses every polygon
    this way the mask becomes ``None`` while the still-valid bbox is kept, yielding
    ``(None, bbox)``.

    Returns ``(new_mask_json_or_None, new_bbox)``, or ``None`` (delete the row).
    """
    rx, ry, rw, rh = rect
    old_w, old_h = old_size
    if rw <= 0 or rh <= 0 or old_w <= 0 or old_h <= 0:
        return None

    def _mx(n: float) -> float:
        return min(max((n * old_w - rx) / rw, 0.0), 1.0)

    def _my(n: float) -> float:
        return min(max((n * old_h - ry) / rh, 0.0), 1.0)

    # --- bbox (primary geometry, always present) ---
    if not bbox or len(bbox) != 4:
        return None
    try:
        bx1, by1, bx2, by2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if bx1 > bx2:
        bx1, bx2 = bx2, bx1
    if by1 > by2:
        by1, by2 = by2, by1
    nbx1, nby1, nbx2, nby2 = _mx(bx1), _my(by1), _mx(bx2), _my(by2)
    if (nbx2 - nbx1) < 0.002 or (nby2 - nby1) < 0.002:
        return None
    new_bbox = [round(nbx1, 4), round(nby1, 4), round(nbx2, 4), round(nby2, 4)]

    # --- polygons (optional) ---
    polygons: list = []
    if mask_json:
        try:
            polygons = json.loads(mask_json).get("polygons") or []
        except (ValueError, AttributeError):
            polygons = []
    polygons = [p for p in polygons if len(p) >= 3]

    new_polys: list[list[list[float]]] = []
    for poly in polygons:
        remapped: list[list[float]] = []
        for pt in poly:
            try:
                px, py = float(pt[0]), float(pt[1])
            except (TypeError, ValueError, IndexError):
                continue
            remapped.append([round(_mx(px), 4), round(_my(py), 4)])
        if len(remapped) < 3:
            continue
        area = 0.0
        n = len(remapped)
        for i in range(n):
            x1, y1 = remapped[i]
            x2, y2 = remapped[(i + 1) % n]
            area += x1 * y2 - x2 * y1
        if abs(area) / 2.0 < 1e-4:
            continue
        new_polys.append(remapped)

    mask_out = json.dumps({"polygons": new_polys}) if new_polys else None
    return mask_out, new_bbox


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
