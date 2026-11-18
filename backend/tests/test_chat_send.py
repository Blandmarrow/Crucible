"""Sending an analyst chat message and streaming its reply (M12 phase 2b), against the
fake LLM server: the stream, usage, errors and time limits, budgets, cancel, the
startup sweep, a dataset delete mid-reply, and the provider limit shared with captioning.
"""
import asyncio
import json
import logging

from sqlalchemy import select

from backend.models import ChatMessage, Image, LlmUsage
from backend.services import chat_runner, llm_limiter
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.tests.fake_llm import FAKE_REPLY, LENGTH_TEXT, Reply

SECRET = "sk-test-SECRET-key-1234"


def _events(body: str) -> list[tuple[str, dict]]:
    """An SSE body as (event, data) pairs."""
    out = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event:
            out.append((event, data))
    return out


async def _setup(env, *, billed: bool | None = None, max_tokens: int = 256, max_concurrent: int = 1,
                 default_model: str = "fake-chat", model: str = "") -> tuple[dict, dict, str]:
    """A dataset with one uncaptioned image, a provider, and a chat on it."""
    ds = await env.create_dataset("send")
    await upload_image(env, ds["id"], name="a.png", data=png_bytes((120, 30, 30)))
    body = {"name": f"p{billed}", "base_url": "http://localhost:9/v1", "api_key": SECRET,
            "default_model": default_model, "max_tokens": max_tokens, "max_concurrent": max_concurrent}
    if billed is not None:
        body["billed"] = billed
    r = await env.client.post(f"{API}/providers/", json=body)
    assert r.status_code == 201, r.text
    prov = r.json()
    r = await env.client.post(f"{API}/datasets/{ds['id']}/chats", json={"provider_id": prov["id"], "model": model})
    return ds, prov, r.json()["id"]


async def _send(env, chat_id: str, content: str):
    r = await env.client.post(f"{API}/chats/{chat_id}/messages", json={"content": content})
    return r, (_events(r.text) if r.status_code == 200 else [])


async def _usage(env) -> list[LlmUsage]:
    async with env.Session() as db:
        return list((await db.execute(select(LlmUsage).order_by(LlmUsage.id))).scalars().all())


def test_a_reply_streams_and_is_stored(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, prov, chat_id = await _setup(env)
            r, events = await _send(env, chat_id, "What should I fix first?")
            assert r.status_code == 200, r.text
            names = [e for e, _ in events]
            assert names[0] == "start" and names[-1] == "done" and "usage" in names
            assert "".join(d["text"] for e, d in events if e == "delta") == FAKE_REPLY
            final = events[-1][1]
            assert (final["status"], final["content"], final["model"]) == ("complete", FAKE_REPLY, "fake-chat")
            assert final["tokens_in"] > 0 and final["tokens_out"] > 0 and final["tokens_estimated"] is False
            # The dataset has open findings, so Crucible fills the strip itself.
            assert final["related_findings"]

            req = fake_llm.requests[-1]["json"]
            assert req["stream"] is True and req["stream_options"] == {"include_usage": True}
            assert req["model"] == "fake-chat" and req["max_tokens"] == 256
            assert [m["role"] for m in req["messages"]] == ["system", "user"]
            assert req["messages"][-1]["content"] == "What should I fix first?"

            detail = (await env.client.get(f"{API}/chats/{chat_id}")).json()
            assert detail["title"] == "What should I fix first?"
            assert [(m["role"], m["status"]) for m in detail["messages"]] == [("user", "complete"), ("assistant", "complete")]
            # A free local provider: the reply's manifest records what its context sent.
            assert detail["messages"][1]["context_manifest"]["categories"]["identity"]["sent"] is True
            (usage,) = await _usage(env)
            assert (usage.chat_id, usage.provider_id, usage.billed, usage.estimated) == (chat_id, prov["id"], False, False)
            assert usage.day == chat_runner.local_day()

            # The next message carries the history.
            await _send(env, chat_id, "And then?")
            roles = [m["role"] for m in fake_llm.requests[-1]["json"]["messages"]]
            assert roles == ["system", "user", "assistant", "user"]

    run(scenario())


def test_usage_counts_hidden_reasoning_as_output():
    from types import SimpleNamespace as U

    from backend.ml.chat_stream import usage_tokens

    # Gemini 2.5 Flash, measured: thinking is in the total, not in completion_tokens.
    assert usage_tokens(U(prompt_tokens=16, completion_tokens=57, total_tokens=833)) == (16, 817)
    # OpenAI counts reasoning inside completion_tokens: the two agree.
    assert usage_tokens(U(prompt_tokens=16, completion_tokens=57, total_tokens=73)) == (16, 57)
    # A server without a total keeps its completion count.
    assert usage_tokens(U(prompt_tokens=16, completion_tokens=57, total_tokens=None)) == (16, 57)
    assert usage_tokens(U()) == (None, None)


def test_finding_references_and_the_related_strip(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            fake_llm.replies.append(Reply(text="See [[finding:uncaptioned]] and [[finding:made_up]]."))
            _r, events = await _send(env, chat_id, "Hi")
            related = events[-1][1]["related_findings"]
            assert related[0] == "uncaptioned" and "made_up" not in related
            assert 1 <= len(related) <= chat_runner.RELATED_MAX

    run(scenario())


def test_no_usage_is_estimated_and_length_is_marked(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            _r, events = await _send(env, chat_id, "[[fake:no-usage]]")
            final = events[-1][1]
            assert final["status"] == "complete" and final["tokens_estimated"] is True
            assert final["tokens_out"] == chat_runner.estimate_tokens(len(FAKE_REPLY))
            assert (await _usage(env))[-1].estimated is True

            _r, events = await _send(env, chat_id, "[[fake:length]]")
            final = events[-1][1]
            assert (final["status"], final["error_kind"], final["content"]) == ("complete", "length", LENGTH_TEXT)
            assert "Max tokens" in final["error_detail"]

    run(scenario())


def test_errors_are_sorted_scrubbed_and_unbilled_when_refused(fake_llm, tmp_path, caplog):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            r, events = await _send(env, chat_id, "[[fake:429]]")
            assert r.status_code == 200 and events[-1][0] == "error"
            assert (events[-1][1]["status"], events[-1][1]["error_kind"]) == ("error", "transient")
            # Refused before any text: nothing was used, nothing recorded.
            assert await _usage(env) == []

            fake_llm.replies.append(Reply(status=401, error={"error": {"message": f"bad key {SECRET}"}}))
            r, events = await _send(env, chat_id, "again")
            final = events[-1][1]
            assert final["error_kind"] == "fatal"
            assert SECRET not in r.text and "***" in final["error_detail"]
            async with env.Session() as db:
                for m in (await db.execute(select(ChatMessage))).scalars():
                    assert SECRET not in (m.content + (m.error_detail or ""))

    with caplog.at_level(logging.DEBUG):
        run(scenario())
    # Crucible's own log lines. aiosqlite's DEBUG trace echoes every bound parameter,
    # the test's own provider INSERT included, so it is left out.
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("backend")]
    assert ours and not any(SECRET in line for line in ours)


def test_a_stalled_stream_times_out_keeping_its_text_and_usage(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr(chat_runner, "IDLE_S", 0.3)

    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            _r, events = await _send(env, chat_id, "[[fake:stall]]")
            final = events[-1][1]
            assert (final["status"], final["error_kind"]) == ("error", "timeout")
            assert final["content"] == FAKE_REPLY[:16]  # two 8-character chunks arrived
            (usage,) = await _usage(env)
            assert usage.estimated is True and usage.tokens_out > 0

    run(scenario())


def test_refusals_store_nothing(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, prov, chat_id = await _setup(env, default_model="")
            r, _ = await _send(env, chat_id, "Hi")
            assert r.status_code == 422 and "model" in r.json()["detail"]
            bare = (await env.client.post(f"{API}/datasets/{ds['id']}/chats", json={})).json()
            r, _ = await _send(env, bare["id"], "Hi")
            assert r.status_code == 422 and "provider" in r.json()["detail"]
            r = await env.client.post(f"{API}/chats/{chat_id}/messages", json={"content": "   "})
            assert r.status_code == 422
            async with env.Session() as db:
                assert (await db.execute(select(ChatMessage))).first() is None

    run(scenario())


def test_budgets_refuse_count_failures_and_skip_free_providers(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr(chat_runner, "IDLE_S", 0.3)

    async def scenario():
        async with api_env(tmp_path) as env:
            await env.client.patch(f"{API}/settings/thresholds", json={
                "analyst_chat_budget_tokens": 1000, "analyst_daily_budget_tokens": 1000,
            })
            # Free: a reply that could use far more than the budget still goes.
            _ds, _p, free_chat = await _setup(env, max_tokens=5000)
            r, events = await _send(env, free_chat, "Hi")
            assert r.status_code == 200 and events[-1][1]["status"] == "complete"

            # Billed: the estimate (context + a full reply) passes the budget.
            ds2 = await env.create_dataset("billed")
            paid = (await env.client.post(f"{API}/providers/", json={
                "name": "paid", "base_url": "http://localhost:9/v1", "default_model": "fake-chat",
                "max_tokens": 5000, "billed": True,
            })).json()
            chat = (await env.client.post(f"{API}/datasets/{ds2['id']}/chats", json={"provider_id": paid["id"]})).json()
            r, _ = await _send(env, chat["id"], "Hi")
            assert r.status_code == 409
            assert "1,000" in r.json()["detail"] and "Settings → Analyst" in r.json()["detail"]
            preview = (await env.client.post(f"{API}/chats/{chat['id']}/preview-context", json={"draft": "Hi"})).json()
            assert preview["budget"]["billed"] is True and preview["budget"]["would_exceed"] is True
            async with env.Session() as db:
                assert (await db.execute(select(ChatMessage).where(ChatMessage.chat_id == chat["id"]))).first() is None

            # A failed billed call still counts against the budget.
            await env.client.patch(f"{API}/settings/thresholds", json={
                "analyst_chat_budget_tokens": 100_000, "analyst_daily_budget_tokens": 100_000,
            })
            _r, events = await _send(env, chat["id"], "[[fake:stall]]")
            assert events[-1][1]["error_kind"] == "timeout"
            preview = (await env.client.post(f"{API}/chats/{chat['id']}/preview-context", json={})).json()
            assert preview["budget"]["chat_used"] > 0 and preview["budget"]["day_used"] > 0
            # The free chat's usage is not billed and counts toward nothing.
            assert preview["budget"]["day_used"] == preview["budget"]["chat_used"]

    run(scenario())


def _slow_reply() -> Reply:
    return Reply(text="word " * 200, chunk_size=5, chunk_delay=0.02)


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.02)


def test_cancel_keeps_the_partial_reply(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            fake_llm.replies.append(_slow_reply())
            sending = asyncio.create_task(_send(env, chat_id, "Talk at length"))
            await _until(lambda: (r := chat_runner.active_for_chat(chat_id)) is not None and len(r.text) > 20)
            run_ = chat_runner.active_for_chat(chat_id)

            # A second send while one is running is refused.
            r, _ = await _send(env, chat_id, "Me too")
            assert r.status_code == 409

            r = await env.client.post(f"{API}/chats/{chat_id}/messages/{run_.message_id}/cancel")
            assert r.status_code == 200
            got = r.json()
            assert got["status"] == "cancelled" and 20 < len(got["content"]) < len("word " * 200)
            _resp, events = await sending
            assert events[-1] == ("done", got)
            assert len(await _usage(env)) == 1

            # Cancelling again is a no-op that returns the reply as it is.
            r = await env.client.post(f"{API}/chats/{chat_id}/messages/{run_.message_id}/cancel")
            assert r.json()["status"] == "cancelled"
            # And the stream of an ended reply replays its start and end.
            r = await env.client.get(f"{API}/chats/{chat_id}/messages/{run_.message_id}/stream")
            assert [e for e, _ in _events(r.text)] == ["start", "done"]

    run(scenario())


async def _start_direct(env, chat_id: str, content: str = "Go"):
    """Start a send without listening to it, as a closed tab would leave it."""
    from backend.models import Chat

    async with env.Session() as db:
        return await chat_runner.start(db, await db.get(Chat, chat_id), content)


def test_the_reply_finishes_with_nobody_listening(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr(chat_runner, "FLUSH_EVERY_S", 0.05)

    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            fake_llm.replies.append(_slow_reply())
            run_ = await _start_direct(env, chat_id)
            # Flushed while it streams: a reload mid-reply sees the text so far.
            await _until(lambda: len(run_.text) > 50)
            await asyncio.sleep(0.2)
            async with env.Session() as db:
                partial = await db.get(ChatMessage, run_.message_id)
                assert partial.status == "partial" and partial.content
            await run_.task
            assert run_.listeners == set()  # nobody listened, start to end
            async with env.Session() as db:
                done = await db.get(ChatMessage, run_.message_id)
                assert (done.status, done.content) == ("complete", "word " * 200)

    run(scenario())


def test_a_late_listener_resumes_mid_reply(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            fake_llm.replies.append(_slow_reply())
            run_ = await _start_direct(env, chat_id)
            await _until(lambda: len(run_.text) > 50)
            r = await env.client.get(f"{API}/chats/{chat_id}/messages/{run_.message_id}/stream")
            events = _events(r.text)
            # The text so far in `start`, then the rest live: together, the whole reply.
            assert events[0][0] == "start" and events[0][1]["content"]
            rest = "".join(d["text"] for e, d in events if e == "delta")
            assert events[0][1]["content"] + rest == "word " * 200
            assert events[-1][1]["content"] == "word " * 200

    run(scenario())


def test_two_sends_at_once_one_is_refused(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            fake_llm.replies.append(_slow_reply())
            fake_llm.replies.append(_slow_reply())
            (r1, _e1), (r2, _e2) = await asyncio.gather(_send(env, chat_id, "One"), _send(env, chat_id, "Two"))
            assert sorted([r1.status_code, r2.status_code]) == [200, 409]
            async with env.Session() as db:
                users = (await db.execute(select(ChatMessage).where(ChatMessage.role == "user"))).scalars().all()
                assert len(users) == 1

    run(scenario())


def test_a_stop_while_the_reply_is_stored_waits_for_it(fake_llm, tmp_path, monkeypatch):
    async def slow_related(db, dataset_id, text):
        await asyncio.sleep(0.4)
        return []

    monkeypatch.setattr(chat_runner, "_related", slow_related)

    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            run_ = await _start_direct(env, chat_id, "Hi")
            await _until(lambda: run_.ending)
            r = await env.client.post(f"{API}/chats/{chat_id}/messages/{run_.message_id}/cancel")
            # The call had already ended: the reply is stored whole, never left partial.
            assert (r.json()["status"], r.json()["content"]) == ("complete", FAKE_REPLY)
            assert run_.final is not None and run_.final[0] == "done"

    run(scenario())


def test_a_failed_store_still_ends_the_reply(fake_llm, tmp_path, monkeypatch):
    async def broken_finish(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(chat_runner, "_finish", broken_finish)

    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            r, events = await _send(env, chat_id, "Hi")
            assert events[-1][0] == "error" and events[-1][1]["error_kind"] == "fatal"
            async with env.Session() as db:
                row = (await db.execute(select(ChatMessage).where(ChatMessage.role == "assistant"))).scalar_one()
                assert (row.status, row.content) == ("error", FAKE_REPLY)

    run(scenario())


class _HangingClient:
    """An `AsyncOpenAI` stand-in whose request goes out and never answers."""

    class _Completions:
        async def create(self, **_kwargs):
            await asyncio.sleep(3600)

    def __init__(self):
        self.chat = type("_Chat", (), {"completions": self._Completions()})()

    async def close(self):
        pass


def test_a_billed_request_cut_short_before_any_answer_is_recorded(tmp_path, monkeypatch):
    from backend.ml import openai_compat_captioner

    monkeypatch.setattr(openai_compat_captioner, "make_client", lambda *a, **k: _HangingClient())
    monkeypatch.setattr(chat_runner, "FIRST_TOKEN_S", 0.2)

    async def scenario():
        async with api_env(tmp_path) as env:
            # Free: a request that never answered used nothing worth recording.
            _ds, _p, free_chat = await _setup(env)
            _r, events = await _send(env, free_chat, "Hi")
            assert events[-1][1]["error_kind"] == "timeout"
            assert await _usage(env) == []

            # Billed: it was sent and may have been billed, so it is recorded as an estimate.
            ds2 = await env.create_dataset("paid")
            paid = (await env.client.post(f"{API}/providers/", json={
                "name": "paid", "base_url": "http://localhost:9/v1", "default_model": "m", "billed": True,
            })).json()
            chat = (await env.client.post(f"{API}/datasets/{ds2['id']}/chats", json={"provider_id": paid["id"]})).json()
            _r, events = await _send(env, chat["id"], "Hi")
            assert events[-1][1]["error_kind"] == "timeout"
            (usage,) = await _usage(env)
            assert (usage.billed, usage.estimated) == (True, True) and usage.tokens_in > 0

            # A Stop while the request is out counts the same way.
            monkeypatch.setattr(chat_runner, "FIRST_TOKEN_S", 30.0)
            run_ = await _start_direct(env, chat["id"])
            await asyncio.sleep(0.1)
            await chat_runner.cancel(run_.message_id)
            assert len(await _usage(env)) == 2

    run(scenario())


def test_startup_sweep_and_dataset_delete_mid_reply(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, _p, chat_id = await _setup(env)
            async with env.Session() as db:
                db.add(ChatMessage(chat_id=chat_id, seq=0, role="assistant", content="half", status="partial"))
                await db.commit()
            assert await chat_runner.mark_interrupted() == 1
            async with env.Session() as db:
                row = (await db.execute(select(ChatMessage))).scalar_one()
                assert (row.status, row.error_kind, row.content) == ("error", "interrupted", "half")

            fake_llm.replies.append(_slow_reply())
            async with env.Session() as db:
                from backend.models import Chat
                run_ = await chat_runner.start(db, await db.get(Chat, chat_id), "Go")
            await _until(lambda: len(run_.text) > 10)
            assert (await env.client.delete(f"{API}/datasets/{ds['id']}")).status_code == 204
            assert run_.task.done() and chat_runner.get_run(run_.message_id) is None
            # The usage outlives the chat, unattached.
            (usage,) = await _usage(env)
            assert usage.chat_id is None and usage.tokens_out > 0

    run(scenario())


def test_shutdown_marks_replies_interrupted(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env)
            fake_llm.replies.append(_slow_reply())
            async with env.Session() as db:
                from backend.models import Chat
                run_ = await chat_runner.start(db, await db.get(Chat, chat_id), "Go")
            await _until(lambda: len(run_.text) > 10)
            await chat_runner.stop_all()
            async with env.Session() as db:
                row = await db.get(ChatMessage, run_.message_id)
                assert (row.status, row.error_kind) == ("error", "interrupted") and row.content

    run(scenario())


def test_only_shared_categories_reach_the_provider(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, _p, chat_id = await _setup(env)
            await env.client.patch(f"{API}/datasets/{ds['id']}", json={"goal": "GOALSENT"})
            async with env.Session() as db:
                for img in (await db.execute(select(Image))).scalars():
                    img.caption_text = "CAPSENT on a red field"
                await db.commit()
            await env.client.patch(f"{API}/chats/{chat_id}", json={"privacy": {
                "numbers": True, "identity": False, "notes": False, "tags": False,
                "captions": False, "file_names": False,
            }})
            await _send(env, chat_id, "Hi")
            sent = json.dumps(fake_llm.requests[-1]["json"])
            assert "GOALSENT" not in sent and "CAPSENT" not in sent and "a.png" not in sent
            assert "## Findings" in sent

    run(scenario())


def test_a_chat_and_a_caption_job_share_the_providers_limit(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr(chat_runner, "IDLE_S", 1.0)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds, prov, chat_id = await _setup(env, max_concurrent=1)
            img = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()[0]
            # The chat streams a little, then stalls, holding the provider's one slot.
            fake_llm.replies.append(Reply(text=FAKE_REPLY, stall_after=1))
            async with env.Session() as db:
                from backend.models import Chat
                run_ = await chat_runner.start(db, await db.get(Chat, chat_id), "Go")
            await _until(lambda: len(fake_llm.requests) == 1 and run_.text)
            assert llm_limiter.in_use(prov["id"]) == 1

            r = await env.client.post(f"{API}/captioning/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "model": f"openai_compat:{prov['id']}:fake-vision", "overwrite": True,
            })
            assert r.status_code == 200, r.text
            await asyncio.sleep(0.4)
            # The caption request waits for the slot: the fake has seen only the chat.
            assert len(fake_llm.requests) == 1
            await run_.task
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert len(fake_llm.requests) == 2
            assert llm_limiter.in_use(prov["id"]) == 0

    run(scenario())


def test_a_caption_stop_drops_requests_still_waiting_for_a_slot(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr(chat_runner, "IDLE_S", 1.5)
    monkeypatch.setattr(llm_limiter, "STOP_POLL_S", 0.05)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds, prov, chat_id = await _setup(env, max_concurrent=1)
            await upload_image(env, ds["id"], name="b.png", data=png_bytes((30, 120, 30)))
            ids = [i["id"] for i in (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()]
            fake_llm.replies.append(Reply(text=FAKE_REPLY, stall_after=1))
            run_ = await _start_direct(env, chat_id)
            await _until(lambda: len(fake_llm.requests) == 1 and run_.text)

            r = await env.client.post(f"{API}/captioning/run", json={
                "dataset_id": ds["id"], "image_ids": ids,
                "model": f"openai_compat:{prov['id']}:fake-vision", "overwrite": True,
            })
            job_id = r.json()["job_id"]
            await asyncio.sleep(0.3)
            assert (await env.client.delete(f"{API}/jobs/{job_id}")).status_code == 204
            job = await wait_for_job(env, job_id)
            # Stopped while waiting: no caption request ever reached the server, and
            # nothing is counted as a failed image.
            assert job["status"] == "cancelled", job
            assert len(fake_llm.requests) == 1
            assert (job.get("result_data") or {}).get("failed_count", 0) == 0
            await run_.task

    run(scenario())
