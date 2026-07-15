"""comfy_plans: output_node_id (single) -> output_node_ids (JSON list)

Revision ID: e2b3c4d5f6a7
Revises: d8f0a1b2c3e5
Create Date: 2026-07-12

Plans can import images from multiple workflow nodes. Existing single
selections become one-element lists; NULL becomes [] (auto).
"""
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b3c4d5f6a7"
down_revision: str | Sequence[str] | None = "d8f0a1b2c3e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("comfy_plans", sa.Column("output_node_ids", sa.JSON(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, output_node_id FROM comfy_plans")).fetchall()
    stmt = sa.text("UPDATE comfy_plans SET output_node_ids = :ids WHERE id = :plan_id")
    for plan_id, node_id in rows:
        conn.execute(stmt, {"ids": json.dumps([node_id] if node_id else []), "plan_id": plan_id})
    op.drop_column("comfy_plans", "output_node_id")


def downgrade() -> None:
    op.add_column("comfy_plans", sa.Column("output_node_id", sa.String(length=32), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, output_node_ids FROM comfy_plans")).fetchall()
    stmt = sa.text("UPDATE comfy_plans SET output_node_id = :nid WHERE id = :plan_id")
    for plan_id, ids_json in rows:
        ids = json.loads(ids_json) if isinstance(ids_json, str) else (ids_json or [])
        conn.execute(stmt, {"nid": ids[0] if ids else None, "plan_id": plan_id})
    op.drop_column("comfy_plans", "output_node_ids")
