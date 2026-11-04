# ComfyUI blueprint spec and expansion

**Status: living.** What a blueprint's spec is and how the pure expansion service turns it
into leaves: the `BlueprintSpec` schema, combinations, the blueprint-wide budgets, sampling,
seeds, folder paths, templates and the template walk. Nothing here touches the DB or HTTP.
What the DB keeps, fill accounting, orphans, Apply and Top up are
`docs/dev/comfyui/structured.md`; the outline call that drafts a spec with an LLM is
`docs/dev/comfyui/structured-llm.md`. The family's hub is `docs/dev/comfyui.md`.

## The spec (`backend/schemas/comfy_blueprint.py`)

`BlueprintSpec` holds:
- `axes`: name → entities `{id, label, descriptor}`
- `variations`: name → pieces
- `scenarios`: each with `name`, `combine`, `mode` (`full` | `sample:K`), `count`, `path`,
  `template` and `rewrite` (M6 §3's opt-in prose pass; expansion ignores it, and the outline
  call never sets it)

A `combine` item is a bare axis name (take one) or `{axis, take}`.

The Pydantic model checks **structure and bounds only**. Every list and string is bounded,
because the spec is stored as JSON and returned on every read. Everything that needs the
spec as a whole is the expansion's job, so a draft with mistakes can still be saved and
shown with its errors.

## Expansion (`backend/services/blueprint_expand.py`)

**A pure service: no DB, no HTTP, no `HTTPException`.** `expand(spec, seed)` returns
`Expansion(leaves, errors, warnings)`, and errors are collected rather than raised, so the
review panel shows all of them at once. Any error discards every leaf. One function backs
both the preview and Apply, which is what makes the preview trustworthy.

- **Combinations.** A scenario is `itertools.product` over each item's
  `itertools.combinations(entities, take)`: unordered and distinct. "Alone and together" is
  `take`, not a separate mode.
- **The whole blueprint is counted before anything is enumerated.** `_prepare` validates and
  sizes every scenario with `math.comb`. Then two budgets apply across the blueprint:
  `MAX_COMBINATIONS` (100,000) combinations, and `HARD_ROW_CAP` folders. Only then does
  `_build` enumerate. Either budget exceeded is an error, never a truncation.
  - The budgets are blueprint-wide, not per scenario, because the preview re-expands on
    every debounced edit: 50 scenarios each under a per-scenario limit froze the server for
    seconds.
  - Expansion also runs in an executor (`_compute`), so it never holds the event loop.
- **Errors are capped at `MAX_ERRORS` (50)** plus one "…and N more" line. A half-typed path
  like `{a}` on the way to `{a}/{b}` makes every leaf a duplicate, which once meant 39,800
  error strings in one response.
- **`sample:K` ranks each combination by a hash of its entity ids** and keeps the K
  smallest, in enumeration order.
  - It deliberately does not call `random.sample`: that algorithm may change between Python
    versions.
  - A hash of the ids keeps most of a sample stable when an entity is added.
- **Every derived seed comes from `sub_seed`** (sha256 of the blueprint seed and a
  context), **never `hash()`**. Python randomizes `hash()` per process, so a seed built
  from it would re-expand differently after every restart. A same-process test cannot catch
  this, so `test_sample_is_stable_across_processes` asserts a hard-coded path list instead.
- **No seed includes the scenario's name.** The sample rank is keyed by the path *pattern*
  and the entity ids, and the walk by the rendered folder. Renaming a scenario is cosmetic,
  and must neither re-draw a sample (orphaning folders) nor re-seed a walk past its stored
  `cursor` (repeating prompts). A new path pattern renames every folder anyway, so it
  re-drawing costs nothing extra.
- **Paths.** `{axis}` renders the entity label's `slugify_filename` slug, and a multi-take
  axis joins slugs with `_and_` (`iron_knight_and_salt_witch`). Only combined axes may
  appear.
  - **A blank path is `default_path`: one level per combined axis, in combine order**
    (`{biome}/{character}`). Users expected the path to follow the axes and had to type it
    by hand, so blank no longer means an error. `blueprintKeys.defaultPath` mirrors it for
    the field's placeholder; keep the two in step.
  - **The slug trap:** `slugify_filename` answers `"image"` for a label with no letter or
    digit. As a folder name that is silently wrong, so such a label is an error. The test is
    `[^\W_]`, not `\w`: `"_"` and `"-_-"` pass `\w` and still slug to `"image"`.
  - Two labels on one axis that slug alike are an error, and so is a path two leaves share.
  - So is an empty segment, `.` or `..`, and a path over 512 characters.
- **Templates.**
  - `{axis}` → descriptor. Several entities join with ", " and a final " and ", and an
    empty descriptor falls back to the label.
  - `{axis.label}` → label. `{variation}` → one piece.
  - **A blank template is `default_template`: every combined axis, then every
    variation** (`{biome}, {time}, {angles}`). It once joined only the descriptors, so a
    variation the user had defined was silently unused, and the blueprint looked broken.
    `blueprintKeys.defaultTemplate` mirrors it.
  - An unknown name, or an axis the scenario does not combine, is an error.
- **The template walk.** The variation sets the template names make a product of P
  distinct prompts, indexed in mixed radix. A leaf's prompt *i* uses index
  `(a·i + b) mod P`, with `a` coprime to P and `a`, `b` drawn from the leaf's sub-seed.
  - It is a bijection per cycle, so a leaf's prompts differ from each other.
  - Prompt *i* never changes when more are drawn, which is what lets Top up continue a
    walk rather than repeat it.
  - A scenario whose `count` exceeds P is a **warning** (prompts repeat), not an error:
    repeated prompts still give different images under different seeds.

## Testing

`backend/tests/test_blueprint_expand.py` covers the pure expansion: combinations, paths,
templates, every validation error, the walk's bijection and stable prefix, the
cross-process sample, and the `plan_fill` table. `plan_fill` lives in
`blueprint_expand.py` because it is pure; the rules it encodes are
`docs/dev/comfyui/structured.md` § Fill accounting.
