"""Frame lineage — and every quality score — survives every path that copies,
moves, snapshots or restores an Image.

Extraction writes `source_video_id`, `source_timestamp_ms` and
`source_shot_index` once and nothing ever changes them again — which is exactly
why they are easy to lose. Eight code paths rebuild an `Image` field by field
(two in `duplicate_dataset`, `batch_copy_dataset`, `batch_move_dataset`,
`create_snapshot`, the restore write-back, and the `VersionImageState` mirror
each of the last two depends on), every one of them fails **silently** when a
column is missed, and before this file none of them had a test.

`test_every_image_column_is_mirrored_on_version_image_state` is the structural
one: it fails for the *next* column somebody adds to `Image` without mirroring
it, which is the only kind of test that helps here. The rest are behavioural
round-trips through the paths that a structural test cannot reach.

The rule the behavioural tests pin: **a cross-dataset copy or move NULLs
`source_video_id` and keeps the timestamp and shot index.** The id would point
at a video the destination dataset does not contain; where in a video a frame
came from is a fact about the frame and travels with it.

The same eight paths carry the ten `*_score` columns, and lost three of them the
same silent way — hence `SCORE_COLUMNS` and the guards over it here. A score is
authored data as far as a snapshot is concerned: nothing recomputes one, so the
rule is that every rebuild path carries every score, from the column of the same
name.
"""

import ast
import inspect
import textwrap
from pathlib import Path

from sqlalchemy import select

from backend.models import Image, Video
from backend.models.versioning import VersionImageState
from backend.services import dataset_service, version_service
from backend.tests.conftest import (
    API,
    api_env,
    needs_cv2,
    run,
    upload_image,
    upload_video,
    wait_for_job,
)

# `needs_cv2` is per-test here rather than a module-level `pytest.importorskip`:
# the four structural guards below need no video at all, and they are the ones
# CLAUDE.md relies on to fail CI when a new `Image` column goes unmirrored.
# Skipping *those* on a machine without opencv would drop the coverage that
# matters most in this file.

LINEAGE = ("source_video_id", "source_timestamp_ms", "source_shot_index")

# Every `*_score` column on `Image`, derived rather than listed so the *eleventh*
# score is covered the moment it is added.
SCORE_COLUMNS = {c.key for c in Image.__table__.columns if c.key.endswith("_score")}

# Columns that *qualify* a score without being one — they say something about how
# a score should be read, so a rebuild path that carries the number and drops
# these presents it under the wrong terms. `aesthetic_model` is the first: a
# LAION score inherited under a "v2_5" marker is worse than no marker at all,
# because two consumers act destructively on the value and one of them deletes
# images. They cannot be found by the `*_score` suffix, so they announce
# themselves in the column's own `info` — a new one is enrolled by declaring
# `info={"qualifies": "<score column>"}` on the model and nothing else.
SCORE_QUALIFIERS = {c.key for c in Image.__table__.columns if "qualifies" in c.info}

# What every field-by-field rebuild path must carry.
CARRIED_COLUMNS = SCORE_COLUMNS | SCORE_QUALIFIERS

# Columns that live on `Image` and deliberately have no `VersionImageState`
# counterpart. Every entry needs a reason, because the default answer for a new
# column is "mirror it" — a snapshot restore writes back exactly what the mirror
# holds, so an unmirrored column is silently blanked by any restore.
# (`Image.id` is absent because it *is* mirrored, as `VersionImageState.image_id`
# alongside that table's own surrogate `id`.)
NOT_MIRRORED = {
    "dataset_id",            # a restore can target a different dataset
    "thumbnail_path",        # re-derived from file_path on restore
    "is_auto_named",         # a naming provenance flag, not image content
    "created_at",            # the state row carries the version's timestamp
    "updated_at",
    "phash",                 # recomputed from the restored file
    "clip_embedding",        # blobs: megabytes per row, recomputed on demand
    "dino_embedding",
    "dino_layer_embeddings",
    "caption_token_count",   # derived from caption_text by the ORM listener
    "caption_style",         # captioning bookkeeping, not caption content
    "captioned_by",
    "captioned_at",
}


# Columns the `include_videos` copy in `duplicate_dataset` deliberately does not
# carry from the source row. Every one is written, just computed rather than
# copied: the clone needs its own identity and its own paths, and the timestamps
# are the copy's, not the original's.
VIDEO_NOT_CARRIED = {
    "id",            # a fresh uuid, and the key the lineage remap is built on
    "dataset_id",    # the destination's
    "file_path",     # under the clone's videos/
    "poster_path",   # under the clone's videos/thumbnails/, or NULL if absent
    "created_at",    # when the copy was made
    "updated_at",
}


def _columns(model) -> set[str]:
    return {c.key for c in model.__table__.columns}


def _ctor_kwargs(func, ctor_name: str) -> list[dict[str, tuple[str, str] | None]]:
    """One dict per `ctor_name(...)` call in `func`'s source: kwarg name → the
    `(variable, attribute)` it copies, or `None` where the value is computed.

    Read out of the AST rather than by running the path, so the guards below fail
    for the *next* column somebody adds even on a machine with no cv2 — the same
    reason the `Image`↔`VersionImageState` guard is structural.

    `**copy_provenance(row)` (an `ast.keyword` with `arg is None`) is skipped: it
    contributes the five provenance keys, never a score.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    out: list[dict[str, tuple[str, str] | None]] = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == ctor_name):
            continue
        kwargs: dict[str, tuple[str, str] | None] = {}
        for kw in n.keywords:
            if kw.arg is None:
                continue
            v = kw.value
            if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name):
                kwargs[kw.arg] = (v.value.id, v.attr)
            else:
                kwargs[kw.arg] = None
        out.append(kwargs)
    return out


def _assigned_attrs(func, target_var: str) -> dict[str, tuple[str, str] | None]:
    """The assignment form of `_ctor_kwargs`: every `<target_var>.x = y.z` in
    `func`, as `x → (y, z)`.

    `restore_snapshot` writes its fields this way — its own `Image(...)` covers
    only the columns needed to make the re-created row insertable, and the
    assignment block that follows runs for re-created and pre-existing rows
    alike, so *that* is the site a dropped column is lost at.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    out: dict[str, tuple[str, str] | None] = {}
    for n in ast.walk(tree):
        if not isinstance(n, ast.Assign) or len(n.targets) != 1:
            continue
        t = n.targets[0]
        if not (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == target_var):
            continue
        v = n.value
        if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name):
            out[t.attr] = (v.value.id, v.attr)
        else:
            out[t.attr] = None
    return out


def _duplicate_video_kwargs() -> dict[str, str | None]:
    """Kwarg name → the source attribute it copies, for the one `Video(...)`
    `duplicate_dataset` builds. `None` where the value is computed instead."""
    calls = _ctor_kwargs(dataset_service.duplicate_dataset, "Video")
    assert len(calls) == 1, f"expected one Video(...) in duplicate_dataset, found {len(calls)}"
    return {
        k: (src[1] if src and src[0] == "vid" else None)
        for k, src in calls[0].items()
    }


def test_the_video_copy_carries_every_video_column():
    """The `Video` twin of the mirror guard. Without it, the next column added
    to `Video` is silently dropped by every dataset duplicate that carries
    videos — a decode fixup or a provenance field lost with no error anywhere."""
    carried = {k for k, src in _duplicate_video_kwargs().items() if src}
    missing = _columns(Video) - carried - VIDEO_NOT_CARRIED
    assert not missing, (
        f"{sorted(missing)} exist on Video but duplicate_dataset's copy does not "
        "carry them. Copy them from the source row, or add them to "
        "VIDEO_NOT_CARRIED with a reason."
    )


def test_the_video_copy_carries_each_column_from_its_own_name():
    """`width=vid.height` would satisfy the guard above and still be wrong."""
    mismatched = {k: src for k, src in _duplicate_video_kwargs().items() if src and src != k}
    assert not mismatched, f"copied from the wrong source attribute: {mismatched}"


def test_video_not_carried_has_no_stale_entries():
    """Same contract as NOT_MIRRORED's: exactly the uncarried set, not a superset."""
    kwargs = _duplicate_video_kwargs()
    carried = {k for k, src in kwargs.items() if src}
    stale = (VIDEO_NOT_CARRIED & carried) | (VIDEO_NOT_CARRIED - _columns(Video))
    assert not stale, (
        f"VIDEO_NOT_CARRIED entries that are no longer needed: {sorted(stale)} "
        "(the column was dropped, or it is carried after all)"
    )


def test_every_image_column_is_mirrored_on_version_image_state():
    """The structural guard. This fails for the *next* column added to `Image`
    without a mirror, which is the whole reason it exists — every failure mode
    downstream of a missing mirror is silent."""
    missing = _columns(Image) - _columns(VersionImageState) - NOT_MIRRORED
    assert not missing, (
        f"{sorted(missing)} exist on Image but not on VersionImageState. "
        "Mirror them (and copy them in create_snapshot and the restore "
        "write-back), or add them to NOT_MIRRORED with a reason."
    )


def test_every_score_column_is_mirrored_on_version_image_state():
    """Stronger than the allowlist alone, which only says *these three* are
    unmirrored — this says no score may be. Nothing recomputes a technical score:
    quality scoring is a manual job, and `score_coverage["technical"]` counts
    `blur_score` alone, so a dataset missing one does not even report as needing
    a re-score. A snapshot is the only record of an old value.
    """
    missing = SCORE_COLUMNS - _columns(VersionImageState)
    assert not missing, (
        f"{sorted(missing)} are Image score columns with no VersionImageState "
        "mirror, so a restore blanks them. Mirror them; do not add a score to "
        "NOT_MIRRORED."
    )


def _pick_call(calls: list[dict], var: str) -> dict:
    """The one call among `calls` that reads its values off `var`."""
    matches = [c for c in calls if any(src and src[0] == var for src in c.values())]
    assert len(matches) == 1, f"expected one call sourced from `{var}`, found {len(matches)}"
    return matches[0]


def _score_carriers() -> dict[str, dict[str, tuple[str, str] | None]]:
    """Site label → its kwarg/assignment map, for every path that rebuilds an
    `Image`-shaped row field by field.

    An **explicit** list, never every `Image(...)` in the codebase: five of the
    nine constructor sites (`routers/images.py`'s crop and upscale replacements,
    `detection.py`, `lut.py`, `upscaling.py`) build *derivatives*, whose pixels
    are not the scored pixels — they must not carry a score forward.
    """
    from backend.routers import images as images_router

    dup = _ctor_kwargs(dataset_service.duplicate_dataset, "Image")
    assert len(dup) == 2, f"expected two Image(...) in duplicate_dataset, found {len(dup)}"
    copy = _ctor_kwargs(images_router.batch_copy_dataset, "Image")
    assert len(copy) == 1, f"expected one Image(...) in batch_copy_dataset, found {len(copy)}"
    snap = _ctor_kwargs(version_service.create_snapshot, "VersionImageState")
    assert len(snap) == 1, f"expected one VersionImageState(...) in create_snapshot, found {len(snap)}"

    return {
        "duplicate_dataset (on-disk branch)": _pick_call(dup, "row"),
        "duplicate_dataset (snapshot branch)": _pick_call(dup, "state"),
        "batch_copy_dataset": copy[0],
        "create_snapshot": snap[0],
        # The restore write-back, in its assignment form.
        "restore_snapshot": _assigned_attrs(version_service.restore_snapshot, "img"),
    }


def test_every_rebuild_path_carries_every_score():
    """The structural half of the round-trips below, and the only guard on
    `duplicate_dataset`'s snapshot branch that does not need a running job.

    Each site must carry every score **from the column of the same name** —
    `nsfw_score=row.saturation_score` satisfies "carried" and is still wrong, and
    the values a behavioural test seeds are distinct for the same reason.
    """
    missing: dict[str, list[str]] = {}
    mismatched: dict[str, dict[str, str]] = {}
    for label, kwargs in _score_carriers().items():
        carried = {k: src for k, src in kwargs.items() if src}
        gone = sorted(CARRIED_COLUMNS - set(carried))
        if gone:
            missing[label] = gone
        wrong = {k: src[1] for k, src in carried.items() if k in CARRIED_COLUMNS and src[1] != k}
        if wrong:
            mismatched[label] = wrong
    assert not missing, f"score columns dropped by a rebuild path: {missing}"
    assert not mismatched, f"score columns copied from the wrong attribute: {mismatched}"


def test_every_score_qualifier_names_a_real_score_column():
    """`info={"qualifies": "aesthetic_score"}` is the whole enrolment mechanism,
    so a typo in it silently un-enrols the column from the guard above rather
    than failing anywhere."""
    bad = {
        c.key: c.info["qualifies"]
        for c in Image.__table__.columns
        if "qualifies" in c.info and c.info["qualifies"] not in SCORE_COLUMNS
    }
    assert not bad, (
        f"these columns qualify something that is not a score column: {bad} "
        f"(known scores: {sorted(SCORE_COLUMNS)})"
    )
    # A qualifier that *is* a score would be enrolled twice and, worse, seeded
    # with a float by the behavioural helpers below.
    assert SCORE_QUALIFIERS.isdisjoint(SCORE_COLUMNS)
    # …and the filter above passes *vacuously* on an empty set, which is exactly
    # what an `info=` dropped in passing (adding `index=True`, say) produces:
    # `CARRIED_COLUMNS` collapses back to `SCORE_COLUMNS` and
    # `test_every_rebuild_path_carries_every_score` stops guarding the marker,
    # with the whole suite still green. So the known members are named.
    assert "aesthetic_model" in SCORE_QUALIFIERS, (
        "aesthetic_model lost its info={'qualifies': ...} on backend/models/image.py, "
        "which silently un-enrols it from test_every_rebuild_path_carries_every_score"
    )


def test_not_mirrored_has_no_stale_entries():
    """The allowlist must be exactly the unmirrored set, not merely a superset.

    An entry naming a column that was since dropped, or one that has since
    *gained* a mirror, is a reason nobody will read again — and either would
    silently absorb a genuinely missing mirror if that name came back.
    """
    stale = NOT_MIRRORED - (_columns(Image) - _columns(VersionImageState))
    assert not stale, (
        f"NOT_MIRRORED entries that are no longer needed: {sorted(stale)} "
        "(the column was dropped, or it is mirrored after all)"
    )


def test_lineage_is_mirrored_and_snapshotted_but_not_diffed():
    """Lineage is immutable per image, so it can never differ between two
    snapshots — it is stored and restored, just not *compared*."""
    assert set(LINEAGE) <= _columns(VersionImageState)
    selected = {c.key for c in version_service._DIFF_COLS}
    assert selected.isdisjoint(LINEAGE)
    # The existing invariant, extended rather than duplicated: everything the
    # comparison loop reads has to be selected, or the diff reports "unchanged"
    # for a value that changed.
    assert set(version_service._DIFF_COMPARE_FIELDS) <= selected


def test_scores_stale_is_mirrored_and_diffed():
    """The bit qualifying the ten scores is *mutable*, so unlike lineage it is
    compared as well as carried.

    The mirror itself is already forced by
    `test_every_image_column_is_mirrored_on_version_image_state` — a snapshot
    restoring stale scores without the bit would silently declare them
    trustworthy. What that guard cannot say is which side of `_DIFF_COLS`'
    immutable-lineage carve-out this falls on. An in-place pixel rewrite between
    two snapshots flips this column and nothing else, which is exactly a
    difference the diff exists to show.
    """
    assert "scores_stale" in _columns(VersionImageState)
    selected = {c.key for c in version_service._DIFF_COLS}
    assert "scores_stale" in selected
    assert "scores_stale" in version_service._DIFF_COMPARE_FIELDS


def test_scores_stale_is_not_mistaken_for_a_score_column():
    """`SCORE_COLUMNS` is derived by suffix and drives float-seeding guards, so a
    boolean must never land in it. The naming rule this pins is in
    `backend/models/image.py`'s comment on the column."""
    assert "scores_stale" not in SCORE_COLUMNS
    assert all(not c.endswith("_score") for c in ("scores_stale",))


def test_every_rebuild_path_carries_scores_stale():
    """The `scores_stale` half of `test_every_rebuild_path_carries_every_score`.

    Same five field-by-field rebuild sites, same silent failure: a snapshot that
    carries a stale score and drops the bit is worse than one that carries
    neither, because it presents the number as current.
    """
    missing = [
        label for label, kwargs in _score_carriers().items()
        if not kwargs.get("scores_stale")
    ]
    assert not missing, (
        f"these rebuild paths drop `scores_stale`: {missing} — carry it from the "
        "source row alongside the scores it qualifies"
    )
    mismatched = {
        label: kwargs["scores_stale"][1]
        for label, kwargs in _score_carriers().items()
        if kwargs.get("scores_stale") and kwargs["scores_stale"][1] != "scores_stale"
    }
    assert not mismatched, f"`scores_stale` copied from the wrong attribute: {mismatched}"


def test_version_image_state_does_not_carry_a_video_foreign_key():
    """Matching `image_id`, which is FK-free because a restore can target
    another dataset. A snapshot must also survive its source video's deletion —
    which is precisely when the lineage record is worth the most."""
    fks = {fk.parent.key for fk in VersionImageState.__table__.foreign_keys}
    assert "source_video_id" not in fks


@needs_cv2
def test_images_source_video_id_is_set_null_on_delete():
    """Belt-and-braces behind the explicit UPDATE in `DELETE /videos/{id}`.
    Asserted against the DDL because the test harness builds its schema with
    `create_all` and never gets the `PRAGMA foreign_keys=ON` that
    backend/database.py installs on the app engine."""
    fk = next(
        fk for fk in Image.__table__.foreign_keys if fk.parent.key == "source_video_id"
    )
    assert fk.column.table.name == "videos"
    assert fk.ondelete == "SET NULL"


# ---------------------------------------------------------------------------
# Behavioural round-trips — scores across a dataset boundary
#
# No video and no `@needs_cv2`: a score is an ordinary column, and routing these
# through a decode would only make them skippable.
# ---------------------------------------------------------------------------


def _distinct_scores() -> dict[str, float]:
    """A different value in every score column.

    Equal values would let `nsfw_score=row.saturation_score` — a copy from the
    wrong source attribute, the exact typo T3 guards structurally — pass a
    behavioural round-trip.
    """
    return {name: 0.1 + i / 100 for i, name in enumerate(sorted(SCORE_COLUMNS))}


async def _seed_scores(env, image_id: str) -> dict[str, float]:
    scores = _distinct_scores()
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        for name, value in scores.items():
            setattr(row, name, value)
        await db.commit()
    return scores


def _read_scores(img: Image) -> dict[str, float | None]:
    return {name: getattr(img, name) for name in SCORE_COLUMNS}


def test_a_cross_dataset_copy_carries_every_score(tmp_path):
    """`batch_copy_dataset` rebuilds the `Image` field by field, so a score
    missing from its column tuple is dropped with no error anywhere."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            img = await upload_image(env, src["id"], "a.png")
            scores = await _seed_scores(env, img["id"])

            r = await env.client.post(
                f"{API}/images/batch/copy-dataset",
                json={"image_ids": [img["id"]], "target_dataset_id": dest["id"], "subfolder": ""},
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id == dest["id"])
                )).scalar_one()
            assert _read_scores(copy) == scores

    run(scenario())


def test_a_duplicate_from_a_snapshot_carries_every_score(tmp_path):
    """`duplicate_dataset`'s snapshot branch, which nothing covered before.

    Its bite is worth stating, because it cannot be shown the usual way: on the
    parent commit the three columns do not exist on `VersionImageState` at all,
    so this fails for a reason that says nothing about the branch it is aimed at.
    It was proven red by applying the whole change *except* the three kwargs in
    that branch's `Image(...)` — which then failed on exactly those three, with
    every other path green.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            img = await upload_image(env, src["id"], "a.png")
            scores = await _seed_scores(env, img["id"])

            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{src['id']}/versions", json={"name": "v1"})
            assert r.status_code in (200, 201, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{src['id']}/versions")).json()[0]["id"]

            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate",
                json={"new_name": "from-snapshot", "source_version_id": version_id},
            )
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id != src["id"])
                )).scalars().one()
            assert _read_scores(copy) == scores

    run(scenario())


def test_a_duplicate_carries_every_score(tmp_path):
    """`duplicate_dataset`'s on-disk branch — a second field-by-field rebuild
    with its own column tuple, which the copy test above does not reach."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            img = await upload_image(env, src["id"], "a.png")
            scores = await _seed_scores(env, img["id"])

            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate", json={"new_name": "copy"}
            )
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id != src["id"])
                )).scalars().one()
            assert _read_scores(copy) == scores

    run(scenario())


async def _seed_stale(env, image_id: str) -> None:
    """Set `scores_stale` directly. The HTTP paths that set it for real are
    exercised in `test_scores_stale.py`; here the bit is only cargo."""
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        row.scores_stale = True
        await db.commit()


def test_a_cross_dataset_copy_carries_scores_stale(tmp_path):
    """A copy that carried the numbers and dropped the bit would present scores
    measured on deleted pixels as current — worse than dropping both."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            img = await upload_image(env, src["id"], "a.png")
            await _seed_scores(env, img["id"])
            await _seed_stale(env, img["id"])

            r = await env.client.post(
                f"{API}/images/batch/copy-dataset",
                json={"image_ids": [img["id"]], "target_dataset_id": dest["id"], "subfolder": ""},
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id == dest["id"])
                )).scalar_one()
            assert copy.scores_stale is True

    run(scenario())


def test_both_duplicate_branches_carry_scores_stale(tmp_path):
    """`duplicate_dataset`'s on-disk branch and its snapshot branch are separate
    field-by-field rebuilds with separate column lists; both must carry it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            img = await upload_image(env, src["id"], "a.png")
            await _seed_scores(env, img["id"])
            await _seed_stale(env, img["id"])

            # On-disk branch.
            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate", json={"new_name": "copy"}
            )
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id != src["id"])
                )).scalars().one()
                assert copy.scores_stale is True
                copy_dataset_id = copy.dataset_id

            # Snapshot branch.
            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{src['id']}/versions", json={"name": "v1"})
            assert r.status_code in (200, 201, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{src['id']}/versions")).json()[0]["id"]

            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate",
                json={"new_name": "from-snapshot", "version_id": version_id},
            )
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                from_snap = (await db.execute(
                    select(Image).where(
                        Image.dataset_id.notin_([src["id"], copy_dataset_id])
                    )
                )).scalars().one()
            assert from_snap.scores_stale is True

    run(scenario())


def test_snapshot_and_restore_preserve_scores_stale(tmp_path):
    """The mirror's whole point: a restore writes back exactly what the state row
    holds, so an unmirrored bit is cleared by every restore — leaving a snapshot
    whose scores are stale and whose flag says they are fine.

    Restores *from* True and *to* True both matter, so this snapshots the stale
    state, clears the bit as a re-score would, and restores.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            await _seed_scores(env, img["id"])
            await _seed_stale(env, img["id"])

            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{ds['id']}/versions", json={"name": "v1"})
            assert r.status_code in (200, 201, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{ds['id']}/versions")).json()[0]["id"]

            async with env.Session() as db:
                state = (await db.execute(
                    select(VersionImageState).where(VersionImageState.image_id == img["id"])
                )).scalar_one()
                assert state.scores_stale is True

            # What a successful re-score does.
            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                row.scores_stale = False
                await db.commit()

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/versions/{version_id}/restore", json={}
            )
            assert r.status_code in (200, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                await db.refresh(row)
            assert row.scores_stale is True

    run(scenario())


# ---------------------------------------------------------------------------
# Behavioural round-trips
# ---------------------------------------------------------------------------


async def _make_frame(env, dataset_id: str, *, video_id: str, name: str = "frame.png") -> dict:
    """An image standing in for an extracted frame, with lineage written on it.

    Set directly rather than by running an extraction: these tests are about the
    eight paths that *carry* lineage, and routing every one of them through a
    real decode would make them slow and would couple them to the detector.
    """
    img = await upload_image(env, dataset_id, name)
    async with env.Session() as db:
        row = await db.get(Image, img["id"])
        row.source_video_id = video_id
        row.source_timestamp_ms = 4321
        row.source_shot_index = 7
        await db.commit()
    return img


@needs_cv2
def test_snapshot_and_restore_preserve_lineage(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            frame = await _make_frame(env, ds["id"], video_id=video["id"])

            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{ds['id']}/versions", json={"name": "v1"})
            assert r.status_code in (200, 201, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{ds['id']}/versions")).json()[0]["id"]

            async with env.Session() as db:
                state = (await db.execute(
                    select(VersionImageState).where(VersionImageState.image_id == frame["id"])
                )).scalar_one()
                assert state.source_video_id == video["id"]
                assert state.source_timestamp_ms == 4321
                assert state.source_shot_index == 7

                # Blank it on the live row, so the restore has something to put back.
                row = await db.get(Image, frame["id"])
                row.source_video_id = None
                row.source_timestamp_ms = None
                row.source_shot_index = None
                await db.commit()

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/versions/{version_id}/restore",
                json={"handle_extra_images": "remove"},
            )
            assert r.status_code in (200, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                row = await db.get(Image, frame["id"])
                assert row.source_video_id == video["id"]
                assert row.source_timestamp_ms == 4321
                assert row.source_shot_index == 7

    run(scenario())


@needs_cv2
def test_cross_dataset_copy_nulls_the_video_id_and_keeps_the_rest(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            video = await upload_video(env, src["id"], "clip.mp4")
            frame = await _make_frame(env, src["id"], video_id=video["id"])

            r = await env.client.post(
                f"{API}/images/batch/copy-dataset",
                json={"image_ids": [frame["id"]], "target_dataset_id": dest["id"], "subfolder": ""},
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id == dest["id"])
                )).scalar_one()
                original = await db.get(Image, frame["id"])

            assert copy.source_video_id is None
            assert copy.source_timestamp_ms == 4321
            assert copy.source_shot_index == 7
            # The original is untouched.
            assert original.source_video_id == video["id"]

    run(scenario())


@needs_cv2
def test_cross_dataset_move_nulls_the_video_id_and_keeps_the_rest(tmp_path):
    """A move is an UPDATE in place, so lineage survives unless it is explicitly
    cleared — the row would land in the target still pointing at a video the
    target does not contain."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            dest = await env.create_dataset("dest")
            video = await upload_video(env, src["id"], "clip.mp4")
            frame = await _make_frame(env, src["id"], video_id=video["id"])

            r = await env.client.post(
                f"{API}/images/batch/move-dataset",
                json={"image_ids": [frame["id"]], "target_dataset_id": dest["id"], "subfolder": ""},
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                moved = await db.get(Image, frame["id"])
            assert moved.dataset_id == dest["id"]
            assert moved.source_video_id is None
            assert moved.source_timestamp_ms == 4321
            assert moved.source_shot_index == 7

    run(scenario())


@needs_cv2
def test_duplicate_without_videos_nulls_the_video_id_and_keeps_the_rest(tmp_path):
    """The default-off case: `include_videos` is absent, so nothing carries the
    footage and there is no video in the clone for an id to point at."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            video = await upload_video(env, src["id"], "clip.mp4")
            await _make_frame(env, src["id"], video_id=video["id"])

            r = await env.client.post(f"{API}/datasets/{src['id']}/duplicate", json={"new_name": "copy"})
            assert r.status_code in (200, 202), r.text
            await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                new_ds_id = (await db.execute(
                    select(Image.dataset_id).where(Image.dataset_id != src["id"])
                )).scalars().first()
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id == new_ds_id)
                )).scalar_one()

            assert copy.source_video_id is None
            assert copy.source_timestamp_ms == 4321
            assert copy.source_shot_index == 7
            # Without the toggle the clone holds no videos, so there is nothing
            # for an id to point at in the first place.
            async with env.Session() as db:
                videos = (await db.execute(
                    select(Video).where(Video.dataset_id == new_ds_id)
                )).scalars().all()
            assert videos == []

    run(scenario())


@needs_cv2
def test_duplicate_with_videos_remaps_the_video_id_onto_the_clones_own_video(tmp_path):
    """The toggle-on twin, and the whole point of the remap: the copied frame
    must name the *clone's* video. `None` would be a dropped lineage and the
    source's id would be a pointer across a dataset boundary — a raw copy passes
    neither."""
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            video = await upload_video(env, src["id"], "clip.mp4")
            await _make_frame(env, src["id"], video_id=video["id"])

            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate",
                json={"new_name": "copy", "include_videos": True},
            )
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                new_ds_id = (await db.execute(
                    select(Image.dataset_id).where(Image.dataset_id != src["id"])
                )).scalars().first()
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id == new_ds_id)
                )).scalar_one()
                new_video = (await db.execute(
                    select(Video).where(Video.dataset_id == new_ds_id)
                )).scalar_one()

            assert copy.source_video_id == new_video.id
            assert copy.source_video_id is not None
            assert copy.source_video_id != video["id"]
            assert copy.source_timestamp_ms == 4321
            assert copy.source_shot_index == 7

    run(scenario())


@needs_cv2
def test_a_video_that_fails_to_copy_leaves_its_frames_unlinked_not_dangling(tmp_path):
    """A copy failure is skip-and-report, like the image loop — so the job still
    completes, the other video lands, and the failed video's frames fall back to
    NULL rather than keeping an id no row in the clone answers to."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("src")
            good = await upload_video(env, ds["id"], "good.mp4")
            gone = await upload_video(env, ds["id"], "gone.mp4")
            await _make_frame(env, ds["id"], video_id=good["id"], name="from_good.png")
            await _make_frame(env, ds["id"], video_id=gone["id"], name="from_gone.png")

            # The row survives; its file does not. (VideoOut carries no path, so
            # the row is the only place to read it from.)
            async with env.Session() as db:
                Path((await db.get(Video, gone["id"])).file_path).unlink()

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/duplicate",
                json={"new_name": "copy", "include_videos": True},
            )
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job
            assert job["result_data"]["videos_added"] == 1
            assert job["result_data"]["videos_failed"] == 1

            async with env.Session() as db:
                new_ds_id = (await db.execute(
                    select(Image.dataset_id).where(Image.dataset_id != ds["id"])
                )).scalars().first()
                videos = (await db.execute(
                    select(Video).where(Video.dataset_id == new_ds_id)
                )).scalars().all()
                frames = {
                    i.filename: i for i in (await db.execute(
                        select(Image).where(Image.dataset_id == new_ds_id)
                    )).scalars().all()
                }

            assert [v.filename for v in videos] == ["good.mp4"]
            assert frames["from_good.png"].source_video_id == videos[0].id
            assert frames["from_gone.png"].source_video_id is None
            # The timestamp is a fact about the frame and survives either way.
            assert frames["from_gone.png"].source_timestamp_ms == 4321

    run(scenario())


@needs_cv2
def test_duplicate_dataset_from_a_snapshot_nulls_the_video_id_and_keeps_the_rest(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            src = await env.create_dataset("src")
            video = await upload_video(env, src["id"], "clip.mp4")
            await _make_frame(env, src["id"], video_id=video["id"])

            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{src['id']}/versions", json={"name": "v1"})
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{src['id']}/versions")).json()[0]["id"]

            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate",
                json={"new_name": "from-snapshot", "source_version_id": version_id},
            )
            assert r.status_code in (200, 202), r.text
            await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                copy = (await db.execute(
                    select(Image).where(Image.dataset_id != src["id"])
                )).scalars().one()

            assert copy.source_video_id is None
            assert copy.source_timestamp_ms == 4321
            assert copy.source_shot_index == 7

    run(scenario())


@needs_cv2
def test_deleting_a_video_leaves_its_frames_with_null_lineage_and_intact_files(tmp_path):
    """Frames are curated data. Deleting a source must not destroy them, and the
    timestamp and shot index survive — a frame keeps knowing where in a video it
    came from even once the video is gone."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            frame = await _make_frame(env, ds["id"], video_id=video["id"])

            r = await env.client.delete(f"{API}/videos/{video['id']}")
            assert r.status_code == 204, r.text

            async with env.Session() as db:
                row = await db.get(Image, frame["id"])
                await db.refresh(row)
            assert row is not None
            assert row.source_video_id is None
            assert row.source_timestamp_ms == 4321
            assert row.source_shot_index == 7
            assert Path(row.file_path).exists()

    run(scenario())


@needs_cv2
def test_a_derivative_of_a_frame_has_no_lineage(tmp_path):
    """`copy_provenance` returns the five provenance keys and nothing else, so
    crop/upscale/LUT/detection-crop derivatives inherit no lineage — the pixels
    are no longer the extracted frame.

    The hazard this does *not* cover is the **replace** mode of those same
    operations, which mutates the row in place and therefore keeps its lineage.
    Any re-extraction pass must skip or warn on a frame with a non-empty
    `processing_history`.
    """
    from backend.licenses import copy_provenance

    class _Frame:
        source_name = "Flickr"
        source_url = "https://flickr.test/p/1"
        license = "CC-BY-4.0"
        attribution = "Jane Doe"
        source_meta = {"post_id": 1}
        source_video_id = "vid-1"
        source_timestamp_ms = 4321
        source_shot_index = 7

    copied = copy_provenance(_Frame())
    assert set(copied).isdisjoint(LINEAGE)
    assert copied["license"] == "CC-BY-4.0"
