"""parse_outline (ml/prompt_generator.py): an outline response → a BlueprintSpec dict."""
import json

from backend.ml.prompt_generator import parse_outline, parse_prompts
from backend.schemas.comfy_blueprint import BlueprintSpec

LIST_FORM = {
    "axes": [{"name": "character", "entities": [
        {"id": "knight", "label": "Iron Knight", "descriptor": "a tall knight"}]}],
    "variations": [{"name": "camera", "pieces": ["low angle", "wide shot"]}],
    "scenarios": [{"name": "solo", "combine": [{"axis": "character", "take": 1}], "mode": "full",
                   "count": 4, "path": "{character}", "template": "{character}, {camera}"}],
}


def _check(out):
    assert out["axes"]["character"][0]["label"] == "Iron Knight"
    assert out["variations"] == {"camera": ["low angle", "wide shot"]}
    spec = BlueprintSpec.model_validate(out)
    assert spec.scenarios[0].combine[0].axis == "character"


def test_list_form_becomes_the_spec_dict_form():
    _check(parse_outline(json.dumps(LIST_FORM)))


def test_dict_form_passes_through():
    out = parse_outline(json.dumps({
        "axes": {"character": LIST_FORM["axes"][0]["entities"]},
        "variations": {"camera": ["low angle", "wide shot"]},
        "scenarios": LIST_FORM["scenarios"],
    }))
    _check(out)


def test_think_blocks_fences_wrappers_and_prose():
    body = json.dumps({"blueprint": LIST_FORM})
    _check(parse_outline(f"<think>axes... {{not json}}</think>```json\n{body}\n```"))
    _check(parse_outline(f"Sure! Here is the blueprint:\n{json.dumps(LIST_FORM)}\nHope it helps."))


def test_garbage_and_unrelated_json_are_none():
    assert parse_outline("") is None
    assert parse_outline("I could not do that.") is None
    assert parse_outline("{not json at all}") is None
    assert parse_outline(json.dumps({"prompts": ["a", "b"]})) is None
    assert parse_outline(json.dumps({"axes": "character", "scenarios": []})) is None
    # A truncated thinking model: the answer never arrived.
    assert parse_outline(f"<think>planning {json.dumps(LIST_FORM)}") is None


def test_placeholders_in_surrounding_prose_do_not_break_the_json():
    """Plain-mode models narrate in this domain's own syntax: "{character}"."""
    raw = f"Here is a set for {{character}} in {{biome}}:\n{json.dumps(LIST_FORM)}\nUse {{camera}} freely."
    _check(parse_outline(raw))


def test_null_sections_read_as_empty():
    out = parse_outline(json.dumps({**LIST_FORM, "variations": None}))
    assert out["variations"] == {} and out["axes"]["character"]


def test_an_empty_outline_is_none_not_an_empty_draft():
    assert parse_outline(json.dumps({"axes": [], "variations": [], "scenarios": []})) is None
    assert parse_outline(json.dumps({"axes": {}, "scenarios": None})) is None


def test_two_entries_with_one_name_are_merged():
    split = {**LIST_FORM, "axes": LIST_FORM["axes"] + [{"name": "character", "entities": [
        {"id": "witch", "label": "Salt Witch", "descriptor": "a grey witch"}]}]}
    out = parse_outline(json.dumps(split))
    assert [e["id"] for e in out["axes"]["character"]] == ["knight", "witch"]


def test_parse_prompts_still_strips_reasoning_after_the_refactor():
    assert parse_prompts('<think>x</think>```json\n["a cat", "a dog"]\n```').prompts == ["a cat", "a dog"]
