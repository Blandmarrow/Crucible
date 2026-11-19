"""The dataset health report (M12 phase 1): deterministic findings, with no LLM.

Crucible finds the problems; the analyst (phase 2) only explains them. Each finding
carries its numbers, a `FilterSpec` that selects the affected images, and the existing
page that fixes it. The report is a list plus counts by severity, never one score.

**Cost.** Built from what the Stats page already computes (`get_dataset_stats`, cached)
plus two statements of its own, both made through `dataset_service._stats_scan` so a
dataset-wide scan carries the Stats index pin (`backend/tests/test_stats_plan.py` reads
their plans). The whole report is cached in the Stats cache under a `"health"` slot;
the caption pass under `"health_captions"`. The Stats payload it reads is the cached
object and is never mutated.

**Images only**, like the Stats page: videos are sources and get no findings.

Thresholds come from `threshold_service` where a setting exists, so a finding never
disagrees with the quality flags the user already sees; the rest are the named
constants below, listed in the user doc (`docs/analyst.md`). See
docs/dev/analyst/health.md.
"""
import asyncio
import logging
from collections import Counter
from datetime import datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Dataset, DatasetBranch, DatasetVersion, Image, StyleSimilarityRun
from backend.schemas.health import (
    Baseline,
    DismissalOut,
    FilterSpec,
    Finding,
    FindingFix,
    HealthReport,
    HealthSummary,
    ResolvedFinding,
)
from backend.services import notes_service
from backend.services.dataset_service import (
    _image_validator,
    _stats_cache_get,
    _stats_cache_put,
    _stats_scan,
    get_dataset_stats,
    get_style_distribution,
)
from backend.services.similar_captions_service import is_tag_like
from backend.services.threshold_service import get_thresholds

logger = logging.getLogger(__name__)

# --- Thresholds with no setting of their own -----------------------------------
# CLIP's text encoder reads 77 tokens and drops the rest. The count is the persisted
# GPT-2 BPE count (`utils.count_caption_tokens`), the same one the Stats page buckets.
CLIP_TOKEN_LIMIT = 77
# A caption under this many tokens says too little to train on.
SHORT_CAPTION_TOKENS = 5
# The short side checked when the dataset sets no `target_resolution`.
DEFAULT_TARGET_SHORT_SIDE = 1024
# Fewer images than this is flagged as a small dataset.
SMALL_DATASET = 20
# A tag in at least this share of tag-style captions is "in nearly every caption".
TAG_EVERYWHERE_FRACTION = 0.9
# Tags-used-once only counts once there are this many distinct tags to compare.
TAGS_ONCE_MIN_DISTINCT = 20
# Tag-style and prose captions are "mixed" when the smaller kind is at least this share.
MIXED_CAPTIONS_FRACTION = 0.1
# An aspect-ratio bucket under this share of the images is a straggler bucket ...
AR_SPARSE_BUCKET_FRACTION = 0.05
# ... and the finding fires when at least this many images sit in straggler buckets.
AR_SPARSE_MIN_IMAGES = 3
# Style outliers: below Q1 - k * IQR of the dataset's style scores (Tukey's fence).
STYLE_OUTLIER_IQR_K = 1.5
# The most-used tags the caption pass keeps, for the analyst's Tags category.
TOP_TAGS = 30
# The ranked tags the cached caption pass keeps for the analyst's `tag_stats` tool
# (M12 phase 3), counted as `top` is, so the tool and the context never disagree.
TAG_STATS_MAX = 500

# Quality flags: stats key -> (finding id, severity, title, flag key, setting name).
_FLAG_FINDINGS: tuple[tuple[str, str, str, str, str, str | None], ...] = (
    ("blurry", "blurry", "warn", "Blurry images", "is_blurry", "blur_threshold"),
    ("noisy", "noisy", "info", "Noisy images", "is_noisy", "noise_threshold"),
    ("uniform", "uniform", "info", "Flat, near-uniform images", "is_uniform", "uniformity_threshold"),
    ("watermarked", "watermarked", "warn", "Images with a watermark", "has_watermark", "watermark_threshold"),
    ("nsfw", "nsfw", "info", "Images flagged NSFW", "is_nsfw", "nsfw_threshold"),
    ("ai_artifacts", "ai_artifacts", "warn", "Images with AI artifacts", "has_ai_artifacts", None),
)

_SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}

_CACHE_SLOT = "health"
_CAPTIONS_SLOT = "health_captions"


# The pages a finding's fix button opens, each with the name the sidebar shows. The
# analyst's system prompt lists these names, so the model points at real pages rather
# than inventing one ("Image Review", in the phase 2 trials). `_fix` refuses any other
# page, so a new finding cannot name a page the prompt leaves out.
FIX_PAGES: dict[str, str] = {
    "datasets": "Datasets",
    "captioning": "Captioning",
    "gallery": "Gallery",
    "stats": "Stats",
    "quality": "Score images",
    "consolidate": "Consolidate Tags",
}


def _fix(page: str, label: str) -> FindingFix:
    if page not in FIX_PAGES:
        raise ValueError(f"fix page {page!r} is not in FIX_PAGES")
    return FindingFix(page=page, label=label)


def _fingerprint(count: int) -> dict:
    """Coarse state stored with a dismissal: the count, and its power-of-two bucket."""
    return {"count": count, "bucket": count.bit_length()}


# --- Statements --------------------------------------------------------------


def _health_counts_stmt(
    dataset_id: str,
    subfolder: str | None,
    target_short_side: int,
    style_fence: float | None,
    dataset_license: str | None = None,
):
    """The counts the Stats payload lacks, in one pinned pass.

    `uncaptioned` and `unlicensed` are counted here although the Stats payload has
    near relatives, because each finding's count must equal what its filter selects:
    the Stats captioned count strips whitespace where the `captioned=false` filter
    does not, and the Stats licence breakdown folds everything past its 20th licence
    into one bucket, which can hide the unlicensed one."""
    short_side = func.min(Image.width, Image.height)
    dup_root = case(
        (func.json_extract(Image.quality_flags, "$.is_duplicate") == 1,
         func.json_extract(Image.quality_flags, "$.duplicate_of")),
        else_=None,
    )
    tokens = func.coalesce(Image.caption_token_count, 0)
    # The same expression `license_missing` filters on (routers/images.py).
    effective_license = func.coalesce(func.nullif(Image.license, ""), dataset_license or "", "")
    cols = [
        func.coalesce(func.sum(case((Image.caption_text == "", 1), else_=0)), 0).label("uncaptioned"),
        func.coalesce(func.sum(case((effective_license == "", 1), else_=0)), 0).label("unlicensed"),
        func.coalesce(func.sum(case((short_side < target_short_side, 1), else_=0)), 0).label("below_target"),
        func.min(short_side).label("min_short_side"),
        func.coalesce(func.sum(case((Image.scores_stale == True, 1), else_=0)), 0).label("stale"),  # noqa: E712
        func.count(func.distinct(dup_root)).label("dup_groups"),
        func.coalesce(
            func.sum(case(((tokens >= 1) & (tokens < SHORT_CAPTION_TOKENS), 1), else_=0)), 0
        ).label("short_captions"),
        func.coalesce(func.sum(case((Image.aesthetic_score.is_(None), 1), else_=0)), 0).label("no_aesthetic"),
        func.coalesce(func.sum(case((Image.blur_score.is_(None), 1), else_=0)), 0).label("no_technical"),
    ]
    if style_fence is not None:
        cols.append(func.coalesce(
            func.sum(case((Image.style_similarity_score < style_fence, 1), else_=0)), 0
        ).label("style_outliers"))
    return _stats_scan(select(*cols), dataset_id, subfolder)


def _health_captions_stmt(dataset_id: str, subfolder: str | None):
    """Every non-empty caption, for the tag and caption-style counts."""
    return _stats_scan(select(Image.caption_text), dataset_id, subfolder).where(Image.caption_text != "")


# --- The caption pass ----------------------------------------------------------


def _aggregate_captions(captions: list[str]) -> dict:
    """Tag frequencies over tag-style captions, plus the tag/prose split. Pure Python,
    run in an executor."""
    tag_like = 0
    prose = 0
    freq: Counter[str] = Counter()
    for text in captions:
        if not text.strip():
            continue
        if is_tag_like(text):
            tag_like += 1
            # Once per image, as `caption_service.get_tag_stats` counts.
            freq.update({t for t in (p.strip() for p in text.split(",")) if t})
        else:
            prose += 1
    singletons = sorted(t for t, n in freq.items() if n == 1)
    # Ties broken by the tag itself: a set's iteration order varies with the hash
    # seed, so `most_common` alone would name a different tag after a restart.
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    everywhere = [
        {"tag": t, "count": n}
        for t, n in ranked[:20]
        if tag_like and n >= TAG_EVERYWHERE_FRACTION * tag_like
    ]
    return {
        "tag_like": tag_like,
        "prose": prose,
        "distinct_tags": len(freq),
        "singleton_tags": len(singletons),
        "singleton_examples": singletons[:10],
        "everywhere": everywhere,
        # The analyst's Tags category (backend/services/analyst_context.py).
        "top": [{"tag": t, "count": n} for t, n in ranked[:TOP_TAGS]],
        # The analyst's `tag_stats` tool (backend/services/analyst_tools.py).
        "ranked": [{"tag": t, "count": n} for t, n in ranked[:TAG_STATS_MAX]],
    }


async def caption_summary(db: AsyncSession, dataset_id: str) -> dict:
    """The cached caption pass for the whole dataset: tag counts over tag-style captions
    and the tag/prose split. The analyst's Tags category reads it."""
    return await _caption_stats(db, dataset_id, await _image_validator(db, dataset_id, None))


async def _caption_stats(db: AsyncSession, dataset_id: str, image_validator: tuple) -> dict:
    key = (dataset_id, None, _CAPTIONS_SLOT)
    cached = _stats_cache_get(key, image_validator)
    if cached is not None:
        return cached
    captions = [r[0] for r in (await db.execute(_health_captions_stmt(dataset_id, None))).all()]
    payload = await asyncio.get_running_loop().run_in_executor(None, _aggregate_captions, captions)
    _stats_cache_put(key, image_validator, payload)
    return payload


# --- Building the findings -----------------------------------------------------


def _style_fence(dist: dict) -> float | None:
    q = dist.get("quantiles") or []
    step = dist.get("quantile_step") or 5
    if len(q) < 3 or not dist.get("scored"):
        return None
    q1 = q[25 // step]
    q3 = q[75 // step]
    return q1 - STYLE_OUTLIER_IQR_K * (q3 - q1)


def _build_findings(
    ds: Dataset,
    stats: dict,
    counts,
    captions: dict,
    thresholds,
    target_short_side: int,
    style_fence: float | None,
) -> list[Finding]:
    total = int(stats.get("image_count") or 0)
    out: list[Finding] = []

    def add(**kw) -> None:
        kw.setdefault("total", total)
        kw["fingerprint"] = _fingerprint(kw["count"])
        out.append(Finding(**kw))

    if not ds.goal.strip():
        add(
            id="no_goal", severity="info", title="No goal set",
            unit="images", count=0, total=0,
            numbers={"hint": "The analyst gives better advice when it knows what the dataset is for."},
            fix=_fix("datasets", "Set a goal"),
        )

    if total == 0:
        return out

    if total < SMALL_DATASET:
        add(id="small_dataset", severity="info", title="Small dataset",
            count=total, numbers={"min_images": SMALL_DATASET})

    # Captions
    if counts.uncaptioned:
        add(id="uncaptioned", severity="warn", title="Images with no caption",
            count=int(counts.uncaptioned),
            filter_spec=FilterSpec(captioned=False),
            fix=_fix("captioning", "Caption them"))
    if counts.short_captions:
        add(id="short_captions", severity="info", title="Very short captions",
            count=int(counts.short_captions),
            numbers={"under_tokens": SHORT_CAPTION_TOKENS},
            filter_spec=FilterSpec(caption_tokens_min=1, caption_tokens_max=SHORT_CAPTION_TOKENS),
            fix=_fix("captioning", "Re-caption them"))
    over = int((stats.get("caption_token_distribution") or {}).get(f"{CLIP_TOKEN_LIMIT}+", 0))
    if over:
        add(id="long_captions", severity="warn", title=f"Captions over {CLIP_TOKEN_LIMIT} tokens (CLIP truncates)",
            count=over, numbers={"token_limit": CLIP_TOKEN_LIMIT},
            filter_spec=FilterSpec(caption_tokens_min=CLIP_TOKEN_LIMIT),
            fix=_fix("captioning", "Re-caption them shorter"))

    # Resolution and aspect ratio
    if counts.below_target:
        add(id="low_resolution", severity="warn",
            title=f"Images below the target resolution ({target_short_side}px short side)",
            count=int(counts.below_target),
            numbers={
                "target_short_side": target_short_side,
                "target_from": "dataset" if ds.target_resolution else "default",
                "smallest_short_side": counts.min_short_side,
            },
            filter_spec=FilterSpec(short_side_max=target_short_side),
            fix=_fix("gallery", "Review or upscale them"))
    ar = stats.get("aspect_ratio_fine") or {}
    sparse = {b: n for b, n in ar.items() if 0 < n < AR_SPARSE_BUCKET_FRACTION * total}
    sparse_images = sum(sparse.values())
    if len(ar) > 1 and sparse_images >= AR_SPARSE_MIN_IMAGES:
        add(id="aspect_ratio_spread", severity="info",
            title="Images in rarely used aspect-ratio buckets",
            count=sparse_images,
            numbers={"sparse_buckets": sparse, "bucket_share_under": AR_SPARSE_BUCKET_FRACTION,
                     "buckets_used": len(ar)},
            fix=_fix("stats", "See the aspect ratios"))

    # Quality flags, from the stored flags, which the thresholds below produced
    flags = stats.get("quality_flag_counts") or {}
    for stats_key, fid, severity, title, flag_key, setting in _FLAG_FINDINGS:
        n = int(flags.get(stats_key) or 0)
        if n:
            numbers = {setting: getattr(thresholds, setting)} if setting else {}
            add(id=fid, severity=severity, title=title, count=n, numbers=numbers,
                filter_spec=FilterSpec(quality_flag=flag_key),
                fix=_fix("quality", "Review on Score images"))
    if counts.dup_groups:
        add(id="duplicates", severity="warn", title="Duplicate groups",
            unit="groups", count=int(counts.dup_groups), total=total,
            numbers={"duplicate_copies": int(flags.get("duplicate") or 0),
                     "duplicate_threshold": thresholds.duplicate_threshold},
            filter_spec=FilterSpec(quality_flag="is_duplicate"),
            fix=_fix("quality", "Open the duplicate resolver"))

    # Scores
    if counts.no_aesthetic:
        add(id="unscored_aesthetic", severity="info", title="Images with no aesthetic score",
            count=int(counts.no_aesthetic),
            filter_spec=FilterSpec(score_field="aesthetic_score", score_is_null=True),
            fix=_fix("quality", "Score them"))
    if counts.no_technical:
        add(id="unscored_technical", severity="info", title="Images with no technical scores",
            count=int(counts.no_technical),
            filter_spec=FilterSpec(score_field="blur_score", score_is_null=True),
            fix=_fix("quality", "Score them"))
    if counts.stale:
        add(id="stale_scores", severity="warn", title="Scores older than the pixels",
            count=int(counts.stale),
            filter_spec=FilterSpec(scores_stale=True),
            fix=_fix("quality", "Re-score them"))
    if style_fence is not None and getattr(counts, "style_outliers", 0):
        add(id="style_outliers", severity="info", title="Style outliers",
            count=int(counts.style_outliers),
            numbers={"below_score": round(style_fence, 4), "iqr_k": STYLE_OUTLIER_IQR_K},
            filter_spec=FilterSpec(score_field="style_similarity_score", max_score_lt=style_fence),
            fix=_fix("quality", "Review on Score images"))

    # Licence
    no_license = int(counts.unlicensed)
    if no_license:
        add(id="no_license", severity="info", title="Images with no licence recorded",
            count=no_license,
            filter_spec=FilterSpec(license_missing=True),
            # The dataset's default is the one-step fix: the count reads the
            # inherited default. Per-image licences are set from the gallery.
            fix=_fix("datasets", "Set a default licence"))

    # Tags and caption style
    tag_like = captions["tag_like"]
    for entry in captions["everywhere"][:1]:
        add(id="tag_everywhere", severity="info", title="A tag in nearly every caption",
            count=entry["count"], total=tag_like,
            numbers={"tags": captions["everywhere"], "fraction": TAG_EVERYWHERE_FRACTION,
                     "hint": "Expected for a trigger word; dismiss it with that reason."},
            # No filter: `search` is a substring ILIKE over captions and filenames
            # (with `_` a wildcard), so it would select more than the tag counts.
            fix=_fix("consolidate", "Review tags"))
    if captions["distinct_tags"] >= TAGS_ONCE_MIN_DISTINCT and captions["singleton_tags"]:
        add(id="tags_once", severity="info", title="Tags used only once",
            unit="tags", count=captions["singleton_tags"], total=captions["distinct_tags"],
            numbers={"examples": captions["singleton_examples"]},
            fix=_fix("consolidate", "Consolidate tags"))
    styled = tag_like + captions["prose"]
    minority = min(tag_like, captions["prose"])
    if styled and minority >= MIXED_CAPTIONS_FRACTION * styled:
        add(id="mixed_caption_styles", severity="info", title="Tag-style and prose captions mixed",
            count=minority, total=styled,
            numbers={"tag_style": tag_like, "prose": captions["prose"]},
            fix=_fix("captioning", "Re-caption in one style"))

    out.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.id))
    return out


# --- The report ----------------------------------------------------------------


async def _baseline(db: AsyncSession, ds: Dataset) -> tuple[DatasetVersion | None, str | None]:
    if not ds.current_branch_id:
        return None, None
    head_id = await db.scalar(select(DatasetBranch.head_version_id).where(DatasetBranch.id == ds.current_branch_id))
    if not head_id:
        return None, None
    return await db.get(DatasetVersion, head_id), head_id


def _summary(findings: list[Finding]) -> HealthSummary:
    s = HealthSummary()
    for f in findings:
        setattr(s, f.severity, getattr(s, f.severity) + 1)
    return s


async def compute_findings(db: AsyncSession, ds: Dataset) -> list[Finding]:
    """Every finding, dismissed or not, uncached. The building block of the report and
    of a snapshot's summary."""
    stats = await get_dataset_stats(db, ds.id)
    thresholds = await get_thresholds(db)
    target = ds.target_resolution or DEFAULT_TARGET_SHORT_SIDE
    style_fence = None
    if await db.scalar(select(StyleSimilarityRun.id).where(StyleSimilarityRun.dataset_id == ds.id)):
        style_fence = _style_fence(await get_style_distribution(db, ds.id))
    counts = (await db.execute(_health_counts_stmt(ds.id, None, target, style_fence, ds.license))).one()
    image_validator = await _image_validator(db, ds.id, None)
    captions = await _caption_stats(db, ds.id, image_validator)
    return _build_findings(ds, stats, counts, captions, thresholds, target, style_fence)


async def _validator(db: AsyncSession, ds: Dataset, head_id: str | None) -> tuple:
    thresholds = await get_thresholds(db)
    style_updated = await db.scalar(
        select(StyleSimilarityRun.updated_at).where(StyleSimilarityRun.dataset_id == ds.id)
    )
    return (
        *await _image_validator(db, ds.id, None),
        ds.updated_at,
        tuple(getattr(thresholds, k) for k in (
            "blur_threshold", "noise_threshold", "uniformity_threshold",
            "duplicate_threshold", "watermark_threshold", "nsfw_threshold",
        )),
        *await notes_service.dismissal_validator(db, ds.id),
        style_updated,
        head_id,
    )


async def get_health(db: AsyncSession, dataset_id: str, *, write_back: bool = True) -> HealthReport | None:
    """The report for one dataset, or None when it does not exist.

    `write_back` stores the severity counts on `Dataset.health_summary` for the card
    badge. It commits, so a caller inside a larger transaction passes False.
    """
    ds = await db.get(Dataset, dataset_id)
    if ds is None:
        return None
    version, head_id = await _baseline(db, ds)
    key = (dataset_id, None, _CACHE_SLOT)
    validator = await _validator(db, ds, head_id)
    cached = _stats_cache_get(key, validator)
    if cached is not None:
        report = cached["report"]
    else:
        report = await _build_report(db, ds, version)
        _stats_cache_put(key, validator, {"report": report})
    if write_back:
        try:
            await _write_summary(db, ds, report)
        except Exception:  # noqa: BLE001 — the badge is a nicety; the report is built
            # A write lock held elsewhere (a manual snapshot job holds one for the
            # whole file copy) would otherwise turn a finished report into a 500.
            await db.rollback()
            logger.warning("Could not store the health summary for dataset %s", dataset_id, exc_info=True)
    return report


async def _build_report(db: AsyncSession, ds: Dataset, version: DatasetVersion | None) -> HealthReport:
    findings = await compute_findings(db, ds)
    dismissals = await notes_service.live_dismissals(db, ds.id)

    baseline = None
    was: dict[str, int] = {}
    base_items: list[dict] = []
    if version is not None:
        baseline = Baseline(
            version_id=version.id, version_name=version.name, created_at=version.created_at,
            missing=version.findings_summary is None,
        )
        base_items = list(version.findings_summary or [])
        was = {i["id"]: int(i["count"]) for i in base_items}

    active: list[Finding] = []
    dismissed: list[Finding] = []
    for f in findings:
        if baseline is not None and not baseline.missing:
            f.was = was.get(f.id, 0)
        d = dismissals.get(f.id)
        if d is None:
            active.append(f)
            continue
        then = (d.fingerprint or {}).get("count")
        f.dismissal = DismissalOut(
            note_id=d.id, reason=d.text, permanent=d.permanent,
            dismissed_at=d.created_at, count_then=then,
        )
        if not d.permanent and then is not None and f.count > 2 * then:
            f.resurfaced = True
            active.append(f)
        else:
            dismissed.append(f)

    current_ids = {f.id for f in findings}
    resolved = [
        ResolvedFinding(id=i["id"], severity=i["severity"], was=int(i["count"]))
        for i in base_items if i["id"] not in current_ids and int(i["count"])
    ]
    return HealthReport(
        dataset_id=ds.id,
        findings=active,
        dismissed=dismissed,
        resolved=resolved,
        summary=_summary(active),
        baseline=baseline,
        computed_at=datetime.utcnow(),
    )


async def _write_summary(db: AsyncSession, ds: Dataset, report: HealthReport) -> None:
    """Store the severity counts for the card badge, without bumping `updated_at`.

    `updated_at` is part of the Stats and health cache validators, so bumping it here
    would evict both on every read. A Core UPDATE still applies the column's
    `onupdate`, hence the explicit `updated_at=Dataset.updated_at`.
    """
    counts = report.summary.model_dump()
    current = ds.health_summary or {}
    if {k: current.get(k) for k in counts} == counts:
        return
    summary = {**counts, "computed_at": report.computed_at.isoformat() + "Z"}
    await db.execute(
        update(Dataset)
        .where(Dataset.id == ds.id)
        .values(health_summary=summary, updated_at=Dataset.updated_at)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    # Read the row back rather than set the attribute: a set marks `ds` dirty, and a
    # later flush would write it again through the ORM, which bumps `updated_at`.
    await db.refresh(ds, ["health_summary", "updated_at"])


async def summary_for_snapshot(db: AsyncSession, dataset_id: str) -> list[dict] | None:
    """`[{id, count, severity}]` for a snapshot row, every finding included (dismissed
    ones too: the delta is about the dataset's state). Served from the report cache
    when it is warm. Never commits."""
    report = await get_health(db, dataset_id, write_back=False)
    if report is None:
        return None
    findings = sorted((*report.findings, *report.dismissed), key=lambda f: f.id)
    return [{"id": f.id, "count": f.count, "severity": f.severity} for f in findings]


async def find(db: AsyncSession, dataset_id: str, finding_id: str) -> Finding | None:
    """One current finding by id, dismissed or not."""
    report = await get_health(db, dataset_id, write_back=False)
    if report is None:
        return None
    for f in (*report.findings, *report.dismissed):
        if f.id == finding_id:
            return f
    return None
