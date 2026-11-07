"""The pure blueprint expansion (services/blueprint_expand.py): combinatorics, paths,
templates, the template walk and the fill arithmetic. No DB."""
import pytest

from backend.schemas.comfy_blueprint import BlueprintSpec
from backend.services.blueprint_expand import (
    HARD_ROW_CAP,
    MAX_COMBINATIONS,
    MAX_ERRORS,
    _walk_index,
    compose,
    expand,
    plan_fill,
)


def _spec(**over):
    base = {
        "axes": {
            "character": [
                {"id": "k", "label": "Iron Knight", "descriptor": "a tall knight"},
                {"id": "w", "label": "Salt Witch", "descriptor": "a grey witch"},
                {"id": "m", "label": "Monk", "descriptor": ""},
            ],
            "biome": [{"id": "fen", "label": "Salt Fen", "descriptor": "a brackish fen"}],
        },
        "variations": {"camera": ["low angle", "wide shot", "close-up"], "time": ["dawn", "dusk"]},
        "scenarios": [{
            "name": "solo", "combine": ["character", "biome"], "count": 4,
            "path": "{biome}/{character}", "template": "{character}, in {biome}, {camera}, {time}",
        }],
    }
    base.update(over)
    return BlueprintSpec.model_validate(base)


def _scenario(**over):
    sc = {"name": "s", "combine": ["character", "biome"], "count": 2, "path": "{biome}/{character}"}
    sc.update(over)
    return sc


def test_full_cross_product_paths_targets_and_entities():
    e = expand(_spec(), 1)
    assert e.errors == []
    assert [l.path for l in e.leaves] == ["salt_fen/iron_knight", "salt_fen/salt_witch", "salt_fen/monk"]
    assert all(l.target == 4 for l in e.leaves)
    assert e.leaves[0].entity_ids == {"character": ["k"], "biome": ["fen"]}
    assert e.leaves[0].product == 6


def test_take_is_unordered_distinct_combinations_joined_with_and():
    spec = _spec(scenarios=[_scenario(combine=[{"axis": "character", "take": 2}, "biome"],
                                      path="{biome}/pairs/{character}", template="{character} together")])
    e = expand(spec, 1)
    assert e.errors == []
    assert [l.path for l in e.leaves] == [
        "salt_fen/pairs/iron_knight_and_salt_witch",
        "salt_fen/pairs/iron_knight_and_monk",
        "salt_fen/pairs/salt_witch_and_monk",
    ]
    # Descriptors join with "and"; an empty descriptor falls back to the label.
    assert compose(e.leaves[1], 0, 1) == ["a tall knight and Monk together"]


def test_three_descriptors_join_with_commas_and_a_final_and():
    spec = _spec(scenarios=[_scenario(combine=[{"axis": "character", "take": 3}], path="trio/{character}",
                                      template="{character}")])
    [leaf] = expand(spec, 1).leaves
    assert compose(leaf, 0, 1) == ["a tall knight, a grey witch and Monk"]


def test_label_placeholder_and_default_template():
    spec = _spec(scenarios=[_scenario(template="{character.label} at {biome.label}"), ])
    assert compose(expand(spec, 1).leaves[0], 0, 1) == ["Iron Knight at Salt Fen"]
    spec = _spec(scenarios=[_scenario(template=None)])
    [prompt] = compose(expand(spec, 1).leaves[0], 0, 1)
    # The default names every variation, so one defined is never silently unused.
    head, camera, time = prompt.rsplit(", ", 2)
    assert head == "a tall knight, a brackish fen"
    assert camera in ("low angle", "wide shot", "close-up") and time in ("dawn", "dusk")


def test_a_blank_path_is_one_folder_level_per_combined_axis():
    e = expand(_spec(scenarios=[_scenario(combine=["biome", "character"], path="")]), 1)
    assert e.errors == []
    assert [l.path for l in e.leaves] == ["salt_fen/iron_knight", "salt_fen/salt_witch", "salt_fen/monk"]
    # Whitespace and bare slashes are blank too.
    e = expand(_spec(scenarios=[_scenario(path=" / ")]), 1)
    assert [l.path for l in e.leaves][:1] == ["iron_knight/salt_fen"]  # combine order


def test_sample_is_stable_across_processes():
    """A hard-coded expectation, not a comparison of two calls: a seed built from
    Python's per-process `hash()` would pass a same-process test and still re-expand
    differently after every restart."""
    ents = [{"id": f"c{i}", "label": f"Char {i}"} for i in range(6)]
    spec = BlueprintSpec.model_validate({
        "axes": {"character": ents}, "variations": {"cam": ["a", "b", "c", "d"]},
        "scenarios": [{"name": "pairs", "combine": [{"axis": "character", "take": 2}], "mode": "sample:4",
                       "count": 2, "path": "pairs/{character}", "template": "{character}, {cam}"}],
    })
    e = expand(spec, 7)
    assert [l.path for l in e.leaves] == [
        "pairs/char_1_and_char_2", "pairs/char_1_and_char_4", "pairs/char_1_and_char_5", "pairs/char_2_and_char_3",
    ]
    assert compose(e.leaves[0], 0, 4) == [
        "Char 1 and Char 2, d", "Char 1 and Char 2, c", "Char 1 and Char 2, b", "Char 1 and Char 2, a",
    ]


def test_renaming_a_scenario_changes_neither_its_sample_nor_its_walk():
    """The leaf is its folder: a cosmetic rename must not re-draw folders (orphaning
    the old ones) or re-seed walks (repeating prompts past the stored cursor)."""
    ents = [{"id": f"c{i}", "label": f"Char {i}"} for i in range(6)]

    def spec(name):
        return BlueprintSpec.model_validate({
            "axes": {"character": ents}, "variations": {"cam": ["a", "b", "c", "d"]},
            "scenarios": [{"name": name, "combine": [{"axis": "character", "take": 2}], "mode": "sample:4",
                           "count": 2, "path": "pairs/{character}", "template": "{character}, {cam}"}],
        })

    a, b = expand(spec("pairs"), 7), expand(spec("duos"), 7)
    assert [l.path for l in a.leaves] == [l.path for l in b.leaves]
    assert [compose(l, 0, 4) for l in a.leaves] == [compose(l, 0, 4) for l in b.leaves]


def test_adding_an_entity_keeps_most_of_a_sample():
    ents = [{"id": f"c{i}", "label": f"Char {i}"} for i in range(8)]

    def paths(n):
        return {l.path for l in expand(BlueprintSpec.model_validate({
            "axes": {"character": ents[:n]},
            "scenarios": [{"name": "s", "combine": [{"axis": "character", "take": 2}], "mode": "sample:6",
                           "count": 1, "path": "pairs/{character}"}],
        }), 3).leaves}

    before, after = paths(7), paths(8)
    # Ranking by a hash of the ids: a new entity's pairs can displace some, never reshuffle all.
    assert len(before & after) >= 3


def test_sample_larger_than_the_product_keeps_everything():
    spec = _spec(scenarios=[_scenario(mode="sample:50")])
    assert len(expand(spec, 1).leaves) == 3


def test_a_different_seed_changes_the_walk_not_the_leaves():
    a, b = expand(_spec(), 1), expand(_spec(), 2)
    assert [l.path for l in a.leaves] == [l.path for l in b.leaves]
    assert [compose(l, 0, 6) for l in a.leaves] != [compose(l, 0, 6) for l in b.leaves]


@pytest.mark.parametrize("product", [1, 2, 3, 4, 6, 9, 12, 30, 97])
def test_walk_is_a_bijection_per_cycle(product):
    for seed in (0, 1, 12345, 2**63 - 1):
        idx = [_walk_index(i, product, seed) for i in range(product)]
        assert sorted(idx) == list(range(product))
        # The next cycle repeats it.
        assert [_walk_index(i + product, product, seed) for i in range(product)] == idx


def test_walk_prefix_is_stable_so_top_up_continues_it():
    leaf = expand(_spec(), 3).leaves[0]
    first = compose(leaf, 0, 6)
    assert compose(leaf, 0, 2) + compose(leaf, 2, 4) == first
    assert len(set(first)) == 6


def test_repeat_warning_when_count_exceeds_product():
    e = expand(_spec(scenarios=[_scenario(count=7, template="{character}, {camera}, {time}")]), 1)
    assert e.errors == []
    assert any("only 6 distinct prompts" in w for w in e.warnings)
    assert expand(_spec(), 1).warnings == []


@pytest.mark.parametrize("over, fragment", [
    ({"scenarios": [_scenario(combine=["nope"])]}, "unknown axis 'nope'"),
    ({"scenarios": [_scenario(combine=[])]}, "combines no axes"),
    ({"scenarios": [_scenario(combine=["character", "character"], path="{character}")]}, "same axis twice"),
    ({"scenarios": [_scenario(combine=[{"axis": "biome", "take": 2}], path="{biome}")]}, "has only 1"),
    ({"scenarios": [_scenario(mode="some")]}, "use 'full' or 'sample:K'"),
    ({"scenarios": [_scenario(mode="sample:0")]}, "use 'full' or 'sample:K'"),
    ({"scenarios": [_scenario(template="{character}, {lighting}")]}, "'{lighting}'"),
    ({"scenarios": [_scenario(combine=["character"], path="{character}", template="{biome}")]}, "does not combine"),
    ({"scenarios": [_scenario(path="{biome}//{character}")]}, "empty folder name"),
    ({"scenarios": [_scenario(path="../{character}")]}, "may not use '..'"),
    ({"scenarios": [_scenario(path="{biome}/{mood}")]}, "'{mood}'"),
    ({"scenarios": [_scenario(path="{biome}")]}, "is made by both"),
    ({"scenarios": [_scenario(), _scenario()]}, "Two scenarios are named 's'"),
    ({"scenarios": [_scenario(name=" ")]}, "has no name"),
    ({"axes": {"character": [{"id": "a", "label": "!!!"}], "biome": [{"id": "b", "label": "Fen"}]}},
     "needs a label with a letter or digit"),
    # "_" passes a `\w` test and still slugs to "image".
    ({"axes": {"character": [{"id": "a", "label": "-_-"}], "biome": [{"id": "b", "label": "Fen"}]}},
     "needs a label with a letter or digit"),
    ({"axes": {"character": [{"id": "a", "label": "Knight"}, {"id": "a", "label": "Witch"}],
               "biome": [{"id": "b", "label": "Fen"}]}}, "two entities with id 'a'"),
    ({"axes": {"character": [{"id": "a", "label": "Iron Knight"}, {"id": "b", "label": "iron-knight"}],
               "biome": [{"id": "c", "label": "Fen"}]}}, "make the same folder name 'iron_knight'"),
    ({"axes": {"character": [], "biome": [{"id": "b", "label": "Fen"}]}}, "has no entities"),
    ({"axes": {"Main Character": [{"id": "a", "label": "A"}]}}, "Axis name 'Main Character'"),
    ({"variations": {"camera": ["  "], "time": ["dawn"]}}, "Variation 'camera' has no pieces"),
    ({"variations": {"biome": ["x"], "camera": ["a"], "time": ["b"]}}, "both an axis and a variation"),
])
def test_validation_errors(over, fragment):
    e = expand(_spec(**over), 1)
    assert e.leaves == []
    assert any(fragment in err for err in e.errors), e.errors


def test_oversized_scenario_is_an_error_not_a_truncation():
    ents = [{"id": f"c{i}", "label": f"C{i}"} for i in range(200)]
    spec = BlueprintSpec.model_validate({
        "axes": {"a": ents},
        "scenarios": [{"name": "s", "combine": [{"axis": "a", "take": 4}], "mode": "sample:5", "path": "{a}"}],
    })
    e = expand(spec, 1)
    assert e.leaves == []
    assert any(f"{MAX_COMBINATIONS:,}" in err for err in e.errors)


def test_too_many_leaves_is_an_error():
    a = [{"id": f"a{i}", "label": f"A{i}"} for i in range(200)]
    b = [{"id": f"b{i}", "label": f"B{i}"} for i in range(200)]
    spec = BlueprintSpec.model_validate({
        "axes": {"a": a, "b": b},
        "scenarios": [{"name": "s", "combine": ["a", "b"], "count": 1, "path": "{a}/{b}"}],
    })
    e = expand(spec, 1)
    assert e.leaves == []
    assert any(f"{HARD_ROW_CAP:,}" in err for err in e.errors)


def test_the_combination_budget_covers_the_whole_blueprint():
    """One scenario under the limit is fine; many of them together are not — the
    preview runs on every edit, so the total work is what has to be bounded."""
    ents = [{"id": f"c{i}", "label": f"C{i}"} for i in range(200)]
    one = {"name": "s0", "combine": [{"axis": "a", "take": 2}], "mode": "sample:5", "path": "s0/{a}"}
    spec = BlueprintSpec.model_validate({"axes": {"a": ents}, "scenarios": [one]})
    assert expand(spec, 1).errors == []  # C(200,2) = 19,900
    many = [{**one, "name": f"s{i}", "path": f"s{i}/{{a}}"} for i in range(6)]  # 119,400
    e = expand(BlueprintSpec.model_validate({"axes": {"a": ents}, "scenarios": many}), 1)
    assert e.leaves == []
    assert any("in all" in err for err in e.errors)


def test_the_error_list_is_bounded():
    """Typing `{a}` on the way to `{a}/{b}` makes every leaf a duplicate."""
    a = [{"id": f"a{i}", "label": f"A{i}"} for i in range(60)]
    b = [{"id": f"b{i}", "label": f"B{i}"} for i in range(60)]
    e = expand(BlueprintSpec.model_validate({
        "axes": {"a": a, "b": b},
        "scenarios": [{"name": "s", "combine": ["a", "b"], "path": "{a}"}],
    }), 1)
    assert len(e.errors) == MAX_ERRORS + 1
    assert e.errors[-1].startswith("…and ")


@pytest.mark.parametrize("target, kept, open_rows, yield_, expected", [
    (10, 0, 0, 1.0, 10),   # fresh leaf
    (10, 10, 0, 1.0, 0),   # full
    (10, 12, 0, 1.0, 0),   # over-full never goes negative
    (10, 6, 0, 1.0, 4),    # four images deleted
    (10, 6, 3, 1.0, 1),    # three pending/failed rows already count
    (10, 0, 0, 4.0, 3),    # batch of 4: three rows make 12 >= 10
    (10, 8, 0, 4.0, 1),
    (10, 4, 1, 4.0, 1),    # 4 kept + 1 open row worth 4 = 8, two short -> one row
    (10, 0, 0, 0.0, 10),   # unknown yield reads as 1
    (3, 0, 0, 1.5, 2),
])
def test_plan_fill(target, kept, open_rows, yield_, expected):
    assert plan_fill(target, kept, open_rows, yield_) == expected
