"""Tests for backend.ml.prompt_generator.parse_prompts.

The line-split fallback feeds rows straight into the ComfyUI queue (the
`comfy_prompts` job), so both directions matter: chatter that survives becomes a
row that may be rendered on GPU time, and an over-filtered line is a paid
generation silently discarded. The "survives" tests below are the ones guarding
the second, quieter failure.
"""

from backend.ml.prompt_generator import parse_prompts


# ── JSON branch (the reliable path — never filtered) ──────────────────────────

def test_bare_json_array():
    assert parse_prompts('["a cat", "a dog"]').prompts == ["a cat", "a dog"]


def test_structured_output_shape():
    assert parse_prompts('{"prompts": ["a cat", "a dog"]}').prompts == ["a cat", "a dog"]


def test_fenced_json():
    raw = '```json\n["a cat", "a dog"]\n```'
    assert parse_prompts(raw).prompts == ["a cat", "a dog"]


def test_json_embedded_in_prose():
    raw = 'Here are 2 prompts:\n["a cat", "a dog"]\nHope these help!'
    assert parse_prompts(raw).prompts == ["a cat", "a dog"]


def test_json_elements_are_never_filtered():
    # A JSON array element is a deliberate unit: commentary/meta filtering is
    # confined to the line-split branch, so this survives verbatim.
    raw = '["These prompts vary in mood as requested.", "a cat"]'
    parsed = parse_prompts(raw)
    assert parsed.prompts == ["These prompts vary in mood as requested.", "a cat"]
    assert parsed.filtered == 0


def test_blank_json_elements_dropped():
    assert parse_prompts('["a cat", "", "  "]').prompts == ["a cat"]


# ── <think> stripping ─────────────────────────────────────────────────────────

def test_closed_think_block_stripped():
    raw = "<think>Let me plan this out.</think>\n[\"a cat\", \"a dog\"]"
    assert parse_prompts(raw).prompts == ["a cat", "a dog"]


def test_unclosed_think_block_drops_everything_after():
    # Truncated mid-reasoning: the answer never arrived, so reasoning must not
    # be parsed as prompts.
    raw = "a cat on a fence\n<think>Now I should consider the lighting and the"
    assert parse_prompts(raw).prompts == ["a cat on a fence"]


# ── Line-split fallback ───────────────────────────────────────────────────────

def test_list_markers_stripped():
    raw = "1. a cat\n2) a dog\n- a bird\n* a fish\n• a frog"
    assert parse_prompts(raw).prompts == ["a cat", "a dog", "a bird", "a fish", "a frog"]


def test_leading_chatter_dropped():
    parsed = parse_prompts("Here are 5 prompts:\na cat\na dog")
    assert parsed.prompts == ["a cat", "a dog"]
    assert parsed.filtered == 1


def test_trailing_meta_line_dropped():
    parsed = parse_prompts("a cat\na dog\nThese prompts vary in mood as requested.")
    assert parsed.prompts == ["a cat", "a dog"]
    assert parsed.filtered == 1


def test_leading_meta_line_dropped():
    parsed = parse_prompts("All prompts below use dramatic lighting.\na cat\na dog")
    assert parsed.prompts == ["a cat", "a dog"]
    assert parsed.filtered == 1


def test_meta_line_only_filtered_at_the_edges():
    # A middle line about "prompts" is a legitimate prompt as far as we can
    # tell — commentary appears as a preamble or a sign-off, not mid-list.
    raw = "a cat\nthese prompts scrawled on a chalkboard, still life\na dog"
    parsed = parse_prompts(raw)
    assert parsed.prompts == [
        "a cat",
        "these prompts scrawled on a chalkboard, still life",
        "a dog",
    ]
    assert parsed.filtered == 0


def test_quotes_and_fences_stripped():
    raw = '```\n"a cat"\n\'a dog\'\n```'
    assert parse_prompts(raw).prompts == ["a cat", "a dog"]


# ── Over-filter guards: ordinary prompts that must NOT be dropped ─────────────

def test_prompt_starting_with_these_survives():
    raw = "these towering cliffs at dawn, volumetric light\na dog"
    parsed = parse_prompts(raw)
    assert "these towering cliffs at dawn, volumetric light" in parsed.prompts
    assert parsed.filtered == 0


def test_prompt_starting_with_sure_hyphenated_survives():
    # "sure\b" would match the hyphen in "sure-footed" and eat this prompt.
    raw = "sure-footed mountain goat on a ridge\na dog"
    parsed = parse_prompts(raw)
    assert "sure-footed mountain goat on a ridge" in parsed.prompts
    assert parsed.filtered == 0


def test_prompt_starting_with_here_noun_survives():
    raw = "heretic monk in candlelit scriptorium\na dog"
    assert "heretic monk in candlelit scriptorium" in parse_prompts(raw).prompts


def test_single_prompt_mentioning_prompts_at_both_edges():
    # Degenerate case: one line that is both first and last. It is dropped once
    # (not twice), and the pop must not index past the emptied list.
    parsed = parse_prompts("These prompts are all in watercolour style.")
    assert parsed.prompts == []
    assert parsed.filtered == 1


def test_empty_input():
    parsed = parse_prompts("")
    assert parsed.prompts == []
    assert parsed.filtered == 0
