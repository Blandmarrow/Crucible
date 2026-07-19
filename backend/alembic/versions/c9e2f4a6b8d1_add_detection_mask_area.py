"""add mask_area to detections (persisted coverage fraction) + backfill

Revision ID: c9e2f4a6b8d1
Revises: b7d1e4f8a2c5
Create Date: 2026-07-18

Adds detections.mask_area — the fraction (0-1) of the image covered by a
detection's geometry — so the Stats "Detections & Masks" coverage histogram can
read a column instead of parsing polygon JSON per request. The column is kept in
sync at runtime by attribute listeners on Detection.mask/Detection.bbox (see
backend/models/detection.py). This migration adds the column and backfills
existing rows with an inlined copy of the shoelace/bbox math (migrations must not
import app code). Best-effort: on any error the backfill is skipped with a
warning and rows stay NULL — consumers coalesce NULL→0 and the geometry
listeners heal each row on its next mask/bbox write.
"""
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e2f4a6b8d1"
down_revision: str | Sequence[str] | None = "b7d1e4f8a2c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _mask_area(mask_json, bbox):
    """Inlined copy of mask_utils.detection_mask_area (migrations import no app code)."""
    polygons = []
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


def upgrade() -> None:
    op.add_column("detections", sa.Column("mask_area", sa.Float(), nullable=True))

    try:
        conn = op.get_bind()
        rows = conn.execute(sa.text("SELECT id, bbox, mask FROM detections")).fetchall()
        updates = []
        for r in rows:
            bbox = r[1]
            if isinstance(bbox, str):
                try:
                    bbox = json.loads(bbox)
                except (ValueError, TypeError):
                    bbox = None
            updates.append({"row_id": r[0], "area": _mask_area(r[2], bbox)})
        stmt = sa.text("UPDATE detections SET mask_area = :area WHERE id = :row_id")
        for start in range(0, len(updates), 1000):
            conn.execute(stmt, updates[start : start + 1000])
    except Exception as e:
        print(
            f"WARNING: detections.mask_area backfill skipped ({e}); rows left NULL. "
            "They will be filled on the next mask/bbox write per detection."
        )


def downgrade() -> None:
    op.drop_column("detections", "mask_area")
