"""The tool loop in the send path (M12 phase 3d), against the fake LLM server: a round
trip and what it stores, who gets tools, the caps on rounds, result size and calls per
round, the budget between rounds, cancel and resume mid-loop, and the manifest merge
that lets the history rule withhold a reply that quoted a tool's data.
"""
import asyncio

from sqlalchemy import select

from backend.models import ChatMessage, ChatToolCall, Image, LlmUsage
from backend.services import chat_runner
from backend.services.analyst_context import TOOLS_PROMPT
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image
from backend.tests.fake_llm import Reply, ToolCall
from backend.tests.test_chat_send import SECRET, _events, _send, _start_direct, _until

SHARE_ALL = {c: True for c in ("numbers", "identity", "notes", "tags", "captions", "file_names")}


def _call(name: str, arguments: str = "{}", text: str = "") -> Reply:
    return Reply(text=text, finish_reason="tool_calls", tool_calls=[ToolCall(name, arguments)])


async def _setup(env, *, probe: bool = True, billed: bool | None = None, max_tokens: int = 256,
                 captions: bool = True, privacy: dict | None = None) -> tuple[dict, dict, str]:
    """A dataset with two images (captioned or not), a provider whose default model
    passed the tool-call probe (unless `probe=False`), and a chat on it."""
    ds = await env.create_dataset("tools")
    for i, colour in enumerate([(120, 30, 30), (30, 120, 30)]):
        await upload_image(env, ds["id"], name=f"img_{i}.png", data=png_bytes(colour))
    if captions:
        async with env.Session() as db:
            for img in (await db.execute(select(Image).where(Image.dataset_id == ds["id"]))).scalars():
                img.caption_text = f"pip, forest, {img.filename}"
            await db.commit()
    body = {"name": "p", "base_url": "http://localhost:9/v1", "api_key": SECRET,
            "default_model": "fake-chat", "max_tokens": max_tokens}
    if billed is not None:
        body["billed"] = billed
    prov = (await env.client.post(f"{API}/providers/", json=body)).json()
    if probe:
        r = await env.client.post(f"{API}/providers/{prov['id']}/probe-tools", json={})
        assert r.status_code == 200 and r.json()["ok"], r.text
    r = await env.client.post(f"{API}/datasets/{ds['id']}/chats", json={"provider_id": prov["id"]})
    chat_id = r.json()["id"]
    if privacy is not None:
        assert (await env.client.patch(f"{API}/chats/{chat_id}", json={"privacy": privacy})).status_code == 200
    return ds, prov, chat_id


def _chat_requests(fake) -> list[dict]:
    """The chat requests after the probe's two."""
    return [r["json"] for r in fake.requests if r["path"].endswith("/chat/completions")
            and not any((t.get("function") or {}).get("name") == "crucible_echo" for t in r["json"].get("tools") or [])]


def _offered(request: dict) -> list[str]:
    return [t["function"]["name"] for t in request.get("tools") or []]


async def _rows(env, model):
    async with env.Session() as db:
        return list((await db.execute(select(model).order_by(model.id))).scalars().all())


def test_a_tool_round_trip(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env, privacy=SHARE_ALL)
            fake_llm.replies.append(_call("list_snapshots", text="Let me look."))
            r, events = await _send(env, chat_id, "What snapshots are there?")
            assert r.status_code == 200
            names = [e for e, _ in events]
            assert names.index("tool_call") < names.index("tool_result") < names.index("done")
            final = events[-1][1]
            assert final["status"] == "complete" and final["error_kind"] is None
            # The round's text and the answer, kept apart.
            assert final["content"].startswith("Let me look.\n\nThe tool returned: Result of list_snapshots")
            [call] = final["tool_calls"]
            assert (call["name"], call["status"], call["round"]) == ("list_snapshots", "ok", 0)
            assert call["result_text"].startswith("Result of list_snapshots") and "No snapshots yet." in call["result_text"]

            first, second = _chat_requests(fake_llm)
            assert _offered(first) == ["list_snapshots", "get_finding", "sample_captions", "tag_stats",
                                       "compare_snapshot"]
            assert first["tool_choice"] == "auto" and TOOLS_PROMPT[:40] in first["messages"][0]["content"]
            assistant, tool = second["messages"][-2:]
            assert assistant["tool_calls"][0]["function"]["name"] == "list_snapshots"
            assert assistant["content"] == "Let me look."
            assert (tool["role"], tool["tool_call_id"], tool["content"]) == (
                "tool", assistant["tool_calls"][0]["id"], call["result_text"])

            # One usage row per call, committed with the message; the message sums them.
            usage = [u for u in await _rows(env, LlmUsage) if u.message_id == final["id"]]
            assert len(usage) == 2
            assert final["tokens_in"] == sum(u.tokens_in for u in usage)
            assert final["tokens_out"] == sum(u.tokens_out for u in usage)
            assert final["context_manifest"]["tool_calls"] == 1
            assert final["context_manifest"]["tools_offered"] == _offered(first)
            # GET returns the calls with the message; a chat still counts two messages.
            detail = (await env.client.get(f"{API}/chats/{chat_id}")).json()
            assert detail["messages"][-1]["tool_calls"][0]["call_id"] == call["call_id"]
            listed = (await env.client.get(f"{API}/datasets/{_ds['id']}/chats")).json()
            assert listed[0]["message_count"] == 2

    run(scenario())


def test_only_a_probed_model_gets_tools(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, prov, chat_id = await _setup(env, probe=False)
            await _send(env, chat_id, "Hi")
            [req] = _chat_requests(fake_llm)
            assert "tools" not in req and "tool_choice" not in req
            assert TOOLS_PROMPT[:40] not in req["messages"][0]["content"]
            # A pass for another model does not count for this one.
            await env.client.post(f"{API}/providers/{prov['id']}/probe-tools", json={"model": "other"})
            await _send(env, chat_id, "Again")
            assert "tools" not in _chat_requests(fake_llm)[-1]
            preview = (await env.client.post(f"{API}/chats/{chat_id}/preview-context", json={})).json()
            assert preview["manifest"]["tools_offered"] == []

    run(scenario())


def test_privacy_decides_which_tools_are_offered(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            numbers_only = {c: c == "numbers" for c in SHARE_ALL}
            _ds, _p, chat_id = await _setup(env, privacy=numbers_only)
            await _send(env, chat_id, "Hi")
            assert _offered(_chat_requests(fake_llm)[-1]) == ["list_snapshots", "get_finding", "compare_snapshot"]
            captions_only = {c: c == "captions" for c in SHARE_ALL}
            await env.client.patch(f"{API}/chats/{chat_id}", json={"privacy": captions_only})
            await _send(env, chat_id, "Again")
            assert _offered(_chat_requests(fake_llm)[-1]) == ["sample_captions"]
            preview = (await env.client.post(f"{API}/chats/{chat_id}/preview-context", json={})).json()
            assert preview["manifest"]["tools_offered"] == ["sample_captions"]

    run(scenario())


def test_the_round_cap_ends_with_a_call_that_may_not_use_tools(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr(chat_runner, "MAX_TOOL_ROUNDS", 2)

    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env, privacy=SHARE_ALL)
            # Two tool rounds, then the model answers.
            fake_llm.replies.extend([_call("list_snapshots"), _call("tag_stats")])
            _r, events = await _send(env, chat_id, "Go")
            reqs = _chat_requests(fake_llm)
            assert [q["tool_choice"] for q in reqs] == ["auto", "auto", "none"]
            # The last call still carries the tools, as "none": some servers refuse tool
            # messages in a request that offers no tools.
            assert _offered(reqs[-1])
            assert events[-1][1]["status"] == "complete" and len(events[-1][1]["tool_calls"]) == 2

            # A model that asks for tools past the cap, and writes nothing, is an error.
            fake_llm.replies.extend([_call("list_snapshots"), _call("list_snapshots"), _call("list_snapshots")])
            _r, events = await _send(env, chat_id, "Again")
            final = events[-1][1]
            assert (final["status"], final["error_kind"]) == ("error", "tools")
            assert len(final["tool_calls"]) == 2

    run(scenario())


def test_the_result_size_cap_stops_offering_tools(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr(chat_runner, "TOOL_TURN_MAX_CHARS", 10)

    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env, privacy=SHARE_ALL)
            fake_llm.replies.append(_call("list_snapshots"))
            _r, events = await _send(env, chat_id, "Go")
            assert [q["tool_choice"] for q in _chat_requests(fake_llm)] == ["auto", "none"]
            assert events[-1][1]["status"] == "complete"

    run(scenario())


def test_calls_past_the_per_round_cap_are_answered_not_run(fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr(chat_runner, "MAX_CALLS_PER_ROUND", 1)

    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env, privacy=SHARE_ALL)
            fake_llm.replies.append(Reply(text="", finish_reason="tool_calls", tool_calls=[
                ToolCall("list_snapshots"), ToolCall("tag_stats"), ToolCall("no_such_tool")]))
            _r, events = await _send(env, chat_id, "Go")
            calls = events[-1][1]["tool_calls"]
            assert [(c["name"], c["status"]) for c in calls] == [
                ("list_snapshots", "ok"), ("tag_stats", "error"), ("no_such_tool", "error")]
            assert "did not" in calls[1]["result_text"]
            # Every call is answered, so the next request is well formed.
            second = _chat_requests(fake_llm)[-1]
            assert [m["tool_call_id"] for m in second["messages"] if m["role"] == "tool"] == [c["call_id"] for c in calls]

    run(scenario())


def test_a_bad_call_is_an_error_row_and_the_reply_goes_on(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env, privacy=SHARE_ALL)
            fake_llm.replies.append(_call("get_finding", '{"finding_id": "nope"}'))
            _r, events = await _send(env, chat_id, "Go")
            final = events[-1][1]
            assert final["status"] == "complete"
            assert final["tool_calls"][0]["status"] == "error" and "No finding has the id" in final["tool_calls"][0]["result_text"]

    run(scenario())


def test_the_budget_is_checked_before_each_round(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, prov, chat_id = await _setup(env, billed=True, max_tokens=64, privacy=SHARE_ALL)
            # A chat budget the first call fits and the second cannot.
            preview = (await env.client.post(f"{API}/chats/{chat_id}/preview-context", json={"draft": "Go"})).json()
            estimate = preview["budget"]["estimate"]
            if estimate < 1000:  # the setting's floor
                await env.client.patch(f"{API}/providers/{prov['id']}", json={"max_tokens": 64 + 1001 - estimate})
                preview = (await env.client.post(f"{API}/chats/{chat_id}/preview-context", json={"draft": "Go"})).json()
                estimate = preview["budget"]["estimate"]
            await env.client.patch(f"{API}/settings/thresholds", json={"analyst_chat_budget_tokens": estimate + 10})
            fake_llm.replies.append(_call("list_snapshots", text="Checking."))
            r, events = await _send(env, chat_id, "Go")
            assert r.status_code == 200
            final = events[-1][1]
            assert (final["status"], final["error_kind"]) == ("complete", "budget")
            assert final["content"] == "Checking." and "Settings → Analyst" in final["error_detail"]
            assert len(_chat_requests(fake_llm)) == 1
            assert len([u for u in await _rows(env, LlmUsage) if u.message_id == final["id"]]) == 1

    run(scenario())


def test_a_reply_that_quoted_a_tool_is_withheld_once_its_category_is_off(fake_llm, tmp_path):
    """The context of an uncaptioned dataset sends no captions; the tool does. The merge
    into the reply's manifest is what lets the history rule see it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env, captions=False, privacy=SHARE_ALL)
            fake_llm.replies.append(_call("sample_captions"))
            _r, events = await _send(env, chat_id, "Show me captions")
            final = events[-1][1]
            assert final["tool_calls"][0]["categories"] == ["captions", "file_names", "numbers"]
            async with env.Session() as db:
                msg = await db.get(ChatMessage, final["id"])
                assert msg.context_manifest["categories"]["captions"]["sent"] is True
            await env.client.patch(f"{API}/chats/{chat_id}", json={"privacy": {**SHARE_ALL, "captions": False}})
            preview = (await env.client.post(f"{API}/chats/{chat_id}/preview-context", json={})).json()
            assert preview["manifest"]["history_withheld"] == 1

    run(scenario())


def test_cancel_and_resume_in_the_second_round(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            _ds, _p, chat_id = await _setup(env, privacy=SHARE_ALL)
            fake_llm.replies.extend([_call("list_snapshots"),
                                     Reply(text="word " * 200, chunk_size=5, chunk_delay=0.02)])
            run_ = await _start_direct(env, chat_id)
            await _until(lambda: len(run_.text) > 30)
            # A late listener gets the tool calls so far in `start`.
            listener = asyncio.create_task(env.client.get(f"{API}/chats/{chat_id}/messages/{run_.message_id}/stream"))
            await _until(lambda: len(run_.listeners) > 0)
            r = await env.client.post(f"{API}/chats/{chat_id}/messages/{run_.message_id}/cancel")
            got = r.json()
            assert got["status"] == "cancelled" and got["content"].startswith("word")
            start = _events((await listener).text)[0]
            assert start[0] == "start" and [c["name"] for c in start[1]["tool_calls"]] == ["list_snapshots"]
            # The tool round's usage and the cut-short call's, once each.
            assert len([u for u in await _rows(env, LlmUsage) if u.message_id == run_.message_id]) == 2
            assert len(await _rows(env, ChatToolCall)) == 1

    run(scenario())
