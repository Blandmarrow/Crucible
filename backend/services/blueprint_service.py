"""Blueprint fill accounting and Apply: the DB side of structured generation.

`preview` and `apply` share `_compute`, so what the review panel shows is what Apply
writes. Every count here is recomputed from rows and images on each call, never kept
in a stored counter: a deleted image or a reset row would otherwise leave the number
quietly wrong. See docs/dev/comfyui/structured.md § Fill accounting.

Not HTTP-aware: the errors below are mapped to status codes by the router, so §4's
stepping run can call the same functions.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from sqlalchemy import delete as sa_delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import Image
from backend.models.comfy import ComfyBlueprint, ComfyLeaf, ComfyPlan, ComfyRow
from backend.schemas.comfy_blueprint import BlueprintSpec
from backend.services.blueprint_expand import (
    DEFAULT_ROW_CAP,
    HARD_ROW_CAP,
    SAMPLES_PER_LEAF,
    Expansion,
    Leaf,
    compose,
    expand,
    plan_fill,
)
from backend.services.blueprint_rewrite import (
    ORIGIN_TEMPLATE,
    call_budget,
    expected_calls,
    rewrite_scenarios,
)
from backend.utils import chunked

# The preview lists at most this many leaves, and composes samples for the first few.
PREVIEW_LEAF_LIMIT = 500
PREVIEW_SAMPLE_LEAVES = 20
OPEN_STATUSES = ("pending", "running", "failed")


class BlueprintError(Exception):
    pass


class BlueprintInvalid(BlueprintError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class OverCap(BlueprintError):
    pass


class TotalChanged(BlueprintError):
    """The rows Apply would add differ from what the user reviewed."""

    def __init__(self, reviewed: int, now: int):
        super().__init__(f"The blueprint now adds {now} rows, not the {reviewed} you reviewed — review it again")
        self.reviewed = reviewed
        self.now = now


@dataclass
class _Stats:
    pending: int = 0
    running: int = 0
    failed: int = 0
    completed: int = 0
    # Images completed rows produced (row.image_ids is never pruned, so its length is
    # what the row made, whatever has been deleted since).
    produced: int = 0
    image_ids: list[str] = field(default_factory=list)
    kept: int = 0

    @property
    def open(self) -> int:
        return self.pending + self.running + self.failed


@dataclass
class _Entry:
    leaf: Leaf
    db_leaf: ComfyLeaf | None
    stats: _Stats
    yield_: float
    to_add: int
    # For a leaf with no row yet: orphaned blueprint rows already filed in its folder,
    # which Apply re-points at the new leaf.
    adopt_row_ids: list[str] = field(default_factory=list)
    # Unfinished rows still holding template text: what the §3 pass would rewrite.
    template_open: int = 0


@dataclass
class _Fill:
    expansion: Expansion
    entries: list[_Entry]
    rows_to_add: int
    row_cap: int
    orphan_row_ids: list[str]
    orphans: dict[str, int]


@dataclass(frozen=True)
class _Row:
    id: str
    leaf_id: str | None
    status: str
    image_ids: tuple[str, ...]
    subfolder: str
    origin: str | None


async def load_blueprint(db: AsyncSession, plan_id: str) -> ComfyBlueprint | None:
    return (
        await db.execute(
            select(ComfyBlueprint)
            .where(ComfyBlueprint.plan_id == plan_id)
            .options(selectinload(ComfyBlueprint.leaves))
        )
    ).scalar_one_or_none()


async def _load_rows(db: AsyncSession, plan: ComfyPlan) -> tuple[list[_Row], set[str], float | None]:
    """The plan's rows, which of their images still exist, and the plan-wide images
    per completed row."""
    rows = [
        _Row(rid, leaf_id, status, tuple(image_ids or ()), subfolder or "", origin)
        for rid, leaf_id, status, image_ids, subfolder, origin in (
            await db.execute(
                select(
                    ComfyRow.id, ComfyRow.leaf_id, ComfyRow.status, ComfyRow.image_ids,
                    ComfyRow.subfolder, ComfyRow.prompt_origin,
                )
                .where(ComfyRow.plan_id == plan.id)
            )
        ).all()
    ]
    wanted = list(dict.fromkeys(i for r in rows if r.leaf_id for i in r.image_ids))
    existing: set[str] = set()
    for chunk in chunked(wanted):
        existing.update(
            (await db.execute(
                select(Image.id).where(Image.id.in_(chunk), Image.dataset_id == plan.dataset_id)
            )).scalars()
        )
    done = [r for r in rows if r.status == "completed"]
    produced = sum(len(r.image_ids) for r in done)
    plan_yield = produced / len(done) if done and produced else None
    return rows, existing, plan_yield


def _tally(rows: list[_Row], existing: set[str]) -> _Stats:
    s = _Stats()
    for r in rows:
        if r.status in ("pending", "running", "failed", "completed"):
            setattr(s, r.status, getattr(s, r.status) + 1)
        if r.status == "completed":
            s.produced += len(r.image_ids)
        s.image_ids.extend(r.image_ids)
    s.kept = len({i for i in s.image_ids if i in existing})
    return s


async def _compute(
    db: AsyncSession, plan: ComfyPlan, bp: ComfyBlueprint | None, spec: BlueprintSpec, seed: int,
    allow_over_cap: bool,
) -> _Fill:
    # Off the event loop: expansion is bounded, but still pure CPU on every preview.
    expansion = await asyncio.get_running_loop().run_in_executor(None, expand, spec, seed)
    rows, existing, plan_yield = await _load_rows(db, plan)
    db_by_path = {l.path: l for l in (bp.leaves if bp else [])}
    surviving = {db_by_path[l.path].id for l in expansion.leaves if l.path in db_by_path}

    by_leaf: dict[str, list[_Row]] = {}
    # Blueprint rows whose leaf this expansion does not keep, by the folder they were
    # filed in. A leaf *is* its folder, so a new leaf adopts the ones in its folder:
    # without that, undoing an edit (rename a label, then rename it back) re-makes the
    # folder under a new leaf id with nothing counted, and it gets a second full target.
    unkept: dict[str, list[_Row]] = {}
    for r in rows:
        if r.leaf_id is None:
            continue
        if r.leaf_id in surviving:
            by_leaf.setdefault(r.leaf_id, []).append(r)
        else:
            unkept.setdefault(r.subfolder, []).append(r)

    entries: list[_Entry] = []
    adopted: set[str] = set()
    for leaf in expansion.leaves:
        db_leaf = db_by_path.get(leaf.path)
        if db_leaf is not None:
            leaf_rows, adopt = by_leaf.get(db_leaf.id, []), []
        else:
            leaf_rows = adopt = unkept.get(leaf.path, [])
            adopted.update(r.id for r in adopt)
        s = _tally(leaf_rows, existing)
        yield_ = s.produced / s.completed if s.completed and s.produced else (plan_yield or 1.0)
        to_add = plan_fill(leaf.target, s.kept, s.open, yield_)
        template_open = sum(1 for r in leaf_rows if r.origin == ORIGIN_TEMPLATE and r.status != "completed")
        entries.append(_Entry(leaf, db_leaf, s, yield_, to_add, [r.id for r in adopt], template_open))

    orphans = {"leaves": 0, "pending": 0, "running": 0, "failed": 0, "completed": 0, "images": 0}
    orphan_rows: list[_Row] = []
    # A spec with errors expands to nothing, and reporting every existing row as an
    # orphan of it would only alarm someone who is halfway through typing a label.
    if not expansion.errors:
        orphan_rows = [r for group in unkept.values() for r in group if r.id not in adopted]
        s = _tally(orphan_rows, existing)
        orphans = {
            "leaves": len({r.leaf_id for r in orphan_rows}),
            "pending": s.pending, "running": s.running, "failed": s.failed, "completed": s.completed,
            "images": s.kept,
        }

    return _Fill(
        expansion=expansion,
        entries=entries,
        rows_to_add=sum(e.to_add for e in entries),
        row_cap=HARD_ROW_CAP if allow_over_cap else DEFAULT_ROW_CAP,
        orphan_row_ids=[r.id for r in orphan_rows if r.status == "pending"],
        orphans=orphans,
    )


async def preview(
    db: AsyncSession, plan: ComfyPlan, spec: BlueprintSpec, seed: int, *, allow_over_cap: bool = False
) -> dict:
    bp = await load_blueprint(db, plan.id)
    fill = await _compute(db, plan, bp, spec, seed, allow_over_cap)
    # What the §3 pass would rewrite once this is applied: the rows still holding
    # template text plus the ones Apply adds, in the scenarios that have it on. The
    # batch is the plan's saved one, which the Rewrite dialog starts from.
    on = rewrite_scenarios(spec)
    rewrite_counts = [e.template_open + e.to_add for e in fill.entries if e.leaf.scenario in on]
    batch = int((plan.gen_settings or {}).get("batch_size") or 5)
    leaves = []
    for i, e in enumerate(fill.entries[:PREVIEW_LEAF_LIMIT]):
        cursor = e.db_leaf.cursor if e.db_leaf else 0
        leaves.append({
            "path": e.leaf.path,
            "scenario": e.leaf.scenario,
            "target": e.leaf.target,
            "kept": e.stats.kept,
            "open": e.stats.open,
            "failed": e.stats.failed,
            "yield": round(e.yield_, 2),
            "to_add": e.to_add,
            # Images the folder still lacks: what Top up reports as short.
            "short": max(0, e.leaf.target - e.stats.kept),
            "samples": compose(e.leaf, cursor, SAMPLES_PER_LEAF) if i < PREVIEW_SAMPLE_LEAVES else [],
        })
    return {
        "leaves": leaves,
        "leaf_count": len(fill.entries),
        "rows_to_add": fill.rows_to_add,
        # Over every leaf, not just the listed 500.
        "images_short": sum(max(0, e.leaf.target - e.stats.kept) for e in fill.entries),
        "rows_pending": sum(e.stats.pending for e in fill.entries),
        "rows_failed": sum(e.stats.failed for e in fill.entries),
        "row_cap": fill.row_cap,
        "over_cap": fill.rows_to_add > fill.row_cap,
        "errors": fill.expansion.errors,
        "warnings": fill.expansion.warnings,
        "orphans": fill.orphans,
        "rewrite_rows": sum(rewrite_counts),
        "rewrite_calls": expected_calls(rewrite_counts, batch),
        "rewrite_call_budget": call_budget(rewrite_counts, batch),
    }


async def save_spec(db: AsyncSession, plan: ComfyPlan, spec: BlueprintSpec, seed: int) -> ComfyBlueprint:
    """Write the spec and seed, creating the blueprint on first save. Does not commit."""
    return _write_spec(db, plan, await load_blueprint(db, plan.id), spec, seed)


def _write_spec(
    db: AsyncSession, plan: ComfyPlan, bp: ComfyBlueprint | None, spec: BlueprintSpec, seed: int
) -> ComfyBlueprint:
    if bp is None:
        bp = ComfyBlueprint(id=str(uuid4()), plan_id=plan.id, leaves=[])
        db.add(bp)
    bp.spec = spec.model_dump()
    bp.seed = seed
    return bp


async def apply(
    db: AsyncSession, plan: ComfyPlan, spec: BlueprintSpec, seed: int, *,
    prompt_alias: str, start_order: int, confirm_total: int | None, allow_over_cap: bool,
    delete_orphan_pending: bool,
) -> dict:
    """Save the spec, reconcile the leaves, and add each leaf's missing rows. Commits once.

    Raises BlueprintInvalid, OverCap or TotalChanged before writing anything.
    `confirm_total` is the row count the user saw in the preview; `None` skips that
    check, for an automated run, which has no preview and holds the plan lock from
    this count to the write, so the count cannot move under it.
    """
    bp = await load_blueprint(db, plan.id)
    fill = await _compute(db, plan, bp, spec, seed, allow_over_cap)
    if fill.expansion.errors:
        raise BlueprintInvalid(fill.expansion.errors)
    if fill.rows_to_add > fill.row_cap:
        raise OverCap(
            f"The blueprint adds {fill.rows_to_add} rows, over the limit of {fill.row_cap}"
            + ("" if allow_over_cap else " — allow a larger expansion to go ahead")
        )
    if confirm_total is not None and fill.rows_to_add != confirm_total:
        raise TotalChanged(confirm_total, fill.rows_to_add)

    bp = _write_spec(db, plan, bp, spec, seed)
    paths = {e.leaf.path for e in fill.entries}
    for stale in [l for l in bp.leaves if l.path not in paths]:
        await db.delete(stale)

    deleted_rows = 0
    if delete_orphan_pending:
        for chunk in chunked(fill.orphan_row_ids):
            res = await db.execute(
                sa_delete(ComfyRow).where(ComfyRow.id.in_(chunk), ComfyRow.status == "pending")
            )
            deleted_rows += res.rowcount or 0

    order = start_order
    created = 0
    for i, e in enumerate(fill.entries):
        db_leaf = e.db_leaf
        if db_leaf is None:
            # An adopting leaf's walk starts past the rows it takes over, which the
            # walk most likely minted from the start, so Top up does not repeat them.
            db_leaf = ComfyLeaf(
                id=str(uuid4()), blueprint_id=bp.id, path=e.leaf.path, cursor=len(e.adopt_row_ids)
            )
            db.add(db_leaf)
        # Every NOT NULL column before the adoption UPDATE below, which autoflushes.
        db_leaf.scenario = e.leaf.scenario
        db_leaf.target = e.leaf.target
        db_leaf.entity_ids = e.leaf.entity_ids
        db_leaf.sort_order = i
        for chunk in chunked(e.adopt_row_ids):
            await db.execute(update(ComfyRow).where(ComfyRow.id.in_(chunk)).values(leaf_id=db_leaf.id))
        if not e.to_add:
            continue
        for prompt in compose(e.leaf, db_leaf.cursor, e.to_add):
            db.add(ComfyRow(
                plan_id=plan.id,
                sort_order=order,
                subfolder=e.leaf.path,
                leaf_id=db_leaf.id,
                values={prompt_alias: prompt},
                prompt_origin=ORIGIN_TEMPLATE,
            ))
            order += 1
        db_leaf.cursor = db_leaf.cursor + e.to_add
        created += e.to_add

    await db.commit()
    return {"created": created, "leaves": len(fill.entries), "deleted_rows": deleted_rows}


async def prepare_top_up(db: AsyncSession, plan: ComfyPlan) -> tuple[int, list[str]]:
    """After an Apply: reset the blueprint's failed rows and list its pending ones.

    Only rows of the blueprint's current leaves, so Top up never re-runs an orphan or a
    row typed by hand. Failed rows already counted as future images in the fill, so
    resetting them is what keeps that count honest. So are rows left `running`: the
    caller holds the plan lock and has checked that no run is live, so nothing is
    running them — a hard kill left them — and skipping them would report "every
    folder is full" over a folder that is short. Returns (rows reset, pending row ids
    in queue order). Commits.
    """
    # Queried, never read off `bp.leaves`: this runs in Apply's session, which sets
    # expire_on_commit=False, so an already-loaded collection is not reloaded and still
    # lists the leaves Apply just deleted — Top up then ran the orphans, not the new folders.
    leaf_ids = list(
        (
            await db.execute(
                select(ComfyLeaf.id).join(ComfyBlueprint).where(ComfyBlueprint.plan_id == plan.id)
            )
        ).scalars()
    )
    if not leaf_ids:
        return 0, []
    reset = 0
    pending: list[tuple[int, str]] = []
    for chunk in chunked(leaf_ids):
        rows = (
            await db.execute(
                select(ComfyRow).where(
                    ComfyRow.plan_id == plan.id,
                    ComfyRow.leaf_id.in_(chunk),
                    ComfyRow.status.in_(("pending", "failed", "running")),
                )
            )
        ).scalars().all()
        for r in rows:
            if r.status != "pending":
                r.status = "pending"
                r.error_msg = None
                r.prompt_id = None
                reset += 1
            pending.append((r.sort_order, r.id))
    await db.commit()
    return reset, [rid for _, rid in sorted(pending)]
