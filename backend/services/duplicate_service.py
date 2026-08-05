"""The `is_duplicate` invariant: a relationship, not a column value.

`is_duplicate` + `duplicate_of` describe an image's membership of a group whose
root is *another row*. Nothing else in the codebase is shaped like that, so both
of the ways derived relational state goes wrong applied here at once: the scan
only ever *added* flags, and no delete path ever removed one. Prune a group by
hand down to a single survivor and that survivor stayed flagged as a duplicate of
an image that no longer existed — visible on the Stats *Duplicate* card, the
gallery *Flagged: duplicate* filter and badge, export `exclude_flags`, and the
captioning/detection/LUT exclusion filters — and re-running the Technical scorer
repaired none of it. See `docs/dev/postmortems/PM-022-orphaned-duplicate-flags.md`.

Two entry points, one per direction:

- `prune_orphaned_duplicate_flags` — the incremental post-delete cleanup, called
  by every path that deletes `Image` rows.
- `apply_duplicate_groups` — the authoritative reconciliation the Technical scan
  runs, which clears every flag it does not itself write.

Pure business logic, no HTTP, and it imports only `backend.models` and
`backend.utils.chunked` — so it is unit-testable without cv2, torch or the job
queue, and cannot cycle with `dataset_service`.

Six delete sites call the prune, and three deliberately do **not** — stated here
so the next sweep does not "finish the conversion":

- `images.delete_image`, `images.batch_delete`, `images.bulk_delete_filtered`,
  `filesystem.delete_path`, `videos._delete_previous_frames`, and
  `quality.resolve_duplicates` (a backstop for a partial resolve) all prune.
- `comfy.py`'s failure-path cleanup does not: those rows were generated seconds
  earlier and have never been through a duplicate scan.
- `version_service.restore_snapshot`'s extras removal does not: a restore
  rewrites `quality_flags` wholesale from `VersionImageState`, so its output is
  snapshot-defined rather than delete-defined. A re-scan is the repair.
- `datasets.delete_dataset` does not: the whole dataset goes, so there are no
  survivors to orphan.
"""
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.image import Image
from backend.utils import chunked


def _clear_duplicate_flags(img: Image) -> bool:
    """Drop both duplicate keys from one row's `quality_flags`; True if changed.

    Copy-then-reassign, exactly as `resolve_duplicates` does it: SQLAlchemy
    compares JSON columns by equality, so mutating `img.quality_flags` in place
    and reassigning the *same* dict looks unchanged and the UPDATE is silently
    skipped (the CLAUDE.md invariant, pinned by
    `test_quality_flags_persistence_http.py`).
    """
    flags = dict(img.quality_flags or {})
    if "is_duplicate" not in flags and "duplicate_of" not in flags:
        return False
    flags.pop("is_duplicate", None)
    flags.pop("duplicate_of", None)
    img.quality_flags = flags
    return True


async def prune_orphaned_duplicate_flags(
    db: AsyncSession, dataset_ids: Iterable[str]
) -> int:
    """Clear `is_duplicate` from survivors left alone in a group whose root is gone.

    A flagged survivor is orphaned **iff** its root row no longer exists **and**
    it is the only surviving flagged member of its group. Two survivors with a
    dead root are still duplicates of each other and keep their flags —
    `get_duplicates` renders exactly that case deliberately, as a group with no
    `kept` row.

    Takes the **datasets** touched, not the ids just deleted, and re-checks the
    invariant across them. That is deliberate: deriving the affected group keys
    from the deleted ids only works when the root goes in the same request. Prune
    a group one image at a time from the lightbox — the overwhelmingly likely way
    a user does it — and the delete that strands the last survivor names a
    *member*, whose own `duplicate_of` is unreadable by then because its row is
    already gone. Re-checking is also idempotent and placement-insensitive, and
    repairs orphans an older build left behind on the first delete in that
    dataset. The cost is one scan of the dataset's rows over an unindexed JSON
    extract, alongside the `refresh_stats` aggregation every one of these
    endpoints already runs.

    A flagged row with **no** `duplicate_of` at all is left alone: it names no
    root, so no delete can have orphaned it. A Technical re-scan is what clears
    that shape (`apply_duplicate_groups`).

    No `commit()`. The caller owns the transaction, so this runs alongside the
    row DELETEs and before the commit that makes them durable: one commit, atomic
    with the delete, and ahead of every filesystem mutation (PM-013).

    Returns the number of rows cleared.
    """
    root_of = Image.quality_flags["duplicate_of"].as_string()
    cleared = 0

    for dataset_id in sorted({d for d in dataset_ids if d}):
        rows = (await db.execute(
            select(Image.id, root_of).where(
                Image.dataset_id == dataset_id,
                Image.quality_flags["is_duplicate"].as_boolean() == True,  # noqa: E712
            )
        )).all()

        members: dict[str, list[str]] = {}
        for img_id, root_id in rows:
            if root_id:
                members.setdefault(root_id, []).append(img_id)
        lone = {root: ids[0] for root, ids in members.items() if len(ids) == 1}
        if not lone:
            continue

        alive: set[str] = set()
        for chunk in chunked(sorted(lone)):
            alive.update(
                r[0] for r in (await db.execute(
                    select(Image.id).where(Image.id.in_(chunk))
                )).all()
            )

        orphaned = [img_id for root, img_id in lone.items() if root not in alive]
        for chunk in chunked(orphaned):
            res = await db.execute(select(Image).where(Image.id.in_(chunk)))
            for img in res.scalars().all():
                if _clear_duplicate_flags(img):
                    cleared += 1
    return cleared


async def apply_duplicate_groups(
    db: AsyncSession, dataset_id: str, dup_of: dict[str, str]
) -> None:
    """Make `dup_of` the whole truth about `is_duplicate` within one dataset.

    Every row named in `dup_of` is flagged and pointed at its root; **every other
    currently-flagged row in the dataset is cleared**. That is what makes a
    Technical re-scan the repair path for any drift — orphans left by an older
    build, and flags written by an earlier scan at a looser `duplicate_threshold`
    that a stricter one no longer finds. An empty `dup_of` is not a no-op: it is
    precisely the case that must clear the dataset.

    Rows with no `phash` never enter the scan, and need not — the clear side is
    keyed on the flag already stored, not on the scan's input.

    No `commit()`; the caller owns the transaction.
    """
    flagged = {
        r[0] for r in (await db.execute(
            select(Image.id).where(
                Image.dataset_id == dataset_id,
                Image.quality_flags["is_duplicate"].as_boolean() == True,  # noqa: E712
            )
        )).all()
    }

    touched = sorted(set(dup_of) | flagged)
    for chunk in chunked(touched):
        res = await db.execute(select(Image).where(Image.id.in_(chunk)))
        for img in res.scalars().all():
            root = dup_of.get(img.id)
            if root is None:
                _clear_duplicate_flags(img)
                continue
            flags = dict(img.quality_flags or {})
            if flags.get("is_duplicate") is True and flags.get("duplicate_of") == root:
                continue
            flags["is_duplicate"] = True
            flags["duplicate_of"] = root
            img.quality_flags = flags
