"""`apply_duplicate_groups` — the Technical scan's reconciliation, both directions.

The scan used to be purely additive: it wrote `is_duplicate`/`duplicate_of` onto
the rows it grouped and returned early when it found no groups at all. So the
obvious recovery from a stale flag — re-run the Technical scorer — was a no-op,
and flags written at a looser `duplicate_threshold` survived a stricter re-scan
untouched. Making the scan authoritative is what turns it into the repair path
for any drift, including orphans already sitting in a user's database.

Service-level on purpose: `_flag_duplicates` needs cv2 and the job queue to reach
this logic, and none of that is required to pin what the reconciliation does.
"""
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from backend.models.image import Image
from backend.services.duplicate_service import (
    apply_duplicate_groups,
    prune_orphaned_duplicate_flags,
)
from backend.tests.conftest import api_env, png_bytes, run, upload_image


async def _seed(env, dataset_id: str, names: list[str]) -> list[str]:
    return [
        (await upload_image(env, dataset_id, n, png_bytes((10 * i + 5, 20, 30))))["id"]
        for i, n in enumerate(names)
    ]


async def _set_flags(env, flags_by_id: dict[str, dict]) -> None:
    async with env.Session() as db:
        for img_id, flags in flags_by_id.items():
            (await db.get(Image, img_id)).quality_flags = flags
        await db.commit()


async def _flags_by_id(env, dataset_id: str) -> dict[str, dict]:
    async with env.Session() as db:
        rows = (await db.execute(
            select(Image.id, Image.quality_flags).where(Image.dataset_id == dataset_id)
        )).all()
    return {r[0]: dict(r[1] or {}) for r in rows}


def test_an_empty_scan_clears_every_stale_flag(tmp_path):
    """Zero groups is not "nothing to do". It is exactly the case that has to
    clear the dataset — the deleted `if not dup_of: return` early return is what
    made the re-scan repair path impossible."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            root, a, b = await _seed(env, ds["id"], ["root.png", "a.png", "b.png"])
            await _set_flags(env, {
                a: {"is_duplicate": True, "duplicate_of": root},
                b: {"is_duplicate": True, "duplicate_of": root, "is_blurry": True},
            })

            async with env.Session() as db:
                await apply_duplicate_groups(db, ds["id"], {})
                await db.commit()

            flags = await _flags_by_id(env, ds["id"])
            assert flags[a] == {}
            # Only the two duplicate keys go; unrelated flags on the same row are
            # not this function's business.
            assert flags[b] == {"is_blurry": True}

    run(scenario())


def test_a_regrouped_scan_replaces_the_old_flags_rather_than_merging(tmp_path):
    """A re-scan that finds a different grouping is the whole truth: the row that
    moved points at its new root, and the row that dropped out is cleared."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            old_root, a, b, new_root = await _seed(
                env, ds["id"], ["old.png", "a.png", "b.png", "new.png"]
            )
            await _set_flags(env, {
                a: {"is_duplicate": True, "duplicate_of": old_root},
                b: {"is_duplicate": True, "duplicate_of": old_root},
            })

            async with env.Session() as db:
                await apply_duplicate_groups(db, ds["id"], {a: new_root})
                await db.commit()

            flags = await _flags_by_id(env, ds["id"])
            assert flags[a] == {"is_duplicate": True, "duplicate_of": new_root}
            assert flags[b] == {}
            assert flags[old_root] == {}
            # The new root is a group root, so it stays unflagged — the marker is
            # for removable copies only, which every bulk filter and export
            # exclusion depends on.
            assert flags[new_root] == {}

    run(scenario())


def test_a_flagged_row_the_scan_never_saw_is_still_cleared(tmp_path):
    """The clear side is keyed on the stored flag, not on the scan's input, so a
    row with no `phash` — never a candidate, never in `dup_of` — is still
    repaired."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            root, stale = await _seed(env, ds["id"], ["root.png", "stale.png"])
            await _set_flags(env, {stale: {"is_duplicate": True, "duplicate_of": root}})
            async with env.Session() as db:
                (await db.get(Image, stale)).phash = None
                await db.commit()

            async with env.Session() as db:
                await apply_duplicate_groups(db, ds["id"], {})
                await db.commit()

            assert (await _flags_by_id(env, ds["id"]))[stale] == {}

    run(scenario())


def test_another_dataset_is_left_alone(tmp_path):
    """The reconciliation is scoped to the dataset that was scanned."""
    async def scenario():
        async with api_env(tmp_path) as env:
            scanned = await env.create_dataset("scanned")
            other = await env.create_dataset("other")
            root, a = await _seed(env, scanned["id"], ["root.png", "a.png"])
            o_root, o_a = await _seed(env, other["id"], ["root.png", "a.png"])
            await _set_flags(env, {
                a: {"is_duplicate": True, "duplicate_of": root},
                o_a: {"is_duplicate": True, "duplicate_of": o_root},
            })

            async with env.Session() as db:
                await apply_duplicate_groups(db, scanned["id"], {})
                await db.commit()

            assert (await _flags_by_id(env, scanned["id"]))[a] == {}
            assert (await _flags_by_id(env, other["id"]))[o_a] == {
                "is_duplicate": True, "duplicate_of": o_root,
            }

    run(scenario())


def test_the_prune_ignores_a_group_whose_root_survived(tmp_path):
    """The prune re-checks the invariant across the **datasets** touched, not
    against the ids just deleted — so an unrelated delete re-examines every
    flagged row in that dataset. A group whose root is still there must come
    through that pass untouched, however small it is.

    The argument is a *dataset* id, and passing an image id instead makes the
    call vacuous: it matches no dataset, so every assertion below holds for the
    wrong reason."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            root, a, unrelated = await _seed(env, ds["id"], ["root.png", "a.png", "x.png"])
            await _set_flags(env, {a: {"is_duplicate": True, "duplicate_of": root}})

            async with env.Session() as db:
                # A real delete-driven prune: something else in the dataset goes,
                # which is what a caller passes this dataset id for.
                await db.execute(sa_delete(Image).where(Image.id == unrelated))
                cleared = await prune_orphaned_duplicate_flags(db, [ds["id"]])
                await db.commit()

            assert cleared == 0
            assert (await _flags_by_id(env, ds["id"]))[a] == {
                "is_duplicate": True, "duplicate_of": root,
            }

    run(scenario())
