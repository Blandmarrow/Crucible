"""Dataset-wide semantic tag consolidation.

Two phases:
  * ``analyze`` — embed the unique-tag vocabulary, cluster by cosine similarity, and
    propose a canonical term per cluster (longest tag; ties broken by frequency).
  * ``apply`` — rewrite every caption with a confirmed ``{variant -> canonical}`` map,
    replacing whole tags only (never substrings) and collapsing duplicates.

See docs/dev/tag-consolidation.md.
"""
import asyncio
import logging
import time
from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ml import tag_embedder
from backend.ml.model_manager import model_manager
from backend.models import Image
from backend.services.caption_service import _write_txt_sidecar
from backend.utils import normalize_subfolder, subsume_tags
from backend.workers.progress import broadcaster

logger = logging.getLogger(__name__)


def _parse_tags(caption_text: str) -> list[str]:
    return [t.strip() for t in caption_text.split(",") if t.strip()]


async def _build_vocab(db: AsyncSession, dataset_id: str, subfolder: str | None) -> tuple[dict[str, int], int]:
    """Return ({tag: frequency}, image_count) for tag-style captions in scope."""
    q = select(Image.caption_text).where(
        Image.dataset_id == dataset_id,
        Image.caption_text != "",
    )
    if subfolder is not None:
        q = q.where(Image.subfolder == normalize_subfolder(subfolder))
    freq: dict[str, int] = {}
    image_count = 0
    result = await db.stream(q)
    async for (caption_text,) in result:
        image_count += 1
        for tag in _parse_tags(caption_text):
            freq[tag] = freq.get(tag, 0) + 1
    return freq, image_count


async def analyze(
    db: AsyncSession,
    dataset_id: str,
    threshold: float,
    subfolder: str | None,
    job_id: str,
) -> dict:
    freq, image_count = await _build_vocab(db, dataset_id, subfolder)

    # Most-frequent first, truncated to bound the n^2 similarity matrix.
    vocab = sorted(freq.keys(), key=lambda t: (-freq[t], t))
    truncated = len(vocab) > tag_embedder.MAX_VOCAB
    vocab = vocab[: tag_embedder.MAX_VOCAB]

    await broadcaster.emit(job_id, {
        "type": "progress", "job_id": job_id, "status": "running",
        "done": 0, "total": image_count, "percent": 0.0,
        "message": f"Embedding {len(vocab)} unique tags...",
    })

    if len(vocab) < 2:
        return {"clusters": [], "vocab_size": len(vocab), "image_count": image_count, "truncated": truncated}

    entry = await model_manager.load_tag_embedder(job_id=job_id, loop=asyncio.get_running_loop(), dataset_id=dataset_id)
    entry.in_use = True
    try:
        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(None, tag_embedder.embed_texts_sync, vocab, entry)
        await broadcaster.emit(job_id, {
            "type": "progress", "job_id": job_id, "status": "running",
            "done": 0, "total": image_count, "percent": 50.0,
            "message": f"Clustering tags at similarity ≥ {threshold:.2f}...",
        })
        index_clusters = await loop.run_in_executor(
            None, tag_embedder.cluster_texts_sync, vocab, embeddings, threshold
        )
    finally:
        entry.in_use = False
        entry.last_used = time.time()

    clusters = []
    for idxs in index_clusters:
        members = [vocab[i] for i in idxs]
        # canonical = longest tag; ties broken by highest frequency
        canonical = max(members, key=lambda t: (len(t), freq.get(t, 0)))
        variants = sorted(members, key=lambda t: (-freq.get(t, 0), t))
        # min pairwise cosine similarity within the cluster (embeddings are L2-normalised,
        # so cosine = dot product) — lets the UI surface the shakiest clusters for review.
        sub = embeddings[idxs]
        sims = sub @ sub.T
        iu = np.triu_indices(len(idxs), 1)
        min_sim = float(sims[iu].min())
        clusters.append({
            "canonical": canonical,
            "variants": [{"tag": t, "count": freq.get(t, 0)} for t in variants],
            "min_sim": round(min_sim, 3),
        })
    # Largest clusters first
    clusters.sort(key=lambda c: -len(c["variants"]))

    return {
        "clusters": clusters,
        "vocab_size": len(freq),
        "image_count": image_count,
        "truncated": truncated,
    }


async def apply(
    db: AsyncSession,
    dataset_id: str,
    mapping: dict[str, str],
    subfolder: str | None,
    job_id: str,
) -> dict:
    """Rewrite captions using a {variant -> canonical} whole-tag map."""
    # Drop identity mappings; nothing to do for tags that map to themselves.
    mapping = {k: v for k, v in mapping.items() if k != v}

    query = select(Image).where(
        Image.dataset_id == dataset_id,
        Image.caption_text != "",
    )
    if subfolder is not None:
        query = query.where(Image.subfolder == normalize_subfolder(subfolder))
    result = await db.execute(query)
    images = result.scalars().all()

    from backend.workers.job_queue import job_queue

    total = len(images)
    affected = 0
    skipped = 0
    cancelled = False
    for i, img in enumerate(images):
        if job_queue.cancel_requested(job_id):
            cancelled = True
            break
        old_text = img.caption_text or ""
        tags = _parse_tags(old_text)
        seen: set[str] = set()
        new_tags: list[str] = []
        for t in tags:
            mapped = mapping.get(t, t)
            if mapped not in seen:
                seen.add(mapped)
                new_tags.append(mapped)
        new_text = ", ".join(new_tags)
        if new_text == old_text:
            skipped += 1
            continue
        img.caption_text = new_text
        img.captioned_at = datetime.utcnow()
        _write_txt_sidecar(img.file_path, new_text)
        affected += 1
        if total and i % 50 == 0:
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "status": "running",
                "done": i + 1, "total": total, "percent": round((i + 1) / total * 100, 1),
                "message": f"Rewriting captions: {i + 1}/{total}",
            })

    await db.commit()
    if cancelled:
        job_queue.raise_if_cancelled(job_id)
    return {"affected": affected, "skipped": skipped}


async def subsume(
    db: AsyncSession,
    dataset_id: str,
    subfolder: str | None,
    dry_run: bool,
    image_ids: list[str] | None = None,
) -> dict:
    """Per-caption subsumption cleanup (drop tags contained whole-word in a longer tag).

    Deterministic, no model. When ``dry_run`` is True nothing is written — only the count
    of captions that *would* change is returned, for the page's preview. ``image_ids``
    (selection / single image) takes precedence over ``subfolder``.
    """
    query = select(Image).where(
        Image.dataset_id == dataset_id,
        Image.caption_text != "",
    )
    if image_ids is not None:
        query = query.where(Image.id.in_(image_ids))
    elif subfolder is not None:
        query = query.where(Image.subfolder == normalize_subfolder(subfolder))
    result = await db.execute(query)
    images = result.scalars().all()

    affected = 0
    skipped = 0
    for img in images:
        old_text = img.caption_text or ""
        tags = _parse_tags(old_text)
        new_text = ", ".join(subsume_tags(tags))
        if new_text == old_text:
            skipped += 1
            continue
        affected += 1
        if not dry_run:
            img.caption_text = new_text
            img.captioned_at = datetime.utcnow()
            _write_txt_sidecar(img.file_path, new_text)

    if not dry_run:
        await db.commit()
    return {"affected": affected, "skipped": skipped}
