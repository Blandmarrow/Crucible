"""The analyst's read-only tools (M12 phase 3c).

A model that passed the tool-call probe (`services/tool_probe.py`) can look things up
during a reply: a finding in detail, captions behind a finding or a filter, tag counts,
the snapshots, and what changed since one. Each tool is a `Tool` in `TOOLS`:

- **A hand-written JSON schema** for its arguments, plain types only (no `anyOf`, no
  `null`), since servers differ in how much JSON Schema they accept; and a pydantic model
  (`extra="forbid"`, bounded numbers) that validates what the model sent.
  `test_analyst_tools.py` checks the two name the same fields.
- **The categories it needs.** A tool is offered only while every one is on
  (`tool_specs`), and its result goes through `analyst_context.render_tool_result`, the
  tools' privacy filter, all the same. Two layers: not offered, and filtered anyway.
- **A read-only handler** returning `ContextItem`s or an error, never a string and
  never an exception for a bad argument.

`run_tool` is the one entry point: it finds the tool, checks it is offered, parses and
validates the arguments, runs the handler and renders the result. Whatever goes wrong,
the model gets a short error it can act on, and nothing is written. The loop that calls
it is phase 3d. See docs/dev/analyst/tools.md.
"""
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Dataset, Image
from backend.models.versioning import DatasetVersion
from backend.schemas.health import FilterSpec
from backend.services import health_service, version_service
from backend.services.analyst_context import (
    ContextItem,
    ToolRender,
    _stratified_positions,
    base_name,
    finding_line,
    render_tool_result,
    summary_changes,
    summary_of,
)
from backend.services.dataset_service import get_dataset_stats

logger = logging.getLogger(__name__)

SAMPLE_MAX = 20
SNAPSHOTS_MAX = 20
TAGS_MAX = 100
DIFF_EXAMPLES = 5


@dataclass(frozen=True)
class Outcome:
    items: list[ContextItem]
    error: str | None = None


def _fail(message: str) -> Outcome:
    return Outcome(items=[], error=message)


Handler = Callable[[AsyncSession, Dataset, BaseModel], Awaitable[Outcome]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    args: type[BaseModel]
    needs: frozenset[str]
    handler: Handler

    def spec(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- list_snapshots ----------------------------------------------------------------


class ListSnapshotsArgs(_Args):
    limit: int = Field(default=10, ge=1, le=SNAPSHOTS_MAX)


async def _list_snapshots(db: AsyncSession, ds: Dataset, args: ListSnapshotsArgs) -> Outcome:
    total = int(await db.scalar(
        select(func.count(DatasetVersion.id)).where(DatasetVersion.dataset_id == ds.id)) or 0)
    if not total:
        return Outcome([ContextItem("numbers", "No snapshots yet.", own=True)])
    rows = (await db.execute(
        select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
        .order_by(DatasetVersion.created_at.desc(), DatasetVersion.id).limit(args.limit)
    )).scalars().all()
    _head, head_id = await health_service._baseline(db, ds)
    items = [ContextItem("numbers", f"{total} snapshots in all; the newest {len(rows)}, newest first.", own=True)]
    for v in rows:
        facts = [f"id {v.id}", f"{v.created_at:%Y-%m-%d %H:%M}", f"{v.image_count} images", v.source]
        if v.is_pinned:
            facts.append("pinned")
        if v.id == head_id:
            facts.append("the latest on the current branch")
        facts.append("findings recorded" if v.findings_summary is not None else "no findings recorded")
        items.append(ContextItem("numbers", " · ".join(facts), own=True, label=v.name or "",
                                 label_category="identity"))
        if (v.description or "").strip():
            items.append(ContextItem("identity", v.description, prefix=f"description of {v.id}: "))
    return Outcome(items)


# --- get_finding -------------------------------------------------------------------


class GetFindingArgs(_Args):
    finding_id: str = Field(min_length=1, max_length=64)


async def _get_finding(db: AsyncSession, ds: Dataset, args: GetFindingArgs) -> Outcome:
    f = await health_service.find(db, ds.id, args.finding_id)
    if f is None:
        return _fail(f"No finding has the id {args.finding_id!r}. The ids are the ones in [[finding:…]].")
    items = [ContextItem("numbers", finding_line(f).removeprefix("- "), own=True)]
    if f.was is not None:
        items.append(ContextItem("numbers", f"At the latest snapshot: {f.was}.", own=True))
    if f.dismissal is not None:
        d = f.dismissal
        when = f"{d.dismissed_at:%Y-%m-%d}"
        then = f", when it counted {d.count_then}" if d.count_then is not None else ""
        items.append(ContextItem(
            "numbers", f"Dismissed on {when}{then}{' for good' if d.permanent else ''}.", own=True))
        if d.reason.strip():
            items.append(ContextItem("notes", d.reason, prefix="The user's reason for dismissing it: "))
    for entry in f.numbers.get("tags") or []:
        if isinstance(entry, dict) and entry.get("tag"):
            items.append(ContextItem("tags", str(entry["tag"]), suffix=f" ({int(entry.get('count') or 0)})"))
    for tag in f.numbers.get("examples") or []:
        if isinstance(tag, str) and tag:
            items.append(ContextItem("tags", tag, suffix=" (used once)"))
    if f.filter_spec is not None:
        items.append(ContextItem(
            "numbers", f"Its images can be sampled with sample_captions(finding_id={f.id!r}).", own=True))
    return Outcome(items)


# --- sample_captions ---------------------------------------------------------------


class SampleCaptionsArgs(_Args):
    finding_id: str | None = Field(default=None, min_length=1, max_length=64)
    filter: FilterSpec | None = None
    limit: int = Field(default=SAMPLE_MAX, ge=1, le=SAMPLE_MAX)

    @model_validator(mode="after")
    def _one_scope(self):
        if self.finding_id is not None and self.filter is not None:
            raise ValueError("give finding_id or filter, not both")
        return self


async def _sample_captions(db: AsyncSession, ds: Dataset, args: SampleCaptionsArgs) -> Outcome:
    # The filter builder lives in the images router and is imported here, late, on
    # purpose: `feat/m4-rating-port` edits its body, so moving it now would clash. M8
    # stage 8 (split `images.py`) gives it a service home.
    from fastapi import HTTPException

    from backend.routers.images import _apply_image_filters
    from backend.schemas.image import ImageFilterParams

    scope = "the whole dataset"
    spec = args.filter
    if args.finding_id is not None:
        f = await health_service.find(db, ds.id, args.finding_id)
        if f is None:
            return _fail(f"No finding has the id {args.finding_id!r}.")
        if f.filter_spec is None:
            return _fail(f"[[finding:{f.id}]] has no image filter: it is about the dataset as a whole, "
                         "not a set of images. Call get_finding for its detail.")
        spec = f.filter_spec
        scope = f"[[finding:{f.id}]]"
    elif spec is not None:
        scope = "the filter given"
    spec_values = spec.model_dump(exclude_none=True) if spec is not None else {}
    try:
        params = ImageFilterParams(dataset_id=ds.id, **spec_values)
    except ValidationError as exc:
        return _fail("The filter was refused: " + _validation_message(exc))
    try:
        n = int(await db.scalar(_apply_image_filters(select(func.count(Image.id)), params)) or 0)
        if not n:
            return Outcome([ContextItem("numbers", f"No images match {scope}.", own=True)])
        k = min(args.limit, n)
        picks = _stratified_positions(n, k, f"{ds.id}:{json.dumps(spec_values, sort_keys=True)}")
        tokens = func.coalesce(Image.caption_token_count, 0)
        ranked = _apply_image_filters(
            select(Image.id, func.row_number().over(order_by=(tokens, Image.id)).label("rn")), params
        ).subquery()
    except HTTPException as exc:
        return _fail(f"The filter was refused: {exc.detail}")
    order = {
        row_id: rn for row_id, rn in (await db.execute(
            select(ranked.c.id, ranked.c.rn).where(ranked.c.rn.in_(picks)))).all()
    }
    found = (await db.execute(
        select(Image.id, Image.filename, Image.caption_text).where(Image.id.in_(list(order))))).all()
    items = [ContextItem(
        "numbers", f"{n} images match {scope}; {k} sampled, spread across caption lengths.", own=True)]
    for _id, filename, caption in sorted(found, key=lambda r: order[r[0]]):
        items.append(ContextItem("captions", (caption or "").strip() or "(no caption)",
                                 label=base_name(filename or ""), label_category="file_names"))
    return Outcome(items)


# --- tag_stats ---------------------------------------------------------------------


class TagStatsArgs(_Args):
    contains: str | None = Field(default=None, min_length=1, max_length=80)
    limit: int = Field(default=30, ge=1, le=TAGS_MAX)


async def _tag_stats(db: AsyncSession, ds: Dataset, args: TagStatsArgs) -> Outcome:
    summary = await health_service.caption_summary(db, ds.id)
    ranked = summary.get("ranked") or []
    items = [ContextItem("numbers", (
        f"{summary['tag_like']} tag-style captions, {summary['distinct_tags']} distinct tags, "
        f"{summary['singleton_tags']} used only once. {summary['prose']} prose captions are not counted."),
        own=True)]
    if args.contains is not None:
        needle = args.contains.casefold()
        ranked = [t for t in ranked if needle in t["tag"].casefold()]
        searched = (f"; only the {health_service.TAG_STATS_MAX} most used are searched"
                    if summary["distinct_tags"] > health_service.TAG_STATS_MAX else "")
        items.append(ContextItem("numbers", f"{len(ranked)} tags contain the text asked for{searched}.", own=True))
    if not ranked:
        return Outcome(items)
    items.append(ContextItem("numbers", f"Counts are images whose caption has the tag; the top {min(args.limit, len(ranked))}:",
                             own=True))
    items += [ContextItem("tags", t["tag"], suffix=f" ({t['count']})") for t in ranked[:args.limit]]
    return Outcome(items)


# --- compare_snapshot --------------------------------------------------------------


class CompareSnapshotArgs(_Args):
    version_id: str = Field(min_length=1, max_length=36)


async def _compare_snapshot(db: AsyncSession, ds: Dataset, args: CompareSnapshotArgs) -> Outcome:
    v = await db.get(DatasetVersion, args.version_id)
    if v is None or v.dataset_id != ds.id:
        return _fail(f"No snapshot of this dataset has the id {args.version_id!r}. Call list_snapshots for the ids.")
    stats = await get_dataset_stats(db, ds.id)
    now_images = int(stats.get("image_count") or 0)
    items = [ContextItem(
        "numbers", f"Snapshot {v.id} from {v.created_at:%Y-%m-%d %H:%M} had {v.image_count} images; now {now_images}.",
        own=True, label=v.name or "", label_category="identity")]
    if v.findings_summary is None:
        items.append(ContextItem("numbers", "Its findings were not recorded, so they cannot be compared.", own=True))
    else:
        report = await health_service.get_health(db, ds.id, write_back=False)
        changes = summary_changes(v.findings_summary, summary_of(report)) if report is not None else []
        items.append(ContextItem(
            "numbers", ("Findings since then: " + "; ".join(changes) + ".") if changes else "No finding changed since then.",
            own=True))

    head, head_id = await health_service._baseline(db, ds)
    if head is None or head_id == v.id:
        items.append(ContextItem("numbers", (
            "It is the latest snapshot on the current branch: changes made since are not recorded image by image."
            if head_id == v.id else "The current branch has no snapshot to compare it with image by image."),
            own=True))
        return Outcome(items)
    diff = await version_service.diff_versions(db, ds.id, v.id, head_id)
    s = diff["summary"]
    items.append(ContextItem("numbers", (
        f"Between it and the latest snapshot on the current branch ({head.created_at:%Y-%m-%d %H:%M}): "
        f"{s['added']} images added, {s['removed']} removed, {s['modified']} changed, {s['unchanged']} unchanged."),
        own=True, label=head.name or "", label_category="identity"))
    fields: dict[str, int] = {}
    for m in diff["modified"]:
        for col in m.get("changes") or {}:
            fields[col] = fields.get(col, 0) + 1
    if fields:
        items.append(ContextItem("numbers", "Changed, by column: " + ", ".join(
            f"{col} {n}" for col, n in sorted(fields.items(), key=lambda kv: (-kv[1], kv[0]))) + ".", own=True))
    for what, rows in (("added", diff["added"]), ("removed", diff["removed"])):
        for row in rows[:DIFF_EXAMPLES]:
            items.append(ContextItem("file_names", base_name(row.get("filename") or ""), prefix=f"{what}: "))
    return Outcome(items)


# --- The registry ------------------------------------------------------------------

_LIMIT = {"type": "integer", "minimum": 1}
_FILTER_SCHEMA = _object({
    "captioned": {"type": "boolean", "description": "true: captioned images only; false: uncaptioned only."},
    "search": {"type": "string", "description": "Text the caption or file name contains."},
    "caption_tokens_min": {"type": "integer", "description": "Captions at least this many tokens long."},
    "caption_tokens_max": {"type": "integer", "description": "Captions at most this many tokens long."},
    "short_side_max": {"type": "integer", "description": "Images whose short side is at most this many pixels."},
    "quality_flag": {"type": "string", "description": "Images with this quality flag set."},
    "score_field": {"type": "string", "description": "The score that max_score_lt or score_is_null applies to."},
    "score_is_null": {"type": "boolean", "description": "true: images not yet scored on score_field."},
    "max_score_lt": {"type": "number", "description": "Images whose score_field is below this."},
    "scores_stale": {"type": "boolean", "description": "true: images whose scores predate an edit."},
    "license_missing": {"type": "boolean", "description": "true: images with no licence."},
})

TOOLS: dict[str, Tool] = {t.name: t for t in (
    # First and argument-free, so the e2e suite's `[[fake:tool]]` (which calls the first
    # tool offered with `{}`) exercises a real tool.
    Tool(
        name="list_snapshots",
        description="List this dataset's snapshots, newest first: id, date, image count, and whether its "
                    "findings were recorded. Use an id with compare_snapshot.",
        parameters=_object({"limit": {**_LIMIT, "maximum": SNAPSHOTS_MAX, "description": "How many (default 10)."}}),
        args=ListSnapshotsArgs, needs=frozenset({"numbers"}), handler=_list_snapshots,
    ),
    Tool(
        name="get_finding",
        description="One finding in detail, dismissed ones included: its numbers, its fix, whether and why it "
                    "was dismissed, and the tags it is about.",
        parameters=_object({"finding_id": {"type": "string", "description": "The id, as in [[finding:id]]."}},
                           ("finding_id",)),
        args=GetFindingArgs, needs=frozenset({"numbers"}), handler=_get_finding,
    ),
    Tool(
        name="sample_captions",
        description="Read the captions of up to 20 images, spread across caption lengths: the images a finding "
                    "is about (finding_id), the images a filter selects, or, with neither, the whole dataset.",
        parameters=_object({
            "finding_id": {"type": "string", "description": "A finding's id, as in [[finding:id]]."},
            "filter": _FILTER_SCHEMA,
            "limit": {**_LIMIT, "maximum": SAMPLE_MAX, "description": "How many (default 20)."},
        }),
        args=SampleCaptionsArgs, needs=frozenset({"captions"}), handler=_sample_captions,
    ),
    Tool(
        name="tag_stats",
        description="Tag counts over the tag-style captions: the most used tags, or those containing some text.",
        parameters=_object({
            "contains": {"type": "string", "description": "Only tags containing this text, ignoring case."},
            "limit": {**_LIMIT, "maximum": TAGS_MAX, "description": "How many tags (default 30)."},
        }),
        args=TagStatsArgs, needs=frozenset({"tags"}), handler=_tag_stats,
    ),
    Tool(
        name="compare_snapshot",
        description="What changed since a snapshot: each finding's count then and now, and the images added, "
                    "removed or changed between it and the latest snapshot.",
        parameters=_object({"version_id": {"type": "string", "description": "A snapshot id from list_snapshots."}},
                           ("version_id",)),
        args=CompareSnapshotArgs, needs=frozenset({"numbers"}), handler=_compare_snapshot,
    ),
)}


def offered(privacy: dict[str, bool]) -> list[Tool]:
    """The tools this chat's privacy allows, in registry order."""
    return [t for t in TOOLS.values() if all(privacy.get(c) for c in t.needs)]


def tool_specs(privacy: dict[str, bool]) -> list[dict]:
    """The `tools` a request offers under this privacy. A tool's description and schema
    are Crucible's own text; nothing about the dataset is in them."""
    return [t.spec() for t in offered(privacy)]


def _validation_message(exc: ValidationError) -> str:
    # Field and message only: never the input, which is the model's own text anyway.
    parts = []
    for err in exc.errors(include_input=False, include_url=False)[:5]:
        where = ".".join(str(p) for p in err.get("loc") or ()) or "arguments"
        parts.append(f"{where}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


async def run_tool(
    db: AsyncSession, ds: Dataset, name: str, arguments: str, privacy: dict[str, bool],
) -> ToolRender:
    """Run one tool call and render its result through the privacy filter. Never raises
    for anything the model sent; an unexpected failure is logged and reported as one."""
    title = f"Result of {name}" if name in TOOLS else "Result of the tool call"
    tool = TOOLS.get(name)
    if tool is None:
        return render_tool_result(title, [], privacy, error=(
            f"There is no tool called {name[:60]!r}. The tools are: "
            + ", ".join(t.name for t in offered(privacy)) + "."))
    if tool not in offered(privacy):
        return render_tool_result(title, [], privacy, error=(
            "This tool is not available in this chat: the user does not share what it reads."))
    try:
        raw = json.loads(arguments) if (arguments or "").strip() else {}
    except ValueError:
        return render_tool_result(title, [], privacy, error="The arguments were not valid JSON.")
    if not isinstance(raw, dict):
        return render_tool_result(title, [], privacy, error="The arguments must be a JSON object.")
    try:
        args = tool.args.model_validate(raw)
    except ValidationError as exc:
        return render_tool_result(title, [], privacy, error="Invalid arguments: " + _validation_message(exc))
    try:
        outcome = await tool.handler(db, ds, args)
    except Exception:  # noqa: BLE001 — the model gets an error, the log gets the detail
        logger.error("Analyst tool %s failed for dataset %s", name, ds.id, exc_info=True)
        return render_tool_result(title, [], privacy, error="The tool failed inside Crucible; see the log.")
    return render_tool_result(title, outcome.items, privacy, error=outcome.error)
