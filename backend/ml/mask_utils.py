"""Shared mask → polygon/bbox helpers used by the segmentation predictors."""

import numpy as np


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
