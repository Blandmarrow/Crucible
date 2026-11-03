"""RewriteSession (M6 §3) against a fake client: the schema probe, the count check and
error classes. Also the standing-instructions order shared by every LLM call here.
No network and no `openai` import: the client is injected."""
import json
from types import SimpleNamespace

import pytest

from backend.ml import prompt_generator
from backend.ml.prompt_generator import BudgetExhausted, RewriteError, RewriteSession
from backend.tests.conftest import run


class StatusError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class APIConnectionError(Exception):
    """Named like the SDK's, which is how `_classify` recognises it."""


def _resp(content: str, finish: str = "stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])


class FakeClient:
    """Answers each create() from a script: a str is content, an Exception is raised.
    Records whether each request asked for the json_schema format."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, SimpleNamespace) else _resp(item)

    @property
    def schema_flags(self) -> list[bool]:
        return ["response_format" in c for c in self.calls]


def _session(client, **kw) -> RewriteSession:
    return RewriteSession(base_url="", api_key="", model_name="m", client=client, **kw)


def _arr(*prompts) -> str:
    return json.dumps({"prompts": list(prompts)})


def test_schema_that_works_is_kept_for_the_run():
    async def go():
        client = FakeClient(_arr("a", "b"), _arr("c"))
        s = _session(client)
        assert await s.rewrite(["t1", "t2"], []) == ["a", "b"]
        assert s.mode == "schema"
        assert await s.rewrite(["t3"], []) == ["c"]
        assert client.schema_flags == [True, True]
        assert s.requests == 2
    run(go())


def test_empty_schema_content_falls_back_to_plain_once_then_sticks():
    # LM Studio with a thinking model answers schema requests with nothing.
    async def go():
        client = FakeClient("", _arr("a"), _arr("b"))
        s = _session(client)
        assert await s.rewrite(["t1"], []) == ["a"]
        assert s.mode == "plain"
        assert await s.rewrite(["t2"], []) == ["b"]
        assert client.schema_flags == [True, False, False]
        assert s.requests == 3
    run(go())


def test_a_rejected_schema_switches_to_plain_without_a_retry():
    async def go():
        client = FakeClient(StatusError(400), _arr("a"))
        s = _session(client)
        assert await s.rewrite(["t1"], []) == ["a"]
        assert s.mode == "plain"
        assert client.schema_flags == [True, False]
    run(go())


@pytest.mark.parametrize("exc, kind", [
    (StatusError(500), "transient"),
    (StatusError(503), "transient"),
    (StatusError(429), "transient"),
    (APIConnectionError("reset"), "transient"),
    (TimeoutError(), "transient"),
    (StatusError(401), "fatal"),
    (StatusError(403), "fatal"),
    (StatusError(404), "fatal"),
    # About this request, not the provider: one leaf's context overflowing, say.
    (StatusError(400), "rejected"),
    (StatusError(413), "rejected"),
    (StatusError(422), "rejected"),
])
def test_errors_are_classified(exc, kind):
    async def go():
        # Mode fixed to plain, so the error comes straight from the one request.
        s = _session(FakeClient(exc))
        s.mode = "plain"
        with pytest.raises(RewriteError) as info:
            await s.rewrite(["t"], [])
        assert info.value.kind == kind
    run(go())


def test_a_transient_error_while_probing_leaves_the_mode_unknown():
    async def go():
        s = _session(FakeClient(StatusError(503)))
        with pytest.raises(RewriteError) as info:
            await s.rewrite(["t"], [])
        assert info.value.kind == "transient" and s.mode is None
    run(go())


def test_a_miscount_is_a_mismatch_never_a_partial_answer():
    async def go():
        s = _session(FakeClient(_arr("only one")))
        with pytest.raises(RewriteError) as info:
            await s.rewrite(["t1", "t2"], [])
        assert info.value.kind == "mismatch"
    run(go())


def test_running_out_of_tokens_is_fatal_with_a_hint():
    async def go():
        s = _session(FakeClient(_resp("<think>long reasoning", "length")))
        s.mode = "plain"
        with pytest.raises(RewriteError) as info:
            await s.rewrite(["t"], [])
        assert info.value.kind == "fatal" and "max tokens" in str(info.value)
    run(go())


def test_the_budget_stops_before_a_request_is_sent():
    async def go():
        client = FakeClient("", _arr("a"))
        s = _session(client, max_requests=1)
        # The probe's plain request would be the second: refused before it is sent.
        with pytest.raises(BudgetExhausted):
            await s.rewrite(["t"], [])
        assert s.requests == 1 and len(client.calls) == 1
    run(go())


def test_the_prompt_carries_the_templates_in_order_and_the_descriptors():
    async def go():
        client = FakeClient(_arr("a", "b"))
        s = _session(client, system_instructions="always end with 'cinematic'")
        await s.rewrite(["first", "second"], ['character "Aria": a red-haired knight'])
        system, user = (m["content"] for m in client.calls[0]["messages"])
        assert user.index("1. first") < user.index("2. second")
        assert 'character "Aria": a red-haired knight' in user
        assert "EXACTLY 2 prompts" in system
        assert system.index("cinematic") < system.index("EXACTLY 2 prompts")
    run(go())


def test_standing_instructions_precede_the_format_mandate_in_every_call():
    rule = "one prompt per line"
    outline = prompt_generator._outline_system(rule)
    assert outline.index(rule) < outline.index("return ONE JSON object")
    assert outline.endswith(prompt_generator._OUTLINE_SYSTEM)
    assert prompt_generator._outline_system("") == prompt_generator._OUTLINE_SYSTEM

    prompts = prompt_generator._with_standing(prompt_generator._SYSTEM.format(count=3), rule, "lead:")
    assert prompts.startswith(f"lead:\n{rule}\n\n") and prompts.index(rule) < prompts.index("EXACTLY 3")


def test_a_reply_cut_off_at_the_token_limit_is_never_a_prompt():
    # One template, and the cut-off JSON is one line: line splitting would accept it.
    async def go():
        cut = '{"prompts": ["An iron knight stands in the'
        s = _session(FakeClient(_resp(cut, "length")))
        s.mode = "plain"
        with pytest.raises(RewriteError) as info:
            await s.rewrite(["t"], [])
        assert info.value.kind == "fatal"
        # Even without the finish reason, it does not parse as an answer.
        s = _session(FakeClient(_resp(cut)))
        s.mode = "plain"
        with pytest.raises(RewriteError) as info:
            await s.rewrite(["t"], [])
        assert info.value.kind == "mismatch"
    run(go())


@pytest.mark.parametrize("raw", [
    '{"rewritten": ["a"]}',
    '{"prompts": "a single string"}',
    '{"prompts": [""]}',
    "a plain line of prose",
])
def test_an_answer_that_is_not_a_json_list_of_prompts_is_a_mismatch(raw):
    async def go():
        s = _session(FakeClient(raw))
        s.mode = "plain"
        with pytest.raises(RewriteError) as info:
            await s.rewrite(["t"], [])
        assert info.value.kind == "mismatch"
    run(go())


def test_parse_rewrite_finds_the_list_in_prose_full_of_placeholders():
    raw = 'Sure, for {character} in {biome}:\n```json\n["a knight", "a witch"]\n``` done'
    assert prompt_generator.parse_rewrite(raw) == ["a knight", "a witch"]
    assert prompt_generator.parse_rewrite('<think>{"prompts": ["x"]}</think>{"prompts": ["y"]}') == ["y"]


def test_a_refusal_alone_never_fixes_the_mode():
    # A 400 for a leaf whose context overflows: the plain request fails the same way,
    # and the next leaf probes again rather than losing the schema for the whole run.
    async def go():
        client = FakeClient(StatusError(400), StatusError(400), _arr("a"))
        s = _session(client)
        with pytest.raises(RewriteError) as info:
            await s.rewrite(["t"], [])
        assert info.value.kind == "rejected" and s.mode is None
        assert await s.rewrite(["t"], []) == ["a"]
        assert s.mode == "schema" and client.schema_flags == [True, False, True]
    run(go())
