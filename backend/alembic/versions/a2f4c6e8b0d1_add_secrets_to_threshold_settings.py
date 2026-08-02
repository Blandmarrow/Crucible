"""add hf_token, gelbooru_api_key and gelbooru_user_id to threshold_settings

Revision ID: a2f4c6e8b0d1
Revises: c8a1d3f5b7e2
Create Date: 2026-08-02

Three secrets that were previously readable only from .env / the OS environment, resolved
once at import time into the backend.config.settings singleton. Changing one meant editing
a file and restarting the server, which is a poor fit for an app that otherwise configures
itself from the Settings page, and made using a gated HuggingFace model a terminal chore.
They now live here too, and Settings -> API Keys writes them.

Three decisions are baked into these columns, so they are recorded here rather than only in
docs/dev/settings.md:

* **The DB value wins when non-empty, otherwise the .env/OS-env chain applies.** This is
  deliberately *not* 12-factor — the conventional ordering puts the environment on top.
  The reasoning is a failure mode, not a principle: a token typed into a field and silently
  overridden by an env var the user cannot see from the UI is the worst outcome available,
  whereas the reverse is visible (the API Keys tab states each secret's source).

* **Empty means inherit, not "no token".** Clearing a field stops overriding; it does not
  record "this secret is deliberately blank". That is why the column is nullable=False with
  a "" server_default and never NULL: "" is the single not-set sentinel, so a row's absence
  and a cleared field resolve identically, and the resolver needs no NULL branch.

* **Plaintext at rest.** No encryption: any key the process can decrypt on its own is
  stored beside its own key, so encryption here would buy obfuscation rather than secrecy.
  Crucible is an unauthenticated local app whose filesystem router already reads .env, and
  the LLM provider keys in `openai_providers.api_key` are stored the same way. The Settings
  tab says so in one line of UI copy rather than implying a protection that is not there.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2f4c6e8b0d1"
down_revision: str | Sequence[str] | None = "c8a1d3f5b7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "threshold_settings",
        sa.Column("hf_token", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        "threshold_settings",
        sa.Column("gelbooru_api_key", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        "threshold_settings",
        sa.Column("gelbooru_user_id", sa.String(length=500), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("threshold_settings", "gelbooru_user_id")
    op.drop_column("threshold_settings", "gelbooru_api_key")
    op.drop_column("threshold_settings", "hf_token")
