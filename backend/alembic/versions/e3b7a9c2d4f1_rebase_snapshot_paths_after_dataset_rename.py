"""rebase snapshot file paths left behind by a dataset rename (GPT-021)

Revision ID: e3b7a9c2d4f1
Revises: c8d1f3a5b709
Create Date: 2026-10-27

`VersionImageState.file_path` is absolute, and until this release
`rename_dataset` rebased only the live `images`/`videos` rows. Every snapshot
taken before a rename therefore still names the old root, and restoring it wrote
there — re-creating the old folder, or overwriting the files of a dataset that
had since taken the old name. The rename now rebases snapshot rows itself; this
migration repairs the ones already stale.

Data repair only, and deliberately conservative. Per dataset folder `F` (parent
`P`) and per version, the rows not under `F` are rebased only when **every** one
of them is provably a pre-rename path: under `P`, shaped
`P/<old_slug>/images/.../<filename>` with the last part equal to the row's own
`filename`, all sharing one `old_slug != F.name`, and none of the rebased paths
colliding with another row of the version. Anything else is left as is and a
WARNING is printed; restore's path gate (Pass 0c) refuses such a snapshot rather
than writing outside the dataset. Pure `pathlib` arithmetic — no filesystem I/O,
no `backend.config` import.

It also prints a count of live `images` rows outside their dataset folder (the
damage a pre-fix restore can leave). Those are not repaired here — no files are
moved by a migration; restore refuses to write over them, and a rescan or a
delete is the manual repair.

Downgrade is a no-op: the rebased paths are equally valid for the old code.
"""
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

import sqlalchemy as sa

# `op` is imported inside upgrade(): CI's pytest install has no alembic, and
# backend/tests/test_migration_rebase_snapshot_paths.py loads this file with
# importlib to test `plan_rebase` directly.

revision: str = "e3b7a9c2d4f1"
down_revision: str | Sequence[str] | None = "c8d1f3a5b709"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHUNK = 1000


def plan_rebase(
    folder_path: str, rows: Iterable[tuple[int, str, str, str]]
) -> tuple[list[tuple[int, str]], list[tuple[str, int, str]]]:
    """Decide which snapshot rows of one dataset to rebase onto `folder_path`.

    `rows` are `(state_id, version_id, file_path, filename)`. Returns
    `(updates, left)`: `updates` is `[(state_id, new_file_path)]`; `left` is
    `[(version_id, row_count, reason)]` for each version whose stray rows could
    not be proven safe and were left untouched.
    """
    folder = Path(folder_path)
    parent = folder.parent
    by_version: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for state_id, version_id, file_path, filename in rows:
        by_version[version_id].append((state_id, file_path or "", filename or ""))

    updates: list[tuple[int, str]] = []
    left: list[tuple[str, int, str]] = []
    for version_id, vrows in by_version.items():
        inside = {fp for _, fp, _ in vrows if fp and Path(fp).is_relative_to(folder)}
        stray = [r for r in vrows if r[1] not in inside]
        if not stray:
            continue

        reason = None
        slugs: set[str] = set()
        planned: list[tuple[int, str]] = []
        for state_id, fp, filename in stray:
            p = Path(fp) if fp else None
            if p is None or not p.is_absolute() or not p.is_relative_to(parent):
                reason = f"{fp!r} is not under {str(parent)!r}"
                break
            parts = p.relative_to(parent).parts
            if len(parts) < 3 or parts[1] != "images":
                reason = f"{fp!r} is not shaped <old_slug>/images/.../<file>"
                break
            if parts[-1] != filename:
                reason = f"{fp!r} does not end in its row's filename {filename!r}"
                break
            slugs.add(parts[0])
            planned.append((state_id, str(folder.joinpath(*parts[1:]))))
        if reason is None and len(slugs) != 1:
            reason = f"stray rows span {len(slugs)} old roots {sorted(slugs)}"
        if reason is None and folder.name in slugs:
            reason = "stray rows already name the current folder's slug"
        if reason is None:
            new_paths = [np for _, np in planned]
            if len(set(new_paths)) != len(new_paths) or inside & set(new_paths):
                reason = "rebased paths would collide within the version"
        if reason is not None:
            left.append((version_id, len(stray), reason))
            continue
        updates.extend(planned)
    return updates, left


def upgrade() -> None:
    from alembic import op

    conn = op.get_bind()
    datasets = conn.execute(sa.text("SELECT id, name, folder_path FROM datasets")).fetchall()
    stmt = sa.text("UPDATE version_image_states SET file_path = :fp WHERE id = :sid")
    rebased = 0
    for ds_id, ds_name, folder_path in datasets:
        if not folder_path:
            continue
        version_ids = conn.execute(
            sa.text("SELECT id FROM dataset_versions WHERE dataset_id = :ds"), {"ds": ds_id}
        ).scalars().all()
        for version_id in version_ids:
            rows = conn.execute(
                sa.text(
                    "SELECT id, version_id, file_path, filename "
                    "FROM version_image_states WHERE version_id = :v"
                ),
                {"v": version_id},
            ).fetchall()
            updates, left = plan_rebase(folder_path, [tuple(r) for r in rows])
            params = [{"sid": sid, "fp": fp} for sid, fp in updates]
            for start in range(0, len(params), _CHUNK):
                conn.execute(stmt, params[start : start + _CHUNK])
            rebased += len(updates)
            for vid, count, reason in left:
                print(
                    f"WARNING: dataset {ds_name!r}: snapshot {vid} has {count} row(s) "
                    f"outside {folder_path!r} that were left as is ({reason}). "
                    "Restoring that snapshot will be refused."
                )
    if rebased:
        print(f"Rebased {rebased} snapshot row(s) onto their dataset's current folder.")

    # Diagnostic only: live rows outside their own dataset folder.
    live = conn.execute(
        sa.text(
            "SELECT i.file_path, d.folder_path FROM images i "
            "JOIN datasets d ON d.id = i.dataset_id"
        )
    )
    outside = sum(
        1 for fp, folder in live
        if not fp or not folder or not Path(fp).is_relative_to(Path(folder))
    )
    if outside:
        print(
            f"WARNING: {outside} image row(s) point outside their dataset folder "
            "(possibly left by a restore after a dataset rename). They were not "
            "changed; restore refuses to write over them. Rescan or delete them to repair."
        )


def downgrade() -> None:
    # Data repair: the rebased paths are just as valid for the previous code.
    pass
