"""Tool calls in the chat stream and the per-model tool-call probe (M12 phase 3b).

`ToolCallBuffer` is tested on its own for the edges servers differ on; `stream_chat` with
tools against the fake LLM; and the probe route end to end: a pass, each way of failing,
a server that refuses tools, a call that reaches no verdict, the daily budget, the usage
it records, and the verdicts cleared when the server changes.
"""
import asyncio
import json
from types import SimpleNamespace as NS

from sqlalchemy import select

from backend.ml.chat_stream import ToolCallBuffer, stream_chat
from backend.models import LlmUsage, OpenAIProvider
from backend.schemas.openai_provider import tools_ok
from backend.services import tool_probe
from backend.tests.conftest import API, api_env, run
from backend.tests.fake_llm import Reply, ToolCall

SECRET = "sk-test-SECRET-key-1234"
ECHO = '{"word": "ember"}'


def _piece(index=None, id=None, name=None, arguments=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=arguments))


# --- ToolCallBuffer -------------------------------------------------------------------


def test_buffer_joins_fragments_by_index():
    b = ToolCallBuffer()
    b.add([_piece(0, "call_a", "get_finding", "")])
    b.add([_piece(1, "call_b", "tag_stats", '{"li')])
    b.add([_piece(0, None, None, '{"finding_id"')])
    b.add([_piece(0, None, None, ': "blurry"}')])
    b.add([_piece(1, None, None, 'mit": 5}')])
    calls = b.calls()
    assert [(c.id, c.name) for c in calls] == [("call_a", "get_finding"), ("call_b", "tag_stats")]
    assert json.loads(calls[0].arguments) == {"finding_id": "blurry"}
    assert json.loads(calls[1].arguments) == {"limit": 5}


def test_buffer_edges_servers_differ_on():
    b = ToolCallBuffer()
    # No index: continues the last call, unless a different id starts a new one.
    b.add([_piece(None, "x1", "a", '{"k":')])
    b.add([_piece(None, None, None, ' 1}')])
    b.add([_piece(None, "x2", "b", "{}")])
    # A name sent again whole is not doubled.
    b.add([_piece(None, None, "b", None)])
    calls = b.calls()
    assert [(c.id, c.name, c.arguments) for c in calls] == [("x1", "a", '{"k": 1}'), ("x2", "b", "{}")]
    # A call that never got an id is given one, so the answer can name it.
    b = ToolCallBuffer()
    b.add([_piece(0, None, "a", "{}")])
    assert b.calls()[0].id == "call_0"


# --- stream_chat with tools -----------------------------------------------------------


def test_stream_assembles_streamed_tool_calls(fake_llm):
    fake_llm.replies.append(Reply(text="", finish_reason="tool_calls", tool_calls=[
        ToolCall("get_finding", '{"finding_id": "low_resolution"}'), ToolCall("tag_stats", "{}")]))
    tools = [{"type": "function", "function": {"name": n, "parameters": {"type": "object"}}}
             for n in ("get_finding", "tag_stats")]

    async def scenario():
        client = fake_llm.openai_client()
        try:
            return await stream_chat(
                client, model="fake-chat", messages=[{"role": "user", "content": "hi"}], max_tokens=64,
                first_token_s=5, idle_s=5, on_text=lambda _p: None, tools=tools)
        finally:
            await client.close()

    result = run(scenario())
    assert result.finish_reason == "tool_calls"
    assert [(c.name, json.loads(c.arguments)) for c in result.tool_calls] == [
        ("get_finding", {"finding_id": "low_resolution"}), ("tag_stats", {})]
    sent = fake_llm.requests[-1]["json"]
    assert sent["tools"] == tools and sent["tool_choice"] == "auto"


def test_stream_without_tools_offers_none(fake_llm):
    async def scenario():
        client = fake_llm.openai_client()
        try:
            return await stream_chat(
                client, model="fake-chat", messages=[{"role": "user", "content": "hi"}], max_tokens=64,
                first_token_s=5, idle_s=5, on_text=lambda _p: None)
        finally:
            await client.close()

    assert run(scenario()).tool_calls == []
    assert "tools" not in fake_llm.requests[-1]["json"]
    assert "tool_choice" not in fake_llm.requests[-1]["json"]


class _SlowAfterToolCall:
    """A stream that sends one tool-call piece, goes quiet longer than the first-token
    limit but within the idle limit, then ends."""

    def __init__(self):
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **_kw):
        return _Stream()


class _Stream:
    def __init__(self):
        self._sent = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self._sent += 1
        if self._sent == 1:
            return NS(usage=None, choices=[NS(delta=NS(content=None, tool_calls=[_piece(0, "c", "a", "{}")]),
                                              finish_reason=None)])
        if self._sent == 2:
            await asyncio.sleep(0.3)
            return NS(usage=None, choices=[NS(delta=NS(content=None, tool_calls=None), finish_reason="tool_calls")])
        raise StopAsyncIteration

    async def close(self):
        pass


def test_a_tool_call_piece_ends_the_first_token_limit():
    result = run(stream_chat(
        _SlowAfterToolCall(), model="m", messages=[], max_tokens=8,
        first_token_s=0.1, idle_s=2, on_text=lambda _p: None, tools=[{"type": "function"}]))
    assert result.finish_reason == "tool_calls" and [c.name for c in result.tool_calls] == ["a"]


# --- The probe ------------------------------------------------------------------------


async def _provider(env, *, billed=None, default_model="fake-chat", max_tokens=256) -> dict:
    body = {"name": f"p{billed}", "base_url": "http://localhost:9/v1", "api_key": SECRET,
            "default_model": default_model, "max_tokens": max_tokens}
    if billed is not None:
        body["billed"] = billed
    r = await env.client.post(f"{API}/providers/", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _probe(env, pid, model=None):
    return await env.client.post(f"{API}/providers/{pid}/probe-tools", json={"model": model} if model else {})


async def _row(env, pid) -> OpenAIProvider:
    async with env.Session() as db:
        return await db.get(OpenAIProvider, pid)


async def _usage(env) -> list[LlmUsage]:
    async with env.Session() as db:
        return list((await db.execute(select(LlmUsage).order_by(LlmUsage.id))).scalars().all())


def test_a_model_that_handles_tools_passes(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            p = await _provider(env)
            r = await _probe(env, p["id"])
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["model"] == "fake-chat" and out["ok"] is True and out["detail"] == tool_probe.PASSED
            # Two calls: the tool call, then the answer using the tool's result.
            first, second = (q["json"] for q in fake_llm.requests[-2:])
            assert first["tools"][0]["function"]["name"] == tool_probe.PROBE_TOOL_NAME
            assert second["messages"][-1]["role"] == "tool"
            assert second["messages"][-2]["tool_calls"][0]["function"]["name"] == tool_probe.PROBE_TOOL_NAME
            assert tools_ok(await _row(env, p["id"]), "fake-chat")
            assert not tools_ok(await _row(env, p["id"]), "another-model")
            listed = (await env.client.get(f"{API}/providers/")).json()[0]
            assert listed["tools_probe"]["fake-chat"]["ok"] is True
            # Each call recorded, with no chat; a local provider's usage is not billed.
            usage = await _usage(env)
            assert len(usage) == 2 and all(u.chat_id is None and u.billed is False for u in usage)

    run(scenario())


def test_each_way_of_failing_is_stored(fake_llm, tmp_path):
    cases = [
        ([Reply(text="ember")], "answered in text"),
        ([Reply(text="", finish_reason="tool_calls", tool_calls=[ToolCall("something_else", ECHO)])],
         "not offered"),
        ([Reply(text="", finish_reason="tool_calls", tool_calls=[ToolCall(tool_probe.PROBE_TOOL_NAME, "{oops")])],
         "arguments"),
        ([Reply(text="", finish_reason="tool_calls", tool_calls=[ToolCall(tool_probe.PROBE_TOOL_NAME, '{"word": "ash"}')])],
         "wrong word"),
        ([Reply(text="", finish_reason="tool_calls", tool_calls=[ToolCall(tool_probe.PROBE_TOOL_NAME, ECHO)]),
          Reply(text="I could not read it.")], "did not use"),
        ([Reply(text="", finish_reason="tool_calls", tool_calls=[ToolCall(tool_probe.PROBE_TOOL_NAME, ECHO)]),
          Reply(text="", finish_reason="tool_calls", tool_calls=[ToolCall(tool_probe.PROBE_TOOL_NAME, ECHO)])],
         "called the tool again"),
        ([Reply(status=400, error={"error": {"message": "tools are not supported", "type": "invalid_request_error",
                                             "param": None, "code": None}})], "refused a request that offers tools"),
    ]

    async def scenario():
        async with api_env(tmp_path) as env:
            p = await _provider(env)
            for replies, words in cases:
                fake_llm.replies.extend(replies)
                r = await _probe(env, p["id"])
                assert r.status_code == 200, (words, r.text)
                assert r.json()["ok"] is False and words in r.json()["detail"], (words, r.json())
                row = await _row(env, p["id"])
                assert row.tools_probe["fake-chat"]["ok"] is False and not tools_ok(row, "fake-chat")
            # A later pass replaces the failure.
            assert (await _probe(env, p["id"])).json()["ok"] is True
            assert tools_ok(await _row(env, p["id"]), "fake-chat")

    run(scenario())


def test_no_verdict_stores_nothing(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            p = await _provider(env)
            for marker in ("500", "401"):
                fake_llm.replies.append(Reply(status=int(marker), error={"error": {
                    "message": f"fake {marker} for {SECRET}", "type": "x", "param": None, "code": None}}))
                r = await _probe(env, p["id"])
                assert r.status_code == 502
                assert SECRET not in r.text and "***" in r.text
            # Cut off at Max tokens before calling: raise Max tokens, not a verdict.
            fake_llm.replies.append(Reply(text="Let me think", finish_reason="length"))
            r = await _probe(env, p["id"])
            assert r.status_code == 502 and "Max tokens" in r.json()["detail"]
            assert (await _row(env, p["id"])).tools_probe is None

    run(scenario())


def test_the_whole_probe_has_a_deadline(fake_llm, tmp_path, monkeypatch):
    """A model that keeps streaming (here text; in life, hidden reasoning, which the
    per-chunk limits never stop) ends in no verdict, not a click that spins until Max
    tokens runs out. The call it cut short is still recorded."""
    monkeypatch.setattr(tool_probe, "PROBE_TOTAL_S", 0.3)

    async def scenario():
        async with api_env(tmp_path) as env:
            p = await _provider(env)
            fake_llm.replies.append(Reply(text="thinking " * 40, chunk_size=4, chunk_delay=0.05))
            r = await _probe(env, p["id"])
            assert r.status_code == 502 and "longer than" in r.json()["detail"]
            assert (await _row(env, p["id"])).tools_probe is None
            for _ in range(50):  # the usage write is shielded and may finish just after
                if await _usage(env):
                    break
                await asyncio.sleep(0.02)
            assert len(await _usage(env)) == 1

    run(scenario())


def test_billed_probe_meets_the_daily_budget(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            # Two calls at up to 1,024 tokens each cannot fit a 1,000-token day.
            p = await _provider(env, billed=True, max_tokens=1024)
            await env.client.patch(f"{API}/settings/thresholds", json={"analyst_daily_budget_tokens": 1000})
            sent = len(fake_llm.requests)
            r = await _probe(env, p["id"])
            assert r.status_code == 409 and "daily budget" in r.json()["detail"]
            assert len(fake_llm.requests) == sent  # refused before any call
            await env.client.patch(f"{API}/settings/thresholds", json={"analyst_daily_budget_tokens": 100_000})
            assert (await _probe(env, p["id"])).json()["ok"] is True
            usage = await _usage(env)
            assert len(usage) == 2 and all(u.billed and u.chat_id is None for u in usage)

    run(scenario())


def test_model_choice_and_refusals(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            p = await _provider(env, default_model="")
            assert (await _probe(env, p["id"])).status_code == 422
            r = await _probe(env, p["id"], model="other-model")
            assert r.status_code == 200 and r.json()["model"] == "other-model"
            assert fake_llm.requests[-1]["json"]["model"] == "other-model"
            assert (await env.client.post(f"{API}/providers/nope/probe-tools", json={})).status_code == 404

    run(scenario())


def test_verdicts_cleared_when_the_server_changes(fake_llm, tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            p = await _provider(env)
            assert (await _probe(env, p["id"])).json()["ok"] is True
            # A rename or a new default model keeps them.
            r = await env.client.patch(f"{API}/providers/{p['id']}", json={"name": "renamed", "default_model": "x"})
            assert r.json()["tools_probe"]["fake-chat"]["ok"] is True
            # A new address clears them.
            r = await env.client.patch(f"{API}/providers/{p['id']}", json={"base_url": "http://localhost:10/v1"})
            assert r.json()["tools_probe"] == {}
            assert (await _row(env, p["id"])).tools_probe is None
            # So does a change in whether it bills.
            assert (await _probe(env, p["id"], model="fake-chat")).json()["ok"] is True
            r = await env.client.patch(f"{API}/providers/{p['id']}", json={"billed": True})
            assert r.json()["tools_probe"] == {}

    run(scenario())


def test_tools_ok_reads_only_a_pass():
    row = NS(tools_probe={"a": {"ok": True}, "b": {"ok": False}, "c": "junk"})
    assert tools_ok(row, "a")
    assert not tools_ok(row, "b") and not tools_ok(row, "c") and not tools_ok(row, "d")
    assert not tools_ok(row, None) and not tools_ok(NS(tools_probe=None), "a")
