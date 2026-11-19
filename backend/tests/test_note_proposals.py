"""Note proposals (M12 phase 2d): the `crucible-notes` block parsed into changes the
user accepts, rejects and undoes, against the fake LLM server. See
docs/dev/analyst/proposals.md.
"""
from sqlalchemy import select

from backend.models import ChatProposal
from backend.models.notes import NoteItemHistory
from backend.services.note_proposals import MAX_PROPOSALS, parse, strip_blocks
from backend.tests.conftest import API, api_env, run
from backend.tests.fake_llm import NOTES_MALFORMED_TEXT, NOTES_TEXT, Reply
from backend.tests.test_chat_send import _send, _setup


# --- The parser ---------------------------------------------------------------


def test_the_parser_reads_well_formed_lines_forgivingly():
    text = (
        "Agreed.\n\n```Crucible-Notes\n"
        "- Add Open Items: re-tag the hands\n"
        "* edit preference #2: zxc comes first\n"
        "1. remove about-me 1.\n"
        "ADD DECISIONS   kept the 768px images\n"
        "add about me I train SDXL LoRAs\n"
        "```\nThanks."
    )
    ops = [(p.op, p.section, p.number, p.text) for p in parse(text)]
    assert ops == [
        ("add", "open_item", None, "re-tag the hands"),
        ("edit", "preference", 2, "zxc comes first"),
        ("remove", "about_me", 1, None),
        ("add", "decision", None, "kept the 768px images"),
        ("add", "about_me", None, "I train SDXL LoRAs"),
    ]
    assert strip_blocks(text) == "Agreed.\n\nThanks."


def test_the_parser_drops_what_it_cannot_read():
    # The fake's malformed corpus: a misspelt section, a word for a number, junk, an op
    # with no text, and no closing fence. Nothing in it is guessed at.
    assert parse(NOTES_MALFORMED_TEXT) == []
    junk = "```crucible-notes\nedit decision: no number\nremove preference\nrename decision 1: x\nadd\n```"
    assert parse(junk) == []
    # A fence with another language, or none, is not a notes block.
    assert parse("```\nadd decision: x\n```\n```python\nadd decision: y\n```") == []


def test_the_parser_reads_an_unclosed_block_and_caps_the_count():
    assert [p.text for p in parse("```crucible-notes\nadd decision: cut off here")] == ["cut off here"]
    many = "```crucible-notes\n" + "\n".join(f"add open_item: item {i}" for i in range(50)) + "\n```"
    assert len(parse(many)) == MAX_PROPOSALS


# --- Through the chat ---------------------------------------------------------


async def _notes(env, ds_id: str) -> list[dict]:
    return (await env.client.get(f"{API}/datasets/{ds_id}/notes", params={"include_deleted": True})).json()["items"]


async def _act(env, chat_id: str, proposal_id: int, action: str):
    return await env.client.post(f"{API}/chats/{chat_id}/proposals/{proposal_id}/{action}")


def test_a_notes_reply_stores_proposals_to_accept_reject_and_undo(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, _prov, chat_id = await _setup(env)
            r, events = await _send(env, chat_id, "[[fake:notes]] Note what we agreed")
            final = events[-1][1]
            assert final["content"] == NOTES_TEXT  # stored as the model wrote it
            props = final["proposals"]
            assert [(p["op"], p["section"], p["status"]) for p in props] == [
                ("add", "decision", "pending"), ("add", "open_item", "pending"),
            ]
            decision, open_item = props

            r = await _act(env, chat_id, decision["id"], "accept")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "accepted"
            (note,) = [n for n in await _notes(env, ds["id"]) if n["section"] == "decision"]
            assert note["text"] == "Dropped images under 768px; the LoRA trains at 1024."
            assert note["source_chat_id"] == chat_id and note["decided_at"] is not None
            async with env.Session() as db:
                (h,) = (await db.execute(select(NoteItemHistory))).scalars().all()
            assert (h.op, h.actor, h.chat_id) == ("add", "llm", chat_id)

            # Twice is refused, not applied twice.
            assert (await _act(env, chat_id, decision["id"], "accept")).status_code == 409

            assert (await _act(env, chat_id, open_item["id"], "reject")).json()["status"] == "rejected"
            assert not [n for n in await _notes(env, ds["id"]) if n["section"] == "open_item"]

            # Undo: the added note goes, and both are pending again.
            assert (await _act(env, chat_id, decision["id"], "undo")).json()["status"] == "pending"
            (note,) = [n for n in await _notes(env, ds["id"]) if n["section"] == "decision"]
            assert note["deleted_at"] is not None
            assert (await _act(env, chat_id, open_item["id"], "undo")).json()["status"] == "pending"

            # The chat shows the proposals with the stored reply.
            detail = (await env.client.get(f"{API}/chats/{chat_id}")).json()
            assert [p["status"] for p in detail["messages"][-1]["proposals"]] == ["pending", "pending"]
    run(scenario())


def test_edit_and_remove_resolve_numbers_through_the_replys_manifest(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, _prov, chat_id = await _setup(env)
            for text in ("Trigger word zxc", "Square crops only"):
                r = await env.client.post(f"{API}/datasets/{ds['id']}/notes", json={"section": "preference", "text": text})
                assert r.status_code == 201, r.text
            fake_llm.replies.append(Reply(text=(
                "Done.\n```crucible-notes\n"
                "edit preference 1: Trigger word zxc comes first\n"
                "remove preference 2\n"
                "remove preference 9\n"
                "```"
            )))
            _r, events = await _send(env, chat_id, "Tidy the preferences")
            edit, remove = events[-1][1]["proposals"]  # preference 9 was never shown: dropped
            assert (edit["op"], edit["target_text"], edit["new_text"]) == ("edit", "Trigger word zxc", "Trigger word zxc comes first")
            assert (remove["op"], remove["target_text"]) == ("remove", "Square crops only")

            # A reorder since does not move what "preference 2" meant to the reply.
            r = await env.client.patch(f"{API}/datasets/{ds['id']}/notes/{remove['target_item_id']}", json={"position": 0})
            assert r.status_code == 200

            assert (await _act(env, chat_id, edit["id"], "accept")).status_code == 200
            assert (await _act(env, chat_id, remove["id"], "accept")).status_code == 200
            live = {n["text"] for n in await _notes(env, ds["id"]) if n["deleted_at"] is None}
            assert live == {"Trigger word zxc comes first"}

            # Undo puts both back.
            assert (await _act(env, chat_id, edit["id"], "undo")).status_code == 200
            assert (await _act(env, chat_id, remove["id"], "undo")).status_code == 200
            live = {n["text"] for n in await _notes(env, ds["id"]) if n["deleted_at"] is None}
            assert live == {"Trigger word zxc", "Square crops only"}

            # An edit changed by hand after it was accepted is not undone over the top.
            assert (await _act(env, chat_id, edit["id"], "accept")).status_code == 200
            r = await env.client.patch(f"{API}/datasets/{ds['id']}/notes/{edit['target_item_id']}", json={"text": "My own words"})
            assert r.status_code == 200
            assert (await _act(env, chat_id, edit["id"], "undo")).status_code == 409
    run(scenario())


def test_a_proposal_whose_note_has_gone_is_stale(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, _prov, chat_id = await _setup(env)
            r = await env.client.post(f"{API}/datasets/{ds['id']}/notes", json={"section": "open_item", "text": "Re-tag"})
            item_id = r.json()["id"]
            fake_llm.replies.append(Reply(text="```crucible-notes\nremove open_item 1\n```"))
            _r, events = await _send(env, chat_id, "Clean up")
            (p,) = events[-1][1]["proposals"]
            await env.client.delete(f"{API}/datasets/{ds['id']}/notes/{item_id}")
            r = await _act(env, chat_id, p["id"], "accept")
            assert r.status_code == 409
            async with env.Session() as db:
                assert (await db.get(ChatProposal, p["id"])).status == "stale"
    run(scenario())


def test_a_proposal_past_the_cap_stays_pending(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, _prov, chat_id = await _setup(env)
            for _ in range(2):  # one note holds at most 2,000 characters; the cap is 4,000
                r = await env.client.post(f"{API}/datasets/{ds['id']}/notes", json={"section": "preference", "text": "x" * 1995})
                assert r.status_code == 201, r.text
            _r, events = await _send(env, chat_id, "[[fake:notes]] note it")
            p = events[-1][1]["proposals"][0]
            r = await _act(env, chat_id, p["id"], "accept")
            assert r.status_code == 422
            async with env.Session() as db:
                assert (await db.get(ChatProposal, p["id"])).status == "pending"
    run(scenario())


def test_about_me_goes_to_the_app_notes(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _prov, chat_id = await _setup(env)
            fake_llm.replies.append(Reply(text="```crucible-notes\nadd about_me: I train SDXL LoRAs\n```"))
            _r, events = await _send(env, chat_id, "Remember me")
            (p,) = events[-1][1]["proposals"]
            assert (await _act(env, chat_id, p["id"], "accept")).status_code == 200
            items = (await env.client.get(f"{API}/app-notes")).json()["items"]
            assert [(i["section"], i["text"], i["source_chat_id"]) for i in items] == [("about_me", "I train SDXL LoRAs", chat_id)]
    run(scenario())


def test_only_a_finished_reply_proposes(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _prov, chat_id = await _setup(env)
            # Cut off at max tokens: the block may be half written, so nothing is proposed.
            fake_llm.replies.append(Reply(text=NOTES_TEXT, finish_reason="length"))
            _r, events = await _send(env, chat_id, "Note it")
            final = events[-1][1]
            assert final["error_kind"] == "length" and final["proposals"] == []
    run(scenario())


def test_deleting_the_chat_keeps_its_notes(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, _prov, chat_id = await _setup(env)
            _r, events = await _send(env, chat_id, "[[fake:notes]] note it")
            p = events[-1][1]["proposals"][0]
            assert (await _act(env, chat_id, p["id"], "accept")).status_code == 200

            # Another chat cannot act on this chat's proposal.
            other = (await env.client.post(f"{API}/datasets/{ds['id']}/chats", json={})).json()["id"]
            assert (await _act(env, other, p["id"], "undo")).status_code == 404

            assert (await env.client.delete(f"{API}/chats/{chat_id}")).status_code == 204
            (note,) = [n for n in await _notes(env, ds["id"]) if n["deleted_at"] is None]
            assert note["source_chat_id"] is None
            async with env.Session() as db:
                assert [h.chat_id for h in (await db.execute(select(NoteItemHistory))).scalars().all()] == [None]
                assert (await db.execute(select(ChatProposal))).scalars().all() == []
    run(scenario())
