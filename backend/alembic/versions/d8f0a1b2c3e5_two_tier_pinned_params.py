"""two-tier pinned params: per_row/value/int_mode on pins, drop plan seed_mode

Revision ID: d8f0a1b2c3e5
Revises: c7e9f1a2b3d4
Create Date: 2026-07-12

Pins gain per_row (True = queue column, False = run default), value (run-default
override) and int_mode (fixed|random|increment for integer params). Existing pins
become per_row=True (exactly today's behavior); the plan-level seed_mode moves onto
the pin aliased "seed" (case-insensitive) as its int_mode, then the column is dropped.
"""
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f0a1b2c3e5"
down_revision: str | Sequence[str] | None = "c7e9f1a2b3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, pinned_params, seed_mode FROM comfy_plans")).fetchall()
    stmt = sa.text("UPDATE comfy_plans SET pinned_params = :pins WHERE id = :plan_id")
    for plan_id, pins_json, seed_mode in rows:
        pins = json.loads(pins_json) if isinstance(pins_json, str) else (pins_json or [])
        for p in pins:
            p.setdefault("per_row", True)
            p.setdefault("value", None)
            is_seed = isinstance(p.get("alias"), str) and p["alias"].lower() == "seed"
            p.setdefault("int_mode", seed_mode if (is_seed and seed_mode in ("random", "increment")) else None)
        conn.execute(stmt, {"pins": json.dumps(pins), "plan_id": plan_id})
    op.drop_column("comfy_plans", "seed_mode")


def downgrade() -> None:
    op.add_column(
        "comfy_plans",
        sa.Column("seed_mode", sa.String(length=16), nullable=False, server_default="fixed"),
    )
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, pinned_params FROM comfy_plans")).fetchall()
    pin_stmt = sa.text("UPDATE comfy_plans SET pinned_params = :pins, seed_mode = :mode WHERE id = :plan_id")
    for plan_id, pins_json in rows:
        pins = json.loads(pins_json) if isinstance(pins_json, str) else (pins_json or [])
        mode = "fixed"
        for p in pins:
            if isinstance(p.get("alias"), str) and p["alias"].lower() == "seed" and p.get("int_mode") in ("random", "increment"):
                mode = p["int_mode"]
            p.pop("per_row", None)
            p.pop("value", None)
            p.pop("int_mode", None)
        conn.execute(pin_stmt, {"pins": json.dumps(pins), "mode": mode, "plan_id": plan_id})
