"""add the five rebindable keep/cut rating keys to threshold_settings

Revision ID: e7c4a1b9d2f6
Revises: d4a2c9e6b8f3
Create Date: 2026-11-02 00:00:00.000000

One key per rating action, defaulting to the digits the rating shipped with
(4 Keep, 3 Probably, 2 Probably not, 1 Cut, 0 clear). Server-side beside the label
hotkeys they can clash with, so both sets follow the install rather than the
browser. `""` means unbound — the row's existing not-set sentinel (`comfyui_url`),
never NULL, because the PATCH skips a `None`. No batch mode: NOT NULL with a server
default adds cleanly on SQLite, the `auto_unload_after_scoring` shape.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7c4a1b9d2f6"
down_revision: Union[str, None] = "d4a2c9e6b8f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEYS = (
    ("rating_key_keep", "4"),
    ("rating_key_probably", "3"),
    ("rating_key_probably_not", "2"),
    ("rating_key_cut", "1"),
    ("rating_key_clear", "0"),
)


def upgrade() -> None:
    for name, default in _KEYS:
        op.add_column(
            "threshold_settings",
            sa.Column(name, sa.String(1), server_default=default, nullable=False),
        )


def downgrade() -> None:
    for name, _ in reversed(_KEYS):
        op.drop_column("threshold_settings", name)
