"""Other captions: how the dataset's other images were captioned (M11 #45).

Read-only and torch-free. Four ways to pick the neighbours of one image, all
within that image's own dataset and among captioned images only:

- ``tags``: images whose caption shares terms with this one, scored by idf so a
  term on every image counts for little;
- ``objects``: the same idf score over detection labels;
- ``clip``: cosine over stored CLIP vectors;
- ``recent``: the most recently captioned.

Every query selects explicit columns and never an ORM ``Image``: ``clip_embedding``
is deferred and ``generation_metadata`` is large. The two dataset-wide scans are
one streamed pass each, pinned to an index (``_scan`` says why), and handled in
partitions, so no result set holds the whole dataset. The numpy dot products are
CPU work, so they go on the default pool; this module never touches the GPU thread.
"""
import asyncio
import heapq
import math
import re

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import indexed_by
from backend.models import Image
from backend.models.detection import Detection
from backend.services.dataset_service import STATS_SCAN_INDEX
from backend.utils import chunked

PAGE_SIZE = 2000

REASON_NO_CAPTION = "This image has no caption yet"
REASON_NO_DETECTIONS = "No detections on this image"
REASON_NO_TERMS = "No words to match in this caption"
REASON_NO_CLIP = "No CLIP vector yet. Run Style embeddings · CLIP on the Quality page."

# Unicode letters and digits, so a caption in another script still has words.
_WORD_RE = re.compile(r"[^\W_][\w']*")
_SPACE_RE = re.compile(r"\s+")

_STOPWORDS = frozenset("""
a an and are as at be been but by for from had has have he her his if in into is it its
of on or our she so than that the their them then there these they this those to too was
we were what when where which while who will with you your very just also can not no
""".split())


def _norm_tag(part: str) -> str:
    return _SPACE_RE.sub(" ", part.replace("_", " ")).strip().lower()


def _is_tag_like(text: str) -> bool:
    parts = [p for p in (s.strip() for s in text.split(",")) if p]
    if len(parts) < 2:
        return False
    short = sum(1 for p in parts if len(_norm_tag(p).split(" ")) <= 4)
    return short * 2 > len(parts)


def _terms_with_spelling(text: str, kind: str) -> dict[str, str]:
    """normalized term -> the caption's own spelling of it, first occurrence wins."""
    out: dict[str, str] = {}
    if kind == "tags":
        for raw in text.split(","):
            raw = raw.strip()
            term = _norm_tag(raw)
            if term and term not in out:
                out[term] = raw
    else:
        for word in _WORD_RE.findall(text.lower()):
            if len(word) >= 3 and word not in _STOPWORDS and word not in out:
                out[word] = word
    return out


def _shared_terms(text: str, kind: str, query_set: frozenset[str]) -> tuple[frozenset[str], int]:
    """The normalized terms `text` shares with `query_set`, and how many terms it has.

    The hot loop of a tags scan, run once per captioned image, so it stays in C where it
    can: one lower(), one split, and a set intersection. It agrees with
    `_terms_with_spelling` on every term `query_set` can hold: a query term is never a
    stopword or shorter than three characters, so prose needs no filtering here, and a
    part with an inner run of whitespace takes the slow normalization only when one is
    present. Measured on 80k tag captions (2026-11-14): 2.2 s per request with the full
    parse per row, which is why the spelling is now worked out for the winners only.
    """
    low = text.lower()
    if kind == "tags":
        low = low.replace("_", " ")
        if "  " in low or "\t" in low or "\n" in low or "\r" in low:
            parts = (" ".join(p.split()) for p in low.split(","))
        else:
            parts = map(str.strip, low.split(","))
        parts = list(parts)
        return query_set.intersection(parts), len(parts)
    words = _WORD_RE.findall(low)
    return query_set.intersection(words), len(words)


def parse_caption_terms(text: str) -> tuple[str, list[str]]:
    """``("tags" | "prose", terms)``, terms deduplicated, in caption order."""
    text = text or ""
    kind = "tags" if _is_tag_like(text) else "prose"
    return kind, list(_terms_with_spelling(text, kind))


def _idf(n: int, df: int) -> float:
    return math.log((n + 1) / (df + 1)) + 1


def _rank(scored: dict[str, tuple[float, list[str]]], limit: int) -> list[tuple[str, float, list[str]]]:
    # A heap, not a sort: on a tag dataset nearly every image shares a tag, so `scored`
    # can hold the whole dataset and only `limit` of it is wanted.
    ordered = heapq.nsmallest(limit, scored.items(), key=lambda kv: (-kv[1][0], kv[0]))
    return [(i, s, shared) for i, (s, shared) in ordered]


def _candidate_filters(dataset_id: str, image_id: str):
    return (Image.dataset_id == dataset_id, Image.caption_text != "", Image.id != image_id)


def _scan(stmt):
    """One streamed pass over the dataset, read through `ix_images_dataset_id`.

    The Stats scans' pin (decision 15, `dataset_service.STATS_SCAN_INDEX`), for the
    same reason: unpinned, SQLite reads these rows through `ix_images_dataset_updated_at`,
    a random-order walk over rows that carry the embedding blobs. Measured on a seeded
    100k-row dataset (2026-11-14): one pinned pass 190 ms against 360 ms unpinned. The
    keyset pages this replaced (`Image.id > last ORDER BY id LIMIT 2000`) were far worse,
    since each page re-sorted the whole dataset: 15 s for one request. Time it again if
    the images indexes change.
    """
    return indexed_by(stmt, Image, STATS_SCAN_INDEX)


async def _hydrate(
    db: AsyncSession, ranked: list[tuple[str, float, list[str]]]
) -> list[dict]:
    """Fetch display columns for the winners and keep their rank order."""
    if not ranked:
        return []
    rows = {}
    for chunk in chunked([r[0] for r in ranked]):
        res = await db.execute(
            select(
                Image.id, Image.filename, Image.subfolder, Image.updated_at,
                Image.captioned_at, Image.caption_text,
            )
            .where(Image.id.in_(chunk))
        )
        for r in res.all():
            rows[r.id] = r
    items = []
    for image_id, score, shared in ranked:
        r = rows.get(image_id)
        if r is None:  # deleted between the scan and now
            continue
        items.append({
            "id": r.id,
            "filename": r.filename,
            "subfolder": r.subfolder or "",
            "updated_at": r.updated_at,
            "captioned_at": r.captioned_at,
            "caption_text": r.caption_text,
            "score": round(float(score), 4),
            "shared": shared,
        })
    return items


def _result(by: str, items: list[dict], available: bool = True, reason: str | None = None) -> dict:
    return {"by": by, "available": available, "reason": reason, "items": items}


async def by_tags(db: AsyncSession, image_id: str, dataset_id: str, caption: str, limit: int) -> dict:
    kind, query_terms = parse_caption_terms(caption)
    if not query_terms:
        return _result("tags", [], False, REASON_NO_TERMS if caption.strip() else REASON_NO_CAPTION)
    query_set = frozenset(query_terms)
    df = dict.fromkeys(query_terms, 0)
    hits: dict[str, tuple[frozenset[str], int]] = {}  # id -> (shared terms, its term count)
    n = 0
    result = await db.stream(_scan(
        select(Image.id, Image.caption_text).where(*_candidate_filters(dataset_id, image_id))
    ))
    async for part in result.partitions(PAGE_SIZE):
        for row in part:
            n += 1
            shared, size = _shared_terms(row.caption_text, kind, query_set)
            if shared:
                for t in shared:
                    df[t] += 1
                hits[row.id] = (shared, size)
    weight = {t: _idf(n, c) for t, c in df.items()}  # once per term, not once per hit
    # Divided by the root of the candidate's length, so a 150-tag caption does not win
    # on the tags it shares by chance alone (a cosine with binary idf-weighted terms).
    scored = {
        cid: (sum(weight[t] for t in shared) / math.sqrt(max(size, 1)), shared)
        for cid, (shared, size) in hits.items()
    }
    items = await _hydrate(db, _rank(scored, limit))
    # The caption's own spelling, in its own order, worked out for the winners only.
    for item in items:
        shared = scored[item["id"]][1]
        item["shared"] = [
            raw for term, raw in _terms_with_spelling(item["caption_text"], kind).items() if term in shared
        ]
    return _result("tags", items)


async def by_objects(db: AsyncSession, image_id: str, dataset_id: str, limit: int) -> dict:
    labels = [r[0] for r in (await db.execute(
        select(Detection.label).where(Detection.image_id == image_id).distinct()
    )).all()]
    if not labels:
        return _result("objects", [], False, REASON_NO_DETECTIONS)
    filters = _candidate_filters(dataset_id, image_id)
    n = (await db.execute(
        select(func.count(func.distinct(Detection.image_id)))
        .join(Image, Image.id == Detection.image_id)
        .where(*filters)
    )).scalar() or 0
    by_image: dict[str, set[str]] = {}
    for chunk in chunked(labels):
        res = await db.execute(
            select(Detection.image_id, Detection.label)
            .join(Image, Image.id == Detection.image_id)
            .where(*filters, Detection.label.in_(chunk))
            .distinct()
        )
        for r in res.all():
            by_image.setdefault(r.image_id, set()).add(r.label)
    df: dict[str, int] = {}
    for found in by_image.values():
        for label in found:
            df[label] = df.get(label, 0) + 1
    scored = {}
    for cid, found in by_image.items():
        ordered = sorted(found)
        scored[cid] = (sum(_idf(n, df[label]) for label in ordered), ordered)
    return _result("objects", await _hydrate(db, _rank(scored, limit)))


def _decode(blobs: list[bytes]) -> np.ndarray:
    return np.stack([np.frombuffer(b, np.float16).astype(np.float32) for b in blobs])


def _top_cosine(query: np.ndarray, ids: list[str], blobs: list[bytes], keep: list[tuple[float, str]], limit: int):
    """Merge one page's cosines into the running top-`limit`. Runs on the default pool."""
    scores = _decode(blobs) @ query
    merged = keep + [(float(s), i) for s, i in zip(scores, ids)]
    merged.sort(key=lambda t: (-t[0], t[1]))
    return merged[:limit]


async def by_clip(db: AsyncSession, image_id: str, dataset_id: str, limit: int) -> dict:
    blob = (await db.execute(
        select(Image.clip_embedding).where(Image.id == image_id)
    )).scalar_one_or_none()
    if not blob:
        return _result("clip", [], False, REASON_NO_CLIP)
    query = np.frombuffer(blob, np.float16).astype(np.float32)
    loop = asyncio.get_running_loop()
    keep: list[tuple[float, str]] = []
    result = await db.stream(_scan(
        select(Image.id, Image.clip_embedding).where(
            *_candidate_filters(dataset_id, image_id), Image.clip_embedding.isnot(None),
        )
    ))
    async for rows in result.partitions(PAGE_SIZE):
        # A vector of another width cannot be compared; skip it rather than fail.
        usable = [r for r in rows if r.clip_embedding and len(r.clip_embedding) == len(blob)]
        if usable:
            keep = await loop.run_in_executor(
                None, _top_cosine, query, [r.id for r in usable],
                [r.clip_embedding for r in usable], keep, limit,
            )
    ranked = [(i, s, []) for s, i in keep]
    return _result("clip", await _hydrate(db, ranked))


async def by_recent(db: AsyncSession, image_id: str, dataset_id: str, limit: int) -> dict:
    rows = (await db.execute(
        select(Image.id)
        .where(*_candidate_filters(dataset_id, image_id), Image.captioned_at.isnot(None))
        .order_by(Image.captioned_at.desc(), Image.id.desc())
        .limit(limit)
    )).all()
    return _result("recent", await _hydrate(db, [(r.id, 0.0, []) for r in rows]))


async def similar_captions(
    db: AsyncSession, image_id: str, dataset_id: str, caption: str, by: str, limit: int
) -> dict:
    if by == "tags":
        return await by_tags(db, image_id, dataset_id, caption, limit)
    if by == "objects":
        return await by_objects(db, image_id, dataset_id, limit)
    if by == "clip":
        return await by_clip(db, image_id, dataset_id, limit)
    return await by_recent(db, image_id, dataset_id, limit)
