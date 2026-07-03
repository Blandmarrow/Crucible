"""enable FK enforcement: orphan cleanup + ondelete rules on images/background_jobs

Revision ID: f7e8d9c0b1a2
Revises: b9d0e1f2a3c4
Create Date: 2026-07-02

Foreign-key enforcement (PRAGMA foreign_keys=ON) is turned on per-connection by the
connect listener in backend/database.py. Before enforcement can be safe:
  1. Purge pre-existing orphan rows that accumulated while enforcement was OFF.
  2. Give images.dataset_id an ON DELETE CASCADE and background_jobs.dataset_id an
     ON DELETE SET NULL so deleting a dataset doesn't raise / orphan rows.

The versioning tables (dataset_branches/dataset_versions/version_image_states) and the
detections table already declare the correct ondelete rules in their creating migrations,
so they are left untouched here.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "f7e8d9c0b1a2"
down_revision: str | None = "b9d0e1f2a3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Naming convention so batch mode can address the reflected (unnamed on SQLite) FKs.
_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def upgrade() -> None:
    # --- Step 1: purge orphans left behind while enforcement was OFF ---
    op.execute("DELETE FROM dataset_branches WHERE dataset_id NOT IN (SELECT id FROM datasets)")
    op.execute("DELETE FROM dataset_versions WHERE dataset_id NOT IN (SELECT id FROM datasets)")
    op.execute("DELETE FROM version_image_states WHERE version_id NOT IN (SELECT id FROM dataset_versions)")
    op.execute("DELETE FROM detections WHERE image_id NOT IN (SELECT id FROM images)")
    op.execute(
        "UPDATE background_jobs SET dataset_id = NULL "
        "WHERE dataset_id IS NOT NULL AND dataset_id NOT IN (SELECT id FROM datasets)"
    )

    # --- Step 2: recreate FKs with the desired ondelete rules ---
    with op.batch_alter_table("images", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_images_dataset_id_datasets", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_images_dataset_id_datasets", "datasets", ["dataset_id"], ["id"], ondelete="CASCADE"
        )

    with op.batch_alter_table("background_jobs", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_background_jobs_dataset_id_datasets", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_background_jobs_dataset_id_datasets", "datasets", ["dataset_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("background_jobs", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_background_jobs_dataset_id_datasets", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_background_jobs_dataset_id_datasets", "datasets", ["dataset_id"], ["id"]
        )

    with op.batch_alter_table("images", naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint("fk_images_dataset_id_datasets", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_images_dataset_id_datasets", "datasets", ["dataset_id"], ["id"]
        )
