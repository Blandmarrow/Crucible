"""Text-only LLM prompt generation via an OpenAI-compatible provider.

Used by the ComfyUI queue's "Generate prompts" feature. Sibling of
ml/openai_compat_captioner.py but without image input; not tracked by
model_manager (no local VRAM involved).

Diversity strategy: callers generate in small batches and pass everything
already generated/queued as `existing` — the LLM is explicitly instructed to
diverge from it. One big single-call list collapses into near-duplicates.
"""
import json
import logging
import re
from typing import NamedTuple

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You generate image-generation prompts (for Stable Diffusion / ComfyUI). "
    "Return EXACTLY {count} prompts as a JSON array of strings — no markdown, no "
    "commentary, no numbering, no code fences. Each array element is one complete, "
    "self-contained prompt. Make the prompts clearly distinct from each other in "
    "subject, setting, composition, and mood."
)

# Fallback line parsing: leading list markers "1.", "2)", "-", "*", "•"
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
# Conversational openers. High precision by construction: every single-word
# opener must be followed by whitespace/end (optionally after punctuation), so
# "sure-footed mountain goat on a ridge" is NOT chatter — a bare \b after
# "sure" would match the hyphen and silently eat a real prompt.
_COMMENTARY_RE = re.compile(
    r"^(?:"
    r"(?:sure|certainly|absolutely|of course|got it|understood)[,.!:]?(?:\s|$)"
    r"|here (?:are|is|you go)\b"
    r"|below (?:are|is)\b"
    r"|i(?:'ve| have) (?:generated|created|written|made|prepared)\b"
    r"|let me know\b"
    r"|hope (?:these|this|that)\b"
    r"|note:"
    r")",
    re.IGNORECASE,
)
# Self-referential lines about the output itself ("These prompts vary in mood as
# requested."). Both branches require the word "prompt(s)" to co-occur — a bare
# leading "these" would reject "these towering cliffs at dawn, volumetric light".
# Applied ONLY to the first and last surviving line (see _split_lines): a
# preamble or a sign-off, never a legitimate prompt in the middle of a list.
_META_LINE_RE = re.compile(
    r"^(?:these|those|the above|all|each)\b[^\n]{0,120}?\bprompts?\b"
    r"|\bprompts?\b[^\n]{0,120}?\b(?:as requested|as you asked|above|listed)\b",
    re.IGNORECASE,
)


class ParsedPrompts(NamedTuple):
    """`prompts` plus how many lines the fallback dropped as chatter/meta.

    The count is reported (`result_data["filtered"]`) rather than discarded
    because a background job inserts rows directly — there is no review
    textarea in which an over-filter would be visible.
    """

    prompts: list[str]
    filtered: int


def _split_lines(text: str) -> ParsedPrompts:
    """Line-splitting fallback: one prompt per line, chatter removed.

    Over- and under-filtering are NOT symmetric here. A surviving junk line
    becomes a queue row that may be rendered on GPU time; an over-filtered line
    is a paid generation silently thrown away. Both regexes are therefore
    precision-first, and neither is applied to the JSON branch — a JSON array
    element is a deliberate unit, and filtering there would make the reliable
    path lossy.
    """
    prompts: list[str] = []
    filtered = 0
    for line in text.splitlines():
        line = _LIST_MARKER_RE.sub("", line).strip().strip("\"'")
        if not line or line.startswith("```"):
            continue
        if _COMMENTARY_RE.match(line):
            logger.info("prompt_generator: dropped commentary line %r", line[:120])
            filtered += 1
            continue
        prompts.append(line)
    for edge in ("first", "last"):
        if not prompts:
            break
        idx = 0 if edge == "first" else -1
        if _META_LINE_RE.search(prompts[idx]):
            logger.info("prompt_generator: dropped %s-line meta text %r", edge, prompts[idx][:120])
            filtered += 1
            prompts.pop(idx)
    return ParsedPrompts(prompts, filtered)


def _strip_reasoning(raw: str) -> str:
    """The answer without inline <think> blocks or a wrapping markdown fence."""
    text = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw, flags=re.DOTALL)
    # Unclosed <think> = the model got truncated mid-reasoning; the answer never
    # arrived. Drop everything from the tag on rather than parsing reasoning as output.
    text = re.sub(r"<think(?:ing)?>.*", "", text, flags=re.DOTALL).strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    return text


def parse_prompts(raw: str) -> ParsedPrompts:
    """Parse LLM output into prompts + a dropped-line count.

    Strips inline <think> blocks (thinking models that don't separate
    reasoning_content), then prefers JSON — either a bare array of strings or
    the structured-output shape {"prompts": [...]}, possibly wrapped in
    markdown fences or surrounding prose — falling back to line splitting with
    list-marker and commentary stripping.
    """
    text = _strip_reasoning(raw)
    candidate = text
    if not candidate.startswith(("[", "{")):
        m = re.search(r"[\[{].*[\]}]", candidate, re.DOTALL)
        candidate = m.group(0) if m else candidate
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            parsed = parsed.get("prompts")
        if isinstance(parsed, list):
            return ParsedPrompts([str(p).strip() for p in parsed if str(p).strip()], 0)
    except ValueError:
        pass
    return _split_lines(text)


async def generate_prompts(
    base_url: str,
    api_key: str,
    model_name: str,
    instruction: str,
    batch_size: int,
    system_instructions: str = "",
    existing: list[str] | None = None,
    temperature: float = 0.9,
    max_tokens: int = 2048,
    timeout_s: float = 300.0,
) -> ParsedPrompts:
    """One chat-completion call → one batch of prompts. Raises on provider errors.

    `system_instructions` are the user's standing rules for HOW prompts are
    written (style, format, constraints) — sent in the system message, before
    the output-format mandate so the mandate wins on conflict. `instruction` is
    the per-call ask (WHAT to generate). `existing` prompts are anti-similarity
    context: the model is told they already exist and to diverge.
    """
    from openai import AsyncOpenAI

    system = _SYSTEM.format(count=batch_size)
    if system_instructions.strip():
        system = (
            "Follow these standing instructions for how every prompt must be written:\n"
            f"{system_instructions.strip()}\n\n{system}"
        )

    user = f"Generate {batch_size} prompts.\n\nRequest: {instruction.strip()}"
    if existing:
        joined = "\n".join(f"- {e}" for e in existing[-40:])
        user += (
            "\n\nThese prompts ALREADY EXIST. Match their general style and format, but make "
            f"the new prompts clearly different from all of them in subject, setting, and "
            f"composition:\n{joined}"
        )

    # max_retries=0: the SDK default of 2 retries timeouts, making the configured
    # timeout a silent 3x ceiling. Losing automatic 429/5xx backoff is acceptable —
    # a failed generation surfaces its error and the user re-runs it.
    client = AsyncOpenAI(base_url=base_url, api_key=api_key or "none", timeout=timeout_s, max_retries=0)
    kwargs = dict(
        model=model_name,
        # Thinking models (Qwen3, R1, o-series) spend most of the budget on
        # reasoning tokens before the answer — the provider's captioning-tuned
        # max_tokens (default 2048) truncates them mid-think, so give headroom.
        max_tokens=max(max_tokens, 8192),
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    # Decoder-level structure enforcement: the model cannot emit prose/numbering.
    # Not universally reliable — providers may reject response_format, and some
    # local-server + thinking-model combos (LM Studio + Qwen3) return EMPTY
    # content under a schema constraint — so any failure (error or nothing
    # parseable) falls back to one plain call, where the prompt-level JSON
    # mandate + parse_prompts still apply.
    schema = {
        "name": "prompt_list",
        "schema": {
            "type": "object",
            "properties": {"prompts": {"type": "array", "items": {"type": "string"}}},
            "required": ["prompts"],
            "additionalProperties": False,
        },
    }

    def _extract(resp) -> tuple[ParsedPrompts, str | None]:
        choice = resp.choices[0] if resp.choices else None
        raw = (choice.message.content or "") if choice else ""
        return parse_prompts(raw), (choice.finish_reason if choice else None)

    parsed = ParsedPrompts([], 0)
    finish_reason: str | None = None
    try:
        resp = await client.chat.completions.create(
            **kwargs, response_format={"type": "json_schema", "json_schema": schema}
        )
        parsed, finish_reason = _extract(resp)
    except Exception as e:
        logger.info("prompt_generator: json_schema attempt failed (%s) — falling back to plain", e)
    if not parsed.prompts:
        resp = await client.chat.completions.create(**kwargs)
        parsed, finish_reason = _extract(resp)

    if not parsed.prompts and finish_reason == "length":
        raise RuntimeError(
            "the model ran out of tokens before finishing (thinking model?) — "
            "raise the provider's max tokens in Settings → LLM Providers"
        )
    logger.info(
        "prompt_generator: model %s returned %d prompts (%d lines filtered)",
        model_name, len(parsed.prompts), parsed.filtered,
    )
    return parsed


# ── Blueprint outline (M6 §2b, docs/dev/comfy-structured.md) ──────────────────

_OUTLINE_SYSTEM = (
    "You design structured image datasets. From the user's brief, return ONE JSON object "
    "(no markdown, no commentary) describing:\n"
    "- axes: the kinds of entity the brief varies over (e.g. character, biome). Each axis has a "
    "name (lowercase letters, digits, _) and entities. Each entity has an id (short, lowercase, "
    "unique on its axis), a label (a short display name, which becomes a folder name) and a "
    "descriptor: a concrete visual description that will be pasted VERBATIM into every prompt "
    "that names it, so it must identify the entity on its own and stay consistent.\n"
    "- variations: sets of interchangeable prompt pieces that vary the shots without changing "
    "who or where (e.g. camera: low angle, wide shot; time: dawn, dusk). Each has a name "
    "(lowercase letters, digits, _) and pieces.\n"
    "- scenarios: how to combine axes. Each has a name; combine (a list of items, each "
    '{"axis": name, "take": N} where take 1 means one entity at a time and take 2 means pairs '
    "of distinct entities together); mode (\"full\" for every combination, or \"sample:K\" for K "
    "of them); count (images per folder); path (folder pattern using {axis} placeholders, e.g. "
    '"{biome}/{character}"); and template (the prompt pattern, using {axis} for descriptors, '
    "{axis.label} for labels and {variation} for one piece).\n"
    "Invent the entities; never enumerate the combinations yourself. Use only the placeholders "
    "of axes a scenario combines and of variations you defined."
)

# Axes and variations as *lists* of named entries: a dict with free-form keys is awkward
# to constrain with a strict JSON schema. parse_outline turns them into the spec's dicts.
_OUTLINE_SCHEMA = {
    "name": "blueprint_outline",
    "schema": {
        "type": "object",
        "properties": {
            "axes": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "entities": {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"}, "label": {"type": "string"},
                            "descriptor": {"type": "string"},
                        },
                        "required": ["id", "label", "descriptor"],
                        "additionalProperties": False,
                    }},
                },
                "required": ["name", "entities"],
                "additionalProperties": False,
            }},
            "variations": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "pieces": {"type": "array", "items": {"type": "string"}}},
                "required": ["name", "pieces"],
                "additionalProperties": False,
            }},
            "scenarios": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "combine": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"axis": {"type": "string"}, "take": {"type": "integer"}},
                        "required": ["axis", "take"],
                        "additionalProperties": False,
                    }},
                    "mode": {"type": "string"},
                    "count": {"type": "integer"},
                    "path": {"type": "string"},
                    "template": {"type": "string"},
                },
                "required": ["name", "combine", "mode", "count", "path", "template"],
                "additionalProperties": False,
            }},
        },
        "required": ["axes", "variations", "scenarios"],
        "additionalProperties": False,
    },
}


def _named_entries(value, inner: str) -> dict | None:
    """`[{name, <inner>}]` → `{name: <inner>}`; a dict passes through, null is empty.

    Two entries with one name are merged rather than the second replacing the first:
    a model that splits an axis in two would otherwise lose half its entities from a
    draft reported as a success.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if not isinstance(value, list):
        return None
    out: dict[str, list] = {}
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            entries = item.get(inner) or []
            out.setdefault(item["name"].strip(), []).extend(entries if isinstance(entries, list) else [entries])
    return out


def _outline_from(parsed) -> dict | None:
    if isinstance(parsed, dict) and isinstance(parsed.get("blueprint"), dict):
        parsed = parsed["blueprint"]
    if not isinstance(parsed, dict) or not ({"axes", "scenarios"} & parsed.keys()):
        return None
    axes = _named_entries(parsed.get("axes"), "entities")
    variations = _named_entries(parsed.get("variations"), "pieces")
    scenarios = parsed.get("scenarios") or []
    if axes is None or variations is None or not isinstance(scenarios, list):
        return None
    # An outline with nothing in it is no draft. Counting it as one would report
    # "draft ready" and let *Use it* replace the user's spec with an empty one (PM-004).
    if not axes and not scenarios:
        return None
    return {"axes": axes, "variations": variations, "scenarios": scenarios}


def parse_outline(raw: str) -> dict | None:
    """An outline response as a BlueprintSpec-shaped dict, or None when it holds none.

    Accepts the list form the schema asks for and the spec's own dict form, a
    `{"blueprint": {...}}` wrapper, <think> blocks, fences and surrounding prose.
    Structural validation is the caller's (`BlueprintSpec.model_validate`).

    Prose is scanned with `raw_decode` at each "{" rather than cut from the first
    "{" to the last "}": prose in this domain is full of `{axis}` placeholders, and
    "a set for {character} in {biome}: {...}" would make that slice invalid JSON.
    """
    text = _strip_reasoning(raw)
    decoder = json.JSONDecoder()
    for start in [i for i, ch in enumerate(text) if ch == "{"]:
        try:
            parsed, _ = decoder.raw_decode(text, start)
        except ValueError:
            continue
        outline = _outline_from(parsed)
        if outline is not None:
            return outline
    return None


async def generate_outline(
    base_url: str,
    api_key: str,
    model_name: str,
    brief: str,
    system_instructions: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout_s: float = 300.0,
) -> dict:
    """One outline call: a brief → a draft blueprint dict. Raises when none comes back.

    The brief goes in the **user** message with the instruction to keep descriptors
    usable verbatim. `system_instructions` are the plan's standing rules for how
    prompts are written; they shape the templates and are never written to here.
    Same structured-then-plain fallback as `generate_prompts`, for the same reason.
    """
    from openai import AsyncOpenAI

    system = _OUTLINE_SYSTEM
    if system_instructions.strip():
        system = (
            f"{system}\n\nThe prompts the templates produce must follow these standing "
            f"instructions:\n{system_instructions.strip()}"
        )
    user = (
        f"Brief: {brief.strip()}\n\n"
        "Write each descriptor so it can be pasted verbatim into any prompt that names it."
    )

    client = AsyncOpenAI(base_url=base_url, api_key=api_key or "none", timeout=timeout_s, max_retries=0)
    kwargs = dict(
        model=model_name,
        max_tokens=max(max_tokens, 8192),
        temperature=temperature,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )

    def _extract(resp) -> tuple[dict | None, str | None]:
        choice = resp.choices[0] if resp.choices else None
        raw = (choice.message.content or "") if choice else ""
        return parse_outline(raw), (choice.finish_reason if choice else None)

    outline: dict | None = None
    finish_reason: str | None = None
    try:
        resp = await client.chat.completions.create(
            **kwargs, response_format={"type": "json_schema", "json_schema": _OUTLINE_SCHEMA}
        )
        outline, finish_reason = _extract(resp)
    except Exception as e:
        logger.info("prompt_generator: outline json_schema attempt failed (%s) — falling back to plain", e)
    if outline is None:
        resp = await client.chat.completions.create(**kwargs)
        outline, finish_reason = _extract(resp)

    if outline is None:
        if finish_reason == "length":
            raise RuntimeError(
                "the model ran out of tokens before finishing (thinking model?) — "
                "raise the provider's max tokens in Settings → LLM Providers"
            )
        raise RuntimeError("the model did not return a blueprint as JSON")
    return outline
