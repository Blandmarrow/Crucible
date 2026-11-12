"""`backend/ml/openai_compat_captioner.py`: the reply parser and the one-client request path (M10 T2).

CI has no `openai` package (`backend/requirements-ci.txt`), so the SDK is faked with an
object that has `chat.completions.create`; only the test of the real `make_client` needs
the package and skips without it. The autouse `_no_real_llm_server` fixture replaces
`make_client`, so that one test loads a fresh copy of the module's source instead.
"""
import asyncio
import importlib.util
import logging
from types import SimpleNamespace

import pytest
from PIL import Image

import backend.ml.openai_compat_captioner as mod


def _resp(content, finish="stop", **message):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, **message), finish_reason=finish)])


class _Client:
    def __init__(self, resp):
        self.resp = resp
        self.calls: list[dict] = []
        self.closed = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.resp

    async def close(self):
        self.closed += 1


@pytest.fixture
def image_file(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGB", (32, 32), (10, 120, 200)).save(p)
    return str(p)


def test_empty_reply_logs_the_warning_and_returns_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        assert mod._caption_text(_resp("   ", finish="length", reasoning_content="thinking"), "m") == ""
    assert "returned an empty caption" in caplog.text
    assert "reasoning_content=present" in caplog.text


def test_no_choices_returns_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        assert mod._caption_text(SimpleNamespace(choices=[]), "m") == ""
    assert "no choices" in caplog.text


def test_a_truncated_reply_is_kept_and_warned_about(caplog):
    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        assert mod._caption_text(_resp(" a half caption ", finish="length"), "m") == "a half caption"
    assert "hit its token limit" in caplog.text


def test_a_clean_reply_logs_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        assert mod._caption_text(_resp("fine"), "m") == "fine"
    assert caplog.text == ""


def test_no_client_and_no_sdk_returns_empty(monkeypatch, image_file):
    monkeypatch.setattr(mod, "make_client", lambda *a, **k: None)
    out = asyncio.run(mod.caption_image(image_file, base_url="http://x/v1", api_key="", model_name="m"))
    assert out == ""


def test_an_injected_client_is_used_and_left_open(image_file):
    client = _Client(_resp("a caption"))
    out = asyncio.run(mod.caption_image(
        image_file, base_url="http://unused/v1", api_key="k", model_name="vl-model",
        custom_prompt="describe it", max_tokens=77, client=client))
    assert out == "a caption"
    assert client.closed == 0                      # the job owns it, not the call
    (call,) = client.calls
    assert call["model"] == "vl-model" and call["max_tokens"] == 77
    parts = call["messages"][0]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert parts[1] == {"type": "text", "text": "describe it"}


def test_a_client_the_call_built_is_closed_by_it(monkeypatch, image_file):
    client = _Client(_resp("x"))
    monkeypatch.setattr(mod, "make_client", lambda *a, **k: client)
    assert asyncio.run(mod.caption_image(image_file, base_url="u", api_key="", model_name="m")) == "x"
    assert client.closed == 1


def test_a_client_the_call_built_is_closed_when_the_request_fails(monkeypatch, image_file):
    client = _Client(None)

    async def boom(**_kw):
        raise ConnectionError("down")

    client.chat.completions.create = boom
    monkeypatch.setattr(mod, "make_client", lambda *a, **k: client)
    with pytest.raises(ConnectionError):
        asyncio.run(mod.caption_image(image_file, base_url="u", api_key="", model_name="m"))
    assert client.closed == 1


def test_the_real_make_client_sets_one_attempt_and_the_configured_timeout():
    pytest.importorskip("openai")
    # A fresh copy of the source: the module's own `make_client` is patched for every test.
    spec = importlib.util.spec_from_file_location("_openai_compat_captioner_fresh", mod.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)

    async def go():
        client = fresh.make_client("http://127.0.0.1:1/v1", "", 42)
        try:
            assert client.max_retries == 0
            assert client.timeout == 42
            assert client.api_key == "no-key"
            assert str(client.base_url).startswith("http://127.0.0.1:1/v1")
        finally:
            await client.close()

    asyncio.run(go())
