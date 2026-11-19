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
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.chat import Chat, ChatMessage, ChatProposal
from backend.models.notes import AppNoteItem, DatasetNoteItem
from backend.services import notes_service

# The block: the fence's info string, then everything to a closing fence at the start of
# a line, or the end. A ``` inside a line does not close it.
BLOCK_RE = re.compile(
    r"```[ \t]*crucible-notes[ \t]*\r?\n(.*?)(?:^[ \t]*```|\Z)",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

# Keyed on the name lower-cased, with runs of spaces, `_` and `-` read as one space.
SECTION_ALIASES = {
    "preference": "preference", "preferences": "preference",
    "decision": "decision", "decisions": "decision",
    "open item": "open_item", "open items": "open_item",
    "about me": "about_me",
}
# The section ends at a word boundary ("preferenceful" is not a section), the number is
# all of its digits ("23" is never item 2 with text "3"), and the text needs a separator
# before it and cannot be a bare colon.
_SECTION = r"(?P<section>open[ _-]+items?|about[ _-]+me|preferences?|decisions?)\b"
_NUMBER = r"#?(?P<n>\d{1,4})(?!\d)"
_TEXT = r"(?:\s*:\s*|\s+)(?P<text>[^\s:](?:.*\S)?)"
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
    return re.sub(r"\n{3,}", "\n\n", BLOCK_RE.sub("", text)).strip()


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
    """The item an edit or removal points at, if it is live, in this chat's dataset, and
    still reads as the reply saw it. Otherwise None: the suggestion is out of date, and
    accepting it would overwrite or remove something the user has not reviewed."""
    item = await db.get(_model(p.section), p.target_item_id) if p.target_item_id else None
    if item is None or item.deleted_at is not None:
        return None
    if isinstance(item, DatasetNoteItem) and item.dataset_id != chat.dataset_id:
        return None
    if item.text != p.target_text:
        return None
    return item


async def _claim(db: AsyncSession, p: ChatProposal, from_status: str, to_status: str) -> None:
    """Move the proposal's status with a conditional UPDATE, so two requests at once (two
    panes, a retry) cannot both act on it: SQLite's write lock serialises them, and the
    second finds the status already moved. Its change follows in the same transaction."""
    res = await db.execute(
        update(ChatProposal)
        .where(ChatProposal.id == p.id, ChatProposal.status == from_status)
        .values(status=to_status, updated_at=datetime.utcnow())
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "This suggestion has changed since; reload the chat.")
    p.status = to_status


async def _abandon(db: AsyncSession, pid: int, *, stale: bool, error: Exception) -> None:
    """Undo the claim and the half-made change, then re-raise. A rollback expires the
    session's objects, so nothing here reads the proposal's attributes again."""
    await db.rollback()
    if stale:
        await db.execute(
            update(ChatProposal).where(ChatProposal.id == pid, ChatProposal.status == "pending")
            .values(status="stale", updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    raise error


class _Stale(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "That note has been removed or changed since the reply; this suggestion is out of date.")


async def accept(db: AsyncSession, chat: Chat, p: ChatProposal) -> ChatProposal:
    """Apply a pending proposal to the notes, as the analyst (`actor="llm"`) on the
    user's say-so, in one commit with its new status. A refusal (the cap, a note gone or
    changed since) leaves it pending, or `stale` for the second."""
    pid = p.id
    await _claim(db, p, "pending", "accepted")
    model = _model(p.section)
    dataset_id = chat.dataset_id if model is DatasetNoteItem else None
    try:
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
                await _abandon(db, pid, stale=True, error=_Stale())
            p.applied_item_id, p.applied_before = item.id, item.text
            if p.op == "edit":
                await notes_service.edit_item(db, item, text=p.new_text, actor="llm", chat_id=chat.id, commit=False)
            else:
                await notes_service.remove_item(db, item, actor="llm", chat_id=chat.id, commit=False)
    except HTTPException as e:
        if isinstance(e, _Stale):
            raise
        await _abandon(db, pid, stale=False, error=e)
    await db.commit()
    return p


async def reject(db: AsyncSession, p: ChatProposal) -> ChatProposal:
    await _claim(db, p, "pending", "rejected")
    await db.commit()
    return p


async def undo(db: AsyncSession, chat: Chat, p: ChatProposal) -> ChatProposal:
    """Back to pending. An accepted proposal's change is reversed first, as the user:
    an add is removed, an edit put back, a removal restored. A note changed by hand
    since is left alone and the undo refused."""
    pid, was = p.id, p.status
    if was not in ("accepted", "rejected"):
        raise HTTPException(409, "Only an accepted or rejected suggestion can be undone.")
    op, section, applied_id = p.op, p.section, p.applied_item_id
    new_text, before = (p.new_text or "").strip(), p.applied_before
    await _claim(db, p, was, "pending")
    if was == "accepted":
        try:
            item = await db.get(_model(section), applied_id) if applied_id else None
            if op == "add":
                if item is not None and item.deleted_at is None:
                    if item.text != new_text:
                        raise HTTPException(409, "That note has been edited since; remove it by hand instead.")
                    await notes_service.remove_item(db, item, chat_id=chat.id, commit=False)
            elif op == "edit":
                if item is None or item.deleted_at is not None or item.text != new_text:
                    raise HTTPException(409, "That note has changed since; edit it by hand instead.")
                await notes_service.edit_item(db, item, text=before, chat_id=chat.id, commit=False)
            else:  # remove
                if item is None:
                    raise HTTPException(409, "That note no longer exists.")
                if item.deleted_at is not None:
                    await notes_service.restore_item(db, item, chat_id=chat.id, commit=False)
        except HTTPException as e:
            await _abandon(db, pid, stale=False, error=e)
    p.applied_item_id = p.applied_before = None
    await db.commit()
    return p
