import asyncio
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_REGEX_TIMEOUT = 30.0  # seconds; protects event loop from catastrophic backtracking

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Image
from backend.utils import ALLOWED_FLAG_KEYS, normalize_subfolder


async def get_caption(db: AsyncSession, image_id: str) -> dict:
    img = await db.get(Image, image_id)
    if not img:
        return {}
    return {
        "image_id": image_id,
        "caption_text": img.caption_text,
        "caption_style": img.caption_style,
        "captioned_by": img.captioned_by,
    }


async def set_caption(
    db: AsyncSession,
    image_id: str,
    caption_text: str,
    caption_style: str = "",
    captioned_by: str = "manual",
    has_ai_artifacts: bool | None = None,
) -> str | None:
    img = await db.get(Image, image_id)
    if not img:
        return None

    img.caption_text = caption_text
    img.caption_style = caption_style
    img.captioned_by = captioned_by
    img.captioned_at = datetime.utcnow()

    if has_ai_artifacts is not None:
        flags = dict(img.quality_flags or {})
        flags["has_ai_artifacts"] = has_ai_artifacts
        img.quality_flags = flags

    _write_txt_sidecar(img.file_path, caption_text)
    await db.commit()
    return img.dataset_id


async def bulk_edit_captions(
    db: AsyncSession,
    dataset_id: str,
    operation: str,
    text: str,
    replacement: str = "",
    use_regex: bool = False,
    image_ids: list[str] | None = None,
    quality_flags: list[str] | None = None,
    subfolder: str | None = None,
) -> dict:
    query = select(Image).where(Image.dataset_id == dataset_id)
    if image_ids is not None:
        query = query.where(Image.id.in_(image_ids))
    elif subfolder is not None:
        query = query.where(Image.subfolder == normalize_subfolder(subfolder))
    if quality_flags:
        valid_flags = [f for f in quality_flags if f in ALLOWED_FLAG_KEYS]
        if valid_flags:
            query = query.where(and_(*[Image.quality_flags[f].as_boolean().is_not(True) for f in valid_flags]))

    result = await db.execute(query)
    images = result.scalars().all()

    affected = 0
    skipped = 0

    # Regex remove/find_replace: run matching in a thread so catastrophic backtracking
    # doesn't block the event loop; asyncio.wait_for enforces a hard timeout.
    if use_regex and operation in ("remove", "find_replace"):
        try:
            compiled = re.compile(text)
        except re.error:
            return {"affected": 0, "skipped": len(images)}

        repl = "" if operation == "remove" else replacement
        items = [(img.id, img.caption_text or "") for img in images]

        def _apply_regex(batch: list[tuple[str, str]]) -> dict[str, str]:
            updates: dict[str, str] = {}
            for img_id, old_text in batch:
                if not old_text:
                    continue
                try:
                    new_text = " ".join(compiled.sub(repl, old_text).split())
                    if new_text != old_text:
                        updates[img_id] = new_text
                except re.error:
                    pass
            return updates

        loop = asyncio.get_running_loop()
        new_texts = await asyncio.wait_for(
            loop.run_in_executor(None, _apply_regex, items),
            timeout=_REGEX_TIMEOUT,
        )

        img_map = {img.id: img for img in images}
        for img_id, new_text in new_texts.items():
            img = img_map[img_id]
            img.caption_text = new_text
            img.captioned_at = datetime.utcnow()
            _write_txt_sidecar(img.file_path, new_text)
            affected += 1
        skipped = len(images) - affected
        await db.commit()
        return {"affected": affected, "skipped": skipped}

    for img in images:
        old_text = img.caption_text or ""

        if operation == "prepend":
            new_text = (text + " " + old_text).strip() if old_text else text
        elif operation == "append":
            new_text = (old_text + " " + text).strip() if old_text else text
        elif operation == "remove":
            if not old_text:
                skipped += 1
                continue
            new_text = old_text.replace(text, "")
            new_text = " ".join(new_text.split())
            if new_text == old_text:
                skipped += 1
                continue
        elif operation == "find_replace":
            if not old_text:
                skipped += 1
                continue
            new_text = old_text.replace(text, replacement)
            new_text = " ".join(new_text.split())
            if new_text == old_text:
                skipped += 1
                continue
        else:
            skipped += 1
            continue

        img.caption_text = new_text
        img.captioned_at = datetime.utcnow()
        _write_txt_sidecar(img.file_path, new_text)
        affected += 1

    await db.commit()
    return {"affected": affected, "skipped": skipped}


async def find_replace_captions(
    db: AsyncSession,
    dataset_id: str,
    find: str,
    replace: str,
    use_regex: bool = False,
    image_ids: list[str] | None = None,
) -> int:
    query = select(Image).where(Image.dataset_id == dataset_id)
    if image_ids:
        query = query.where(Image.id.in_(image_ids))
    result = await db.execute(query)
    images = result.scalars().all()
    updated = 0

    if use_regex:
        try:
            compiled = re.compile(find)
        except re.error:
            return 0

        items = [(img.id, img.caption_text or "") for img in images]

        def _apply_regex(batch: list[tuple[str, str]]) -> dict[str, str]:
            updates: dict[str, str] = {}
            for img_id, old_text in batch:
                try:
                    new_text = compiled.sub(replace, old_text)
                    if new_text != old_text:
                        updates[img_id] = new_text
                except re.error:
                    pass
            return updates

        loop = asyncio.get_running_loop()
        new_texts = await asyncio.wait_for(
            loop.run_in_executor(None, _apply_regex, items),
            timeout=_REGEX_TIMEOUT,
        )

        img_map = {img.id: img for img in images}
        for img_id, new_text in new_texts.items():
            img = img_map[img_id]
            img.caption_text = new_text
            _write_txt_sidecar(img.file_path, new_text)
            updated += 1
        await db.commit()
        return updated

    for img in images:
        old = img.caption_text
        new = old.replace(find, replace)
        if new != old:
            img.caption_text = new
            _write_txt_sidecar(img.file_path, new)
            updated += 1
    await db.commit()
    return updated


async def get_tag_stats(db: AsyncSession, dataset_id: str, subfolder: str | None = None) -> list[dict]:
    q = select(Image.caption_text).where(
        Image.dataset_id == dataset_id,
        Image.caption_text != "",
    )
    if subfolder is not None:
        q = q.where(Image.subfolder == subfolder)
    result = await db.stream(q)
    freq: dict[str, int] = {}
    async for (caption_text,) in result:
        for tag in caption_text.split(","):
            tag = tag.strip()
            if tag:
                freq[tag] = freq.get(tag, 0) + 1
    return [
        {"tag": tag, "count": count}
        for tag, count in sorted(freq.items(), key=lambda x: -x[1])[:500]
    ]


def _write_txt_sidecar(image_path: str, text: str) -> None:
    from pathlib import Path
    txt_path = Path(image_path).with_suffix(".txt")
    try:
        txt_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to write caption sidecar %s: %s", txt_path, exc)
        raise
