"""nest library categories: widen comfy_library_prompts.category, normalize saved ones

Revision ID: e7d1c5a9b3f2
Revises: d5b7f9a1c3e6
Create Date: 2026-11-05 00:00:00.000000

M6 §8c shows library categories as a `/` tree, so a category is now a path of up to 512
characters (the request models' limit; SQLite never enforced the old 100). Categories
saved before were free text: `portraits / studio` and `portraits/studio` would be two
tree nodes that subtree moves (an exact prefix compare) treat as unrelated. This
normalizes every saved category by `services.comfy_library.normalize_category`'s rule,
inlined because a migration must not import app code that later changes. A category
left with no segment becomes `Uncategorized`. Normalizing can make two prompts in one
category duplicates by the library's rule; the older one is kept, as `library/move`
keeps the occupant.

`plan_normalize` is pure and tested directly (`test_migration_nest_library_categories.py`),
which is why `op` and `sqlalchemy` are imported inside `upgrade()`: CI's pytest install
has no alembic.
"""
from collections.abc import Iterable, Sequence

revision: str = "e7d1c5a9b3f2"
down_revision: str | None = "d5b7f9a1c3e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNCATEGORIZED = "Uncategorized"


def normalize(raw: str) -> str:
    parts = [p.strip() for p in raw.replace("\\", "/").split("/")]
    return "/".join(p for p in parts if p and p != ".") or UNCATEGORIZED


def _key(text: str) -> str:
    return " ".join(text.split()).lower()


def plan_normalize(
    rows: Iterable[tuple[str, str, str]],
) -> tuple[list[tuple[str, str]], list[str]]:
    """`rows` are `(id, category, text)`, oldest first. Returns `(updates, deletes)`:
    `(id, new_category)` for each prompt whose category changes, and the ids of prompts
    that normalizing made duplicates of an older one in the same category."""
    seen: set[tuple[str, str]] = set()
    updates: list[tuple[str, str]] = []
    deletes: list[str] = []
    for row_id, category, text in rows:
        new = normalize(category or "")
        key = (new, _key(text or ""))
        if key in seen:
            deletes.append(row_id)
            continue
        seen.add(key)
        if new != category:
            updates.append((row_id, new))
    return updates, deletes


def upgrade() -> None:
    import sqlalchemy as sa
    from alembic import op

    with op.batch_alter_table("comfy_library_prompts") as batch_op:
        batch_op.alter_column(
            "category",
            existing_type=sa.String(100),
            type_=sa.String(512),
            existing_nullable=False,
        )

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, category, text FROM comfy_library_prompts ORDER BY created_at, id"
    )).fetchall()
    updates, deletes = plan_normalize((r[0], r[1], r[2]) for r in rows)
    for row_id in deletes:
        conn.execute(sa.text("DELETE FROM comfy_library_prompts WHERE id = :id"), {"id": row_id})
    for row_id, category in updates:
        conn.execute(
            sa.text("UPDATE comfy_library_prompts SET category = :c WHERE id = :id"),
            {"c": category, "id": row_id},
        )


def downgrade() -> None:
    import sqlalchemy as sa
    from alembic import op

    # The normalization is not reversible; only the width is restored. A category longer
    # than 100 characters survives the downgrade, since SQLite does not enforce it.
    with op.batch_alter_table("comfy_library_prompts") as batch_op:
        batch_op.alter_column(
            "category",
            existing_type=sa.String(512),
            type_=sa.String(100),
            existing_nullable=False,
        )
