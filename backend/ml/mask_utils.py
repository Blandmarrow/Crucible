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
