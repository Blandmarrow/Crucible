"""The optional LLM rewrite pass (M6 §3): a blueprint's template prompts → prose.

Off by default, switched on per scenario (`Scenario.rewrite`). It touches only rows
whose `prompt_origin` is "template" and that have not completed, one leaf at a time,
and writes each batch as it lands. A leaf that fails keeps its template prompts, so
the pass can only improve rows, never lose them. See
docs/dev/comfyui/structured-llm.md § The rewrite pass.

Not HTTP-aware, and the loop takes its LLM session, its writer, its cancel flag and
its clock as arguments, so it is tested without a provider or a database and §4's
stepping run can drive it.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.comfy import ComfyBlueprint, ComfyLeaf, ComfyRow
from backend.schemas.comfy_blueprint import BlueprintSpec
from backend.utils import chunked

logger = logging.getLogger(__name__)

# Values of `ComfyRow.prompt_origin`. NULL is a flat or hand-added row.
ORIGIN_TEMPLATE = "template"
ORIGIN_REWRITTEN = "rewritten"
ORIGIN_MANUAL = "manual"

# A leaf may spend this many requests beyond one per batch: a probe's second request,
# a retry, a re-ask after a miscount.
LEAF_CAP_SLACK = 2
MAX_RETRIES = 2
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0
# Backoff sleeps in slices this long, so a Stop is seen within one of them.
CANCEL_POLL_S = 0.5


@dataclass
class LeafTarget:
    leaf_id: str
    path: str
    # One line per entity the leaf combines: `character "Aria": a red-haired knight…`
    descriptors: list[str]
    # (row id, template prompt), in queue order.
    rows: list[tuple[str, str]]


@dataclass
class PassResult:
    rewritten: int = 0
    requested: int = 0
    leaves: int = 0
    # Leaves every batch of which was written. Not leaves - leaves_failed: a run that
    # stops early leaves some leaves neither done nor failed.
    leaves_done: int = 0
    leaves_failed: int = 0
    calls: int = 0
    budget: int = 0
    mode: str | None = None
    # done | cancelled | call_budget | too_many_failures | provider_error | write_error
    stop_reason: str = "done"
    error: str | None = None
    failed_paths: list[str] = field(default_factory=list)


def leaf_cap(rows: int, batch: int) -> int:
    return math.ceil(rows / batch) + LEAF_CAP_SLACK


def expected_calls(counts: list[int], batch: int) -> int:
    return sum(math.ceil(n / batch) for n in counts if n)


def call_budget(counts: list[int], batch: int) -> int:
    """The run's hard stop, in HTTP requests: the expected calls plus a quarter (at
    least two) for probes, retries and re-asks. 0 when there is nothing to rewrite."""
    expected = expected_calls(counts, batch)
    return expected + max(2, math.ceil(expected / 4)) if expected else 0


def estimate(targets: list[LeafTarget], batch: int) -> dict:
    counts = [len(t.rows) for t in targets]
    return {
        "rows": sum(counts),
        "leaves": len(targets),
        "calls": expected_calls(counts, batch),
        "budget": call_budget(counts, batch),
    }


def rewrite_scenarios(spec: BlueprintSpec) -> set[str]:
    return {s.name for s in spec.scenarios if s.rewrite}


def _descriptors(spec: BlueprintSpec, entity_ids: dict) -> list[str]:
    out = []
    for axis, ids in (entity_ids or {}).items():
        by_id = {e.id: e for e in spec.axes.get(axis, [])}
        for eid in ids:
            e = by_id.get(eid)
            if e is not None and e.descriptor.strip():
                out.append(f'{axis} "{e.label or e.id}": {e.descriptor.strip()}')
    return out


async def select_targets(
    db: AsyncSession, plan_id: str, prompt_alias: str, scenarios: set[str] | None = None, *,
    skip_scenarios: set[str] | frozenset[str] = frozenset(),
    row_ids: set[str] | None = None,
) -> list[LeafTarget]:
    """Every row the pass would rewrite now, grouped by leaf in leaf order.

    Reads the *saved* spec for the switch and the descriptors. `scenarios`, when given,
    narrows that to the scenarios the user reviewed: a job queued behind others must not
    pick up a scenario switched on while it waited, against a budget sized without it.
    The leaves are queried rather than read off `bp.leaves`, for the reason
    `prepare_top_up` gives. A leaf matches by its stored scenario name, so a scenario
    renamed since the last Apply has no targets until it is applied again.

    `skip_scenarios` leaves scenarios out (an automated run rewrites those itself, so
    their rows do not count as waiting), and `row_ids` narrows to exactly those rows
    (a run rewrites only the rows its pass will generate, so it never pays for a row
    it then leaves out).
    """
    bp = (
        await db.execute(select(ComfyBlueprint).where(ComfyBlueprint.plan_id == plan_id))
    ).scalar_one_or_none()
    if bp is None:
        return []
    spec = BlueprintSpec.model_validate(bp.spec or {})
    on = rewrite_scenarios(spec)
    if scenarios is not None:
        on &= scenarios
    on -= set(skip_scenarios)
    if not on:
        return []
    leaves = (
        await db.execute(
            select(ComfyLeaf)
            .where(ComfyLeaf.blueprint_id == bp.id, ComfyLeaf.scenario.in_(on))
            .order_by(ComfyLeaf.sort_order)
        )
    ).scalars().all()
    by_leaf: dict[str, list[tuple[int, str, str]]] = {}
    for chunk in chunked([l.id for l in leaves]):
        for rid, leaf_id, values, order in (
            await db.execute(
                select(ComfyRow.id, ComfyRow.leaf_id, ComfyRow.values, ComfyRow.sort_order).where(
                    ComfyRow.plan_id == plan_id,
                    ComfyRow.leaf_id.in_(chunk),
                    ComfyRow.prompt_origin == ORIGIN_TEMPLATE,
                    ComfyRow.status != "completed",
                )
            )
        ).all():
            if row_ids is not None and rid not in row_ids:
                continue
            text = (values or {}).get(prompt_alias)
            if isinstance(text, str) and text.strip():
                by_leaf.setdefault(leaf_id, []).append((order, rid, text))
    return [
        LeafTarget(
            leaf_id=l.id, path=l.path, descriptors=_descriptors(spec, l.entity_ids),
            rows=[(rid, text) for _, rid, text in sorted(by_leaf[l.id])],
        )
        for l in leaves if l.id in by_leaf
    ]


async def count_awaiting(
    db: AsyncSession, plan_id: str, prompt_alias: str, *,
    skip_scenarios: set[str] | frozenset[str] = frozenset(),
) -> int:
    targets = await select_targets(db, plan_id, prompt_alias, skip_scenarios=skip_scenarios)
    return sum(len(t.rows) for t in targets)


async def write_rewrites(db: AsyncSession, prompt_alias: str, pairs: list[tuple[str, str]]) -> int:
    """Write one batch's prose. Returns the rows written. Does not commit.

    Each UPDATE re-checks that the row is still an unfinished template row, so a hand
    edit (which sets "manual") or a run that completed it while the call was out wins.
    `values` is copied, never mutated in place: SQLAlchemy compares a JSON column by
    equality, and an edited same-object dict reads as unchanged.

    The read and the UPDATE are separate statements, so a write of `values` landing
    between them would be lost. The row routes that write `values` (PATCH, set-value,
    bulk-edit) therefore 409 while a rewrite is live, which is what closes that window.
    """
    current = dict(
        (
            await db.execute(
                select(ComfyRow.id, ComfyRow.values).where(ComfyRow.id.in_([rid for rid, _ in pairs]))
            )
        ).all()
    )
    written = 0
    for rid, text in pairs:
        if rid not in current:
            continue  # deleted while the call was out
        vals = dict(current[rid] or {})
        vals[prompt_alias] = text
        res = await db.execute(
            update(ComfyRow)
            .where(
                ComfyRow.id == rid,
                ComfyRow.prompt_origin == ORIGIN_TEMPLATE,
                ComfyRow.status != "completed",
            )
            .values(
                values=vals, prompt_origin=ORIGIN_REWRITTEN,
                # Changed text invalidates a failed attempt, as a hand edit does.
                status="pending", error_msg=None, prompt_id=None,
            )
        )
        written += res.rowcount or 0
    return written


async def _backoff(
    retry: int, cancelled: Callable[[], bool], sleep: Callable[[float], Awaitable[None]],
    rng: Callable[[], float],
) -> bool:
    """Sleep before retry number `retry` (1-based). False if a Stop arrived meanwhile."""
    remaining = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (retry - 1) * (0.5 + rng()))
    while remaining > 0:
        if cancelled():
            return False
        step = min(CANCEL_POLL_S, remaining)
        await sleep(step)
        remaining -= step
    return not cancelled()


async def run_pass(
    targets: list[LeafTarget],
    *,
    session,
    write: Callable[[list[tuple[str, str]]], Awaitable[int]],
    batch_size: int,
    cancelled: Callable[[], bool],
    on_batch: Callable[[PassResult, str], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,
) -> PassResult:
    """Rewrite every target, one leaf at a time. Never raises for an LLM failure: the
    outcome is in the result's `stop_reason`, and the caller decides what it means.

    `session` is a `prompt_generator.RewriteSession` (or a fake with the same
    `rewrite`, `requests`, `max_requests` and `mode`). Its `max_requests` is the run's
    hard budget; each leaf also stops at `leaf_cap` requests.
    """
    from backend.ml.prompt_generator import BudgetExhausted, RewriteError

    result = PassResult(
        requested=sum(len(t.rows) for t in targets), leaves=len(targets),
        budget=session.max_requests or 0,
    )
    abort_after = max(2, len(targets) // 4)

    async def _leaf(t: LeafTarget) -> bool:
        """True when every batch of the leaf was written. Sets stop_reason to stop the run."""
        cap = leaf_cap(len(t.rows), batch_size)
        start = session.requests
        for i in range(0, len(t.rows), batch_size):
            batch = t.rows[i:i + batch_size]
            retries = 0
            while True:
                if cancelled():
                    result.stop_reason = "cancelled"
                    return False
                if session.requests - start >= cap:
                    logger.info("blueprint_rewrite: leaf %s hit its cap of %d requests", t.path, cap)
                    return False
                try:
                    prose = await session.rewrite([text for _, text in batch], t.descriptors)
                except BudgetExhausted:
                    result.stop_reason = "call_budget"
                    return False
                except RewriteError as e:
                    result.error = str(e)
                    if e.kind == "fatal":
                        result.stop_reason = "provider_error"
                        return False
                    if e.kind == "rejected":
                        return False
                    if e.kind == "transient":
                        if retries >= MAX_RETRIES:
                            return False
                        retries += 1
                        if not await _backoff(retries, cancelled, sleep, rng):
                            result.stop_reason = "cancelled"
                            return False
                    # A miscount is asked again at once; the leaf cap bounds it.
                    continue
                try:
                    result.rewritten += await write([(rid, text) for (rid, _), text in zip(batch, prose)])
                except Exception as e:
                    # A locked database, say. Stop with what is already committed, so
                    # the caller can still record the result rather than fail blind.
                    logger.warning("blueprint_rewrite: writing a batch of %s failed", t.path, exc_info=True)
                    result.error = f"saving a batch failed: {e}"
                    result.stop_reason = "write_error"
                    return False
                result.calls = session.requests
                if on_batch is not None:
                    await on_batch(result, t.path)
                break
        return True

    for t in targets:
        ok = await _leaf(t)
        result.calls = session.requests
        if ok:
            result.leaves_done += 1
        if result.stop_reason != "done":
            break
        if not ok:
            result.leaves_failed += 1
            result.failed_paths.append(t.path)
            if result.leaves_failed > abort_after:
                result.stop_reason = "too_many_failures"
                break
    result.mode = session.mode
    return result
