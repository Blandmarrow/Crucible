"""The fake LLM server's own tests (`fake_llm.py`), driven through the real OpenAI SDK.

`openai` is in `requirements-ci.txt` so that these run in CI: a fake checked only
against a hand-made stand-in of the SDK could drift from what the SDK parses.
"""
import asyncio
import threading
import time

import httpx
import openai
import pytest

from backend.ml import openai_compat_captioner
from backend.tests.fake_llm import (
    FAKE_REPLY,
    LENGTH_TEXT,
    NOTES_MALFORMED_TEXT,
    NOTES_TEXT,
    REFUSAL_TEXT,
    FakeLLM,
    Reply,
    ToolCall,
    serve,
)

MSG = [{"role": "user", "content": "Hello"}]


def _say(text: str) -> list[dict]:
    return [{"role": "system", "content": "You are a test."}, {"role": "user", "content": text}]


async def _complete(client, messages=MSG, **kw):
    async with client:
        return await client.chat.completions.create(model="fake-chat", messages=messages, **kw)


async def _stream(client, messages=MSG, **kw):
    """Every chunk of a streamed reply, and the text it adds up to."""
    async with client:
        stream = await client.chat.completions.create(model="fake-chat", messages=messages, stream=True, **kw)
        chunks = [c async for c in stream]
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    return chunks, text


def test_lists_its_models():
    fake = FakeLLM()

    async def go():
        async with fake.openai_client() as client:
            return [m.id async for m in client.models.list()]

    assert asyncio.run(go()) == ["fake-chat", "fake-vision"]


def test_the_default_reply_plain_and_streamed():
    fake = FakeLLM()
    resp = asyncio.run(_complete(fake.openai_client()))
    assert resp.choices[0].message.content == FAKE_REPLY
    assert resp.choices[0].finish_reason == "stop"
    assert resp.usage.completion_tokens > 0

    chunks, text = asyncio.run(_stream(fake.openai_client()))
    assert text == FAKE_REPLY
    assert len([c for c in chunks if c.choices and c.choices[0].delta.content]) > 1
    assert chunks[-1].choices[0].finish_reason == "stop"
    assert all(c.usage is None for c in chunks)  # usage only when asked for


def test_a_stream_reports_usage_when_asked_and_no_usage_suppresses_it():
    fake = FakeLLM()
    chunks, _ = asyncio.run(_stream(fake.openai_client(), stream_options={"include_usage": True}))
    assert chunks[-1].choices == [] and chunks[-1].usage.total_tokens > 0

    chunks, _ = asyncio.run(_stream(fake.openai_client(), _say("[[fake:no-usage]] hi"),
                                    stream_options={"include_usage": True}))
    assert all(c.usage is None for c in chunks)
    resp = asyncio.run(_complete(fake.openai_client(), _say("[[fake:no-usage]] hi")))
    assert resp.usage is None


@pytest.mark.parametrize(("marker", "text", "finish"), [
    ("refusal", REFUSAL_TEXT, "stop"),
    ("empty", "", "stop"),
    ("length", LENGTH_TEXT, "length"),
    ("notes", NOTES_TEXT, "stop"),
    ("notes-malformed", NOTES_MALFORMED_TEXT, "stop"),
])
def test_text_presets_plain_and_streamed(marker, text, finish):
    fake = FakeLLM()
    resp = asyncio.run(_complete(fake.openai_client(), _say(f"[[fake:{marker}]] go")))
    assert (resp.choices[0].message.content or "") == text
    assert resp.choices[0].finish_reason == finish
    chunks, streamed = asyncio.run(_stream(fake.openai_client(), _say(f"[[fake:{marker}]] go")))
    assert streamed == text
    assert chunks[-1].choices[0].finish_reason == finish


def test_a_marker_counts_only_in_the_last_user_message():
    fake = FakeLLM()
    messages = [
        {"role": "user", "content": "[[fake:refusal]] earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [{"type": "text", "text": "now"},
                                     {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]},
    ]
    resp = asyncio.run(_complete(fake.openai_client(), messages))
    assert resp.choices[0].message.content == FAKE_REPLY


def test_an_unknown_marker_is_a_400_naming_it():
    fake = FakeLLM()
    with pytest.raises(openai.BadRequestError, match=r"\[\[fake:refusel\]\]"):
        asyncio.run(_complete(fake.openai_client(), _say("[[fake:refusel]]")))


TOOLS = [{"type": "function", "function": {"name": "get_findings", "parameters": {"type": "object"}}}]


def test_the_tool_marker_calls_the_first_tool_plain_and_streamed():
    fake = FakeLLM()
    resp = asyncio.run(_complete(fake.openai_client(), _say("[[fake:tool]]"), tools=TOOLS))
    call = resp.choices[0].message.tool_calls[0]
    assert (call.function.name, call.function.arguments) == ("get_findings", "{}")
    assert resp.choices[0].finish_reason == "tool_calls"

    chunks, _ = asyncio.run(_stream(fake.openai_client(), _say("[[fake:tool]]"), tools=TOOLS))
    deltas = [tc for c in chunks if c.choices for tc in (c.choices[0].delta.tool_calls or [])]
    assert deltas[0].function.name == "get_findings"
    assert "".join(d.function.arguments or "" for d in deltas) == "{}"
    assert chunks[-1].choices[0].finish_reason == "tool_calls"


def test_a_scripted_tool_call_streams_its_arguments_in_fragments():
    fake = FakeLLM()
    args = '{"filter": {"captioned": false}, "limit": 20}'
    fake.replies.append(Reply(text="", tool_calls=[ToolCall("sample_captions", args)], finish_reason="tool_calls"))
    chunks, _ = asyncio.run(_stream(fake.openai_client()))
    frags = [tc.function.arguments for c in chunks if c.choices
             for tc in (c.choices[0].delta.tool_calls or []) if tc.function.arguments]
    assert len(frags) == 2 and "".join(frags) == args


@pytest.mark.parametrize(("marker", "exc"), [
    ("429", openai.RateLimitError),
    ("500", openai.InternalServerError),
    ("401", openai.AuthenticationError),
])
def test_error_markers_raise_the_sdks_own_errors(marker, exc):
    fake = FakeLLM()
    with pytest.raises(exc):
        asyncio.run(_complete(fake.openai_client(), _say(f"[[fake:{marker}]]")))
    with pytest.raises(exc):
        asyncio.run(_stream(fake.openai_client(), _say(f"[[fake:{marker}]]")))


def test_scripted_replies_come_first_and_in_order():
    fake = FakeLLM()
    fake.replies.extend([Reply(text="one"), Reply(text="two")])
    texts = [asyncio.run(_complete(fake.openai_client(), _say("[[fake:refusal]]"))).choices[0].message.content
             for _ in range(3)]
    assert texts == ["one", "two", REFUSAL_TEXT]


def test_every_request_is_recorded_with_its_body_and_headers():
    fake = FakeLLM()
    asyncio.run(_complete(fake.openai_client(api_key="sk-secret"), _say("sentinel-ABC")))
    (req,) = fake.requests
    assert req["path"] == "/v1/chat/completions"
    assert req["json"]["messages"][1]["content"] == "sentinel-ABC"
    assert req["headers"]["authorization"] == "Bearer sk-secret"


def test_a_stall_ends_cleanly_when_the_caller_gives_up():
    fake = FakeLLM()
    fake.replies.append(Reply(text="x" * 64, stall_after=2))
    received: list[str] = []

    async def go():
        async with fake.openai_client() as client:
            stream = await client.chat.completions.create(model="fake-chat", messages=MSG, stream=True)

            async def read():
                async for c in stream:
                    if c.choices and c.choices[0].delta.content:
                        received.append(c.choices[0].delta.content)

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(read(), 0.3)

    asyncio.run(go())
    assert "".join(received) == "x" * 16  # two chunks of eight, then nothing
    assert fake.stall_endings == ["cancelled"]


def test_a_short_stall_times_out_and_ends_the_stream_unfinished():
    fake = FakeLLM()
    fake.replies.append(Reply(text="x" * 64, stall_after=1, stall_seconds=0.05))
    chunks, text = asyncio.run(_stream(fake.openai_client()))
    assert text == "x" * 8
    assert all(c.choices[0].finish_reason is None for c in chunks if c.choices)
    assert fake.stall_endings == ["timeout"]


def test_the_httpx_transport_serves_plain_httpx_callers():
    fake = FakeLLM()

    async def go():
        async with httpx.AsyncClient(transport=fake.transport(), base_url="http://llm") as client:
            return (await client.get("/v1/models")).json()

    assert [m["id"] for m in asyncio.run(go())["data"]] == ["fake-chat", "fake-vision"]


def test_the_fixture_routes_make_client_to_the_fake(fake_llm, tmp_path):
    from PIL import Image

    path = tmp_path / "a.png"
    Image.new("RGB", (16, 16), (200, 30, 30)).save(path)
    caption = asyncio.run(openai_compat_captioner.caption_image(
        str(path), base_url="http://ignored/v1", api_key="k", model_name="fake-vision"))
    assert caption == FAKE_REPLY
    (req,) = fake_llm.requests
    parts = req["json"]["messages"][0]["content"]
    assert parts[0]["type"] == "image_url"


# ── Over a real socket ───────────────────────────────────────────────────────

@pytest.fixture
def served():
    fake = FakeLLM()
    server = serve(fake, port=0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


def _socket_client(url: str):
    return openai.AsyncOpenAI(base_url=url, api_key="k", max_retries=0, timeout=10)


def test_a_real_socket_streams_to_the_sdk(served):
    fake, url = served
    chunks, text = asyncio.run(_stream(_socket_client(url), stream_options={"include_usage": True}))
    assert text == FAKE_REPLY
    assert chunks[-1].usage is not None
    assert asyncio.run(_complete(_socket_client(url))).choices[0].message.content == FAKE_REPLY
    assert httpx.get(url.removesuffix("/v1") + "/_fake/requests").json()[0]["path"] == "/v1/chat/completions"


def test_a_stalled_socket_stream_ends_when_the_client_leaves(served):
    fake, url = served
    fake.replies.append(Reply(text="x" * 64, stall_after=2))

    async def go():
        client = _socket_client(url)
        try:
            stream = await client.chat.completions.create(model="fake-chat", messages=MSG, stream=True)

            async def read():
                async for _ in stream:
                    pass

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(read(), 0.3)
        finally:
            await client.close()

    asyncio.run(go())
    deadline = time.monotonic() + 5
    while not fake.stall_endings and time.monotonic() < deadline:
        time.sleep(0.05)
    assert fake.stall_endings == ["client_gone"]
    # The stall held no lock: the server still answers.
    assert asyncio.run(_complete(_socket_client(url))).choices[0].message.content == FAKE_REPLY
