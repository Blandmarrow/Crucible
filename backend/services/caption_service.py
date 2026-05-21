import re
from datetime import datetime

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Image, Tag
from backend.utils import ALLOWED_FLAG_KEYS


async def get_caption(db: AsyncSession, image_id: str) -> dict:
    img = await db.get(Image, image_id)
    if not img:
        return {}
    return {
        "image_id": image_id,
        "caption_text": img.caption_text,
        "tags": img.tags_json,
        "caption_style": img.caption_style,
        "captioned_by": img.captioned_by,
    }


async def set_caption(
    db: AsyncSession,
    image_id: str,
    caption_text: str,
    tags: list[str],
    caption_style: str = "",
    captioned_by: str = "manual",
) -> None:
    img = await db.get(Image, image_id)
    if not img:
        return

    tags = [t.strip() for t in tags if t.strip()]
    img.caption_text = caption_text
    img.tags_json = tags
    img.caption_style = caption_style
    img.captioned_by = captioned_by
    img.captioned_at = datetime.utcnow()

    await _sync_tags(db, img, tags, captioned_by)
    _write_txt_sidecar(img.file_path, caption_text or ", ".join(tags))
    await db.commit()


async def patch_tags(db: AsyncSession, image_id: str, add: list[str], remove: list[str]) -> list[str]:
    img = await db.get(Image, image_id)
    if not img:
        return []
    current = set(img.tags_json)
    current.update(t.strip() for t in add if t.strip())
    current -= set(remove)
    new_tags = list(current)
    img.tags_json = new_tags
    img.captioned_at = datetime.utcnow()
    await _sync_tags(db, img, new_tags, "manual")
    _write_txt_sidecar(img.file_path, img.caption_text or ", ".join(new_tags))
    await db.commit()
    return new_tags


async def batch_set_tags(
    db: AsyncSession,
    image_ids: list[str],
    tags: list[str],
    mode: str = "append",
) -> None:
    tags = [t.strip() for t in tags if t.strip()]
    result = await db.execute(select(Image).where(Image.id.in_(image_ids)))
    images = result.scalars().all()
    for img in images:
        if mode == "replace":
            new_tags = tags
        else:
            existing = set(img.tags_json)
            existing.update(tags)
            new_tags = list(existing)
        img.tags_json = new_tags
        img.captioned_at = datetime.utcnow()
        await _sync_tags(db, img, new_tags, "manual")
        _write_txt_sidecar(img.file_path, img.caption_text or ", ".join(new_tags))
    await db.commit()


async def batch_remove_tags(db: AsyncSession, image_ids: list[str], tags: list[str]) -> None:
    remove_set = set(tags)
    result = await db.execute(select(Image).where(Image.id.in_(image_ids)))
    images = result.scalars().all()
    for img in images:
        new_tags = [t for t in img.tags_json if t not in remove_set]
        img.tags_json = new_tags
        await _sync_tags(db, img, new_tags, "manual")
        _write_txt_sidecar(img.file_path, img.caption_text or ", ".join(new_tags))
    await db.commit()


async def bulk_edit_captions(
    db: AsyncSession,
    dataset_id: str,
    operation: str,
    text: str,
    replacement: str = "",
    use_regex: bool = False,
    image_ids: list[str] | None = None,
    quality_flags: list[str] | None = None,
) -> dict:
    query = select(Image).where(Image.dataset_id == dataset_id)
    if image_ids:
        query = query.where(Image.id.in_(image_ids))
    if quality_flags:
        valid_flags = [f for f in quality_flags if f in ALLOWED_FLAG_KEYS]
        if valid_flags:
            query = query.where(and_(*[Image.quality_flags[f].as_boolean().is_not(True) for f in valid_flags]))

    result = await db.execute(query)
    images = result.scalars().all()

    affected = 0
    skipped = 0

    for img in images:
        old_text = img.caption_text or ""

        try:
            if operation == "prepend":
                new_text = (text + " " + old_text).strip() if old_text else text
            elif operation == "append":
                new_text = (old_text + " " + text).strip() if old_text else text
            elif operation == "remove":
                if not old_text:
                    skipped += 1
                    continue
                new_text = re.sub(text, "", old_text) if use_regex else old_text.replace(text, "")
                new_text = " ".join(new_text.split())
                if new_text == old_text:
                    skipped += 1
                    continue
            elif operation == "find_replace":
                if not old_text:
                    skipped += 1
                    continue
                new_text = re.sub(text, replacement, old_text) if use_regex else old_text.replace(text, replacement)
                new_text = " ".join(new_text.split())
                if new_text == old_text:
                    skipped += 1
                    continue
            else:
                skipped += 1
                continue
        except re.error:
            skipped += 1
            continue

        img.caption_text = new_text
        img.captioned_at = datetime.utcnow()
        _write_txt_sidecar(img.file_path, new_text or ", ".join(img.tags_json))
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
    for img in images:
        old = img.caption_text
        if use_regex:
            new = re.sub(find, replace, old)
        else:
            new = old.replace(find, replace)
        if new != old:
            img.caption_text = new
            _write_txt_sidecar(img.file_path, new)
            updated += 1
    await db.commit()
    return updated


async def get_tag_stats(db: AsyncSession, dataset_id: str, subfolder: str | None = None) -> list[dict]:
    from sqlalchemy import func
    q = (
        select(Tag.tag, Tag.category, func.count(Tag.id).label("count"))
        .where(Tag.dataset_id == dataset_id)
        .group_by(Tag.tag, Tag.category)
        .order_by(func.count(Tag.id).desc())
        .limit(500)
    )
    if subfolder is not None:
        q = q.join(Image, Tag.image_id == Image.id).where(Image.subfolder == subfolder)
    result = await db.execute(q)
    return [{"tag": r.tag, "category": r.category, "count": r.count} for r in result.all()]


async def _sync_tags(db: AsyncSession, img: Image, tags: list[str], source: str) -> None:
    await db.execute(delete(Tag).where(Tag.image_id == img.id))
    for tag in tags:
        db.add(Tag(image_id=img.id, dataset_id=img.dataset_id, tag=tag, source=source))


def _write_txt_sidecar(image_path: str, text: str) -> None:
    from pathlib import Path
    txt_path = Path(image_path).with_suffix(".txt")
    txt_path.write_text(text, encoding="utf-8")
