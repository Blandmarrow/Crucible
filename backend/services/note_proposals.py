"""Note proposals (M12 phase 2d): the `crucible-notes` block at the end of a reply,
parsed into changes the user accepts or rejects one by one. See
docs/dev/analyst/proposals.md.

The block is written by a model that may be small, so parsing is forgiving about
form and strict about meaning:

- **Forgiving**: case, a list marker before a line, a colon or none after the section,
  `#` before a number, plural or spaced section names, and a block whose closing fence
  never came (a reply cut off, or a model that forgot it).
- **Strict**: an unknown op or section, a number that is not digits, a number outside
  the numbering the reply's own context showed, or an op with no text is dropped. A
  line that does not parse is never guessed at.

Numbers are resolved against the reply's own `context_manifest.note_numbers`, the
numbering the model was shown, never against the notes as they are now. An item gone
by the time the user accepts makes the proposal `stale`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.chat import Chat, ChatMessage, ChatProposal
from backend.models.notes import AppNoteItem, DatasetNoteItem
from backend.services import notes_service

# The block: the fence's info string, then everything to the closing fence or the end.
BLOCK_RE = re.compile(r"```[ \t]*crucible-notes[ \t]*\r?\n(.*?)(?:\r?\n?```|\Z)", re.DOTALL | re.IGNORECASE)

# Keyed on the name lower-cased, with runs of spaces, `_` and `-` read as one space.
SECTION_ALIASES = {
    "preference": "preference", "preferences": "preference",
    "decision": "decision", "decisions": "decision",
    "open item": "open_item", "open items": "open_item",
    "about me": "about_me",
}
_SECTION = r"(?P<section>open[ _-]+items?|about[ _-]+me|preferences?|decisions?)"
_NUMBER = r"#?(?P<n>\d{1,4})"
_TEXT = r"\s*:?\s*(?P<text>.*\S)"
_LINE_RES = {
    "add": re.compile(rf"^add\s+{_SECTION}{_TEXT}$", re.IGNORECASE),
    "edit": re.compile(rf"^edit\s+{_SECTION}\s+{_NUMBER}{_TEXT}$", re.IGNORECASE),
    "remove": re.compile(rf"^remove\s+{_SECTION}\s+{_NUMBER}\s*\.?$", re.IGNORECASE),
}
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")

# One reply's block is a handful of changes, not a rewrite of the notes.
MAX_PROPOSALS = 20
MAX_TEXT_CHARS = 2000


@dataclass
class ParsedOp:
    op: str
    section: str
    number: int | None = None
    text: str | None = None


def strip_blocks(text: str) -> str:
    """The reply without its blocks. The frontend strips the same way for display; the
    stored reply keeps them, as the model wrote it."""
    return re.sub(r"\n{3,}", "\n\n", BLOCK_RE.sub("", text)).rstrip()


def parse(text: str) -> list[ParsedOp]:
    """Every well-formed line of every `crucible-notes` block, in order."""
    out: list[ParsedOp] = []
    for block in BLOCK_RE.findall(text):
        for raw in block.splitlines():
            line = _LIST_MARKER.sub("", raw).strip()
            if not line:
                continue
            for op, pattern in _LINE_RES.items():
                m = pattern.match(line)
                if not m:
                    continue
                section = SECTION_ALIASES[re.sub(r"[ _-]+", " ", m.group("section").lower())]
                groups = m.groupdict()
                number = int(groups["n"]) if groups.get("n") else None
                body = groups["text"].strip() if groups.get("text") else None
                if body is not None:
                    body = body[:MAX_TEXT_CHARS]
                out.append(ParsedOp(op=op, section=section, number=number, text=body))
                break
            if len(out) >= MAX_PROPOSALS:
                return out
    return out


async def store(db: AsyncSession, msg: ChatMessage) -> None:
    """Parse a finished reply's block into its pending proposals, resolving each number
    through the reply's own manifest. Lines whose numbers resolve to nothing are
    dropped. Does not commit: the caller stores them with the reply."""
    ops = parse(msg.content or "")
    if not ops:
        return
    numbering = ((msg.context_manifest or {}).get("note_numbers") or {})
    seq = 0
    for p in ops:
        target_id = target_text = None
        if p.op in ("edit", "remove"):
            ids = numbering.get(p.section) or []
            if not p.number or p.number > len(ids):
                continue
            target_id = ids[p.number - 1]
            model = AppNoteItem if p.section == "about_me" else DatasetNoteItem
            item = await db.get(model, target_id)
            target_text = item.text if item is not None else None
        msg.proposals.append(ChatProposal(
            seq=seq, op=p.op, section=p.section, target_item_id=target_id,
            target_text=target_text, new_text=p.text if p.op != "remove" else None,
        ))
        seq += 1


def _model(section: str):
    return AppNoteItem if section == "about_me" else DatasetNoteItem


async def _target(db: AsyncSession, chat: Chat, p: ChatProposal):
    """The live item an edit or removal points at, or None when it has gone."""
    item = await db.get(_model(p.section), p.target_item_id) if p.target_item_id else None
    if item is None or item.deleted_at is not None:
        return None
    if isinstance(item, DatasetNoteItem) and item.dataset_id != chat.dataset_id:
        return None
    return item


async def _stale(db: AsyncSession, p: ChatProposal) -> None:
    p.status = "stale"
    await db.commit()
    raise HTTPException(409, "That note has been removed or moved since the reply; this suggestion is out of date.")


async def accept(db: AsyncSession, chat: Chat, p: ChatProposal) -> ChatProposal:
    """Apply a pending proposal to the notes, as the analyst (`actor="llm"`) on the
    user's say-so, in one commit with its new status."""
    if p.status != "pending":
        raise HTTPException(409, f"This suggestion is already {p.status}.")
    model = _model(p.section)
    dataset_id = chat.dataset_id if model is DatasetNoteItem else None
    if p.op == "add":
        item = await notes_service.add_item(
            db, model, section=p.section, text=p.new_text or "", dataset_id=dataset_id,
            # A decision is dated when it is made, as the notes editor does.
            decided_at=datetime.utcnow() if p.section == "decision" else None,
            actor="llm", chat_id=chat.id, commit=False,
        )
        p.applied_item_id, p.applied_before = item.id, None
    else:
        item = await _target(db, chat, p)
        if item is None:
            await _stale(db, p)
        p.applied_item_id, p.applied_before = item.id, item.text
        if p.op == "edit":
            await notes_service.edit_item(db, item, text=p.new_text, actor="llm", chat_id=chat.id, commit=False)
        else:
            await notes_service.remove_item(db, item, actor="llm", chat_id=chat.id, commit=False)
    p.status = "accepted"
    await db.commit()
    return p


async def reject(db: AsyncSession, p: ChatProposal) -> ChatProposal:
    if p.status != "pending":
        raise HTTPException(409, f"This suggestion is already {p.status}.")
    p.status = "rejected"
    await db.commit()
    return p


async def undo(db: AsyncSession, chat: Chat, p: ChatProposal) -> ChatProposal:
    """Back to pending. An accepted proposal's change is reversed first, as the user:
    an add is removed, an edit put back, a removal restored. A note changed by hand
    since is left alone and the undo refused."""
    if p.status == "rejected":
        p.status = "pending"
        await db.commit()
        return p
    if p.status != "accepted":
        raise HTTPException(409, "Only an accepted or rejected suggestion can be undone.")
    item = await db.get(_model(p.section), p.applied_item_id) if p.applied_item_id else None
    if p.op == "add":
        if item is not None:
            await notes_service.remove_item(db, item, chat_id=chat.id, commit=False)
    elif p.op == "edit":
        if item is None or item.deleted_at is not None or item.text != (p.new_text or "").strip():
            raise HTTPException(409, "That note has changed since; edit it by hand instead.")
        await notes_service.edit_item(db, item, text=p.applied_before, chat_id=chat.id, commit=False)
    else:  # remove
        if item is None:
            raise HTTPException(409, "That note no longer exists.")
        if item.deleted_at is not None:
            await notes_service.restore_item(db, item, chat_id=chat.id, commit=False)
    p.status = "pending"
    p.applied_item_id = p.applied_before = None
    await db.commit()
    return p
