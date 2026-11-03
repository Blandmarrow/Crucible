"""Pure expansion of a structured-generation blueprint into leaves and prompts.

No DB, no HTTP, no `HTTPException`: `expand` returns leaves, errors and warnings, and
the caller decides what is a 400. One function backs both the preview and Apply,
which is what makes the preview trustworthy. See docs/dev/comfyui/structured-expansion.md.

The load-bearing idea: the LLM (or the user) invents entities, and this code does
the combinatorics and writes every prompt from a template, so one character reads
the same in every leaf and all N×M leaves exist.
"""
from __future__ import annotations

import hashlib
import heapq
import itertools
import math
import re
from dataclasses import dataclass, field

from backend.schemas.comfy_blueprint import BlueprintSpec, CombineItem, Entity, Scenario
from backend.utils import slugify_filename

# Rows one Apply may add: the default, and the most an explicit override allows.
DEFAULT_ROW_CAP = 2_000
HARD_ROW_CAP = 20_000
# Combinations the whole blueprint may enumerate before sampling. Counted with
# math.comb first, so an oversized blueprint is an error, never a silent truncation.
MAX_COMBINATIONS = 100_000
SAMPLES_PER_LEAF = 3
# Errors returned at most; the rest are summarized in one line.
MAX_ERRORS = 50

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODE_RE = re.compile(r"^sample:(\d+)$")
# Joins the slugs of a multi-take axis in a path segment: iron_knight_and_salt_witch.
_SLUG_JOIN = "_and_"


@dataclass(frozen=True)
class Leaf:
    scenario: str
    path: str
    target: int
    entity_ids: dict[str, list[str]]
    template: str
    # axis -> the entities this leaf combines on it (more than one when take > 1)
    entities: dict[str, tuple[Entity, ...]]
    # The variation sets the template names, in order of first use.
    variations: tuple[tuple[str, tuple[str, ...]], ...]
    # Distinct prompts the template can make (product of the variation set sizes).
    product: int
    walk_seed: int


@dataclass
class Expansion:
    leaves: list[Leaf] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def sub_seed(seed: int, *parts: str) -> int:
    """A stable 64-bit seed derived from the blueprint seed and a context.

    sha256, **never `hash()`**: Python randomizes `hash()` per process, so a seed
    built from it would re-expand differently after every restart.
    """
    digest = hashlib.sha256(":".join([str(seed), *parts]).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _slug(label: str) -> str | None:
    """The folder name for a label, or None when it has none.

    `slugify_filename` answers "image" for a label with no letters or digits — as a
    path segment that is a silently wrong folder, so it is refused here instead. The
    test is a letter or digit, not `\\w`: "_" passes `\\w` and still slugs to "image".
    """
    if not re.search(r"[^\W_]", label):
        return None
    return slugify_filename(label)


def _join_descriptors(ents: tuple[Entity, ...]) -> str:
    texts = [(e.descriptor.strip() or e.label.strip()) for e in ents]
    if len(texts) <= 1:
        return texts[0] if texts else ""
    return ", ".join(texts[:-1]) + " and " + texts[-1]


def default_path(axes: list[str]) -> str:
    """A blank folder path: one level per combined axis, in combine order
    (`{biome}/{character}`). frontend/src/components/comfy/blueprintKeys.ts mirrors it
    for the field's placeholder, so keep the two in step."""
    return "/".join("{" + a + "}" for a in axes)


def default_template(axes: list[str], variations: list[str]) -> str:
    """A blank template: the combined descriptors, then one piece of every variation.
    Every variation, because one the user defined and the default then ignored read as
    a broken blueprint. Mirrored in blueprintKeys.ts, like `default_path`."""
    return ", ".join("{" + n + "}" for n in [*axes, *variations])


def _combine_items(items: list[str | CombineItem]) -> list[tuple[str, int]]:
    return [(i, 1) if isinstance(i, str) else (i.axis, i.take) for i in items]


def expand(spec: BlueprintSpec, seed: int) -> Expansion:
    """Every leaf the blueprint describes, or the reasons it cannot be expanded.

    Errors are collected rather than raised, so the review panel can show all of
    them at once. Any error discards every leaf.
    """
    out = Expansion()
    errors = out.errors

    # Axes: names usable as placeholders, unique ids, and a folder name per label.
    axis_slugs: dict[str, dict[str, str]] = {}
    for axis, ents in spec.axes.items():
        if not _NAME_RE.match(axis):
            errors.append(f"Axis name '{axis}' must be letters, digits and _ (not starting with a digit)")
        if not ents:
            errors.append(f"Axis '{axis}' has no entities")
        ids: set[str] = set()
        slugs: dict[str, str] = {}
        by_slug: dict[str, str] = {}
        for e in ents:
            if not e.id.strip():
                errors.append(f"An entity on axis '{axis}' has no id")
            elif e.id in ids:
                errors.append(f"Axis '{axis}' has two entities with id '{e.id}'")
            ids.add(e.id)
            s = _slug(e.label)
            if s is None:
                errors.append(f"Entity '{e.label or e.id}' on axis '{axis}' needs a label with a letter or digit, to name its folder")
                continue
            if s in by_slug:
                errors.append(f"Labels '{by_slug[s]}' and '{e.label}' on axis '{axis}' make the same folder name '{s}'")
            by_slug.setdefault(s, e.label)
            slugs[e.id] = s
        axis_slugs[axis] = slugs

    for name, pieces in spec.variations.items():
        if not _NAME_RE.match(name):
            errors.append(f"Variation name '{name}' must be letters, digits and _ (not starting with a digit)")
        if name in spec.axes:
            errors.append(f"'{name}' is both an axis and a variation")
        if not [p for p in pieces if p.strip()]:
            errors.append(f"Variation '{name}' has no pieces")

    seen_names: set[str] = set()
    prepared: list[_Prepared] = []
    for sc in spec.scenarios:
        label = sc.name or "(unnamed scenario)"
        if not sc.name.strip():
            errors.append("A scenario has no name")
        elif sc.name in seen_names:
            errors.append(f"Two scenarios are named '{sc.name}'")
        seen_names.add(sc.name)
        p = _prepare(spec, sc, label, errors)
        if p is not None:
            prepared.append(p)

    # Budgets over the whole blueprint, counted before anything is enumerated: the
    # preview runs on every debounced edit, so its work has to be bounded up front.
    combos = sum(p.total for p in prepared)
    if combos > MAX_COMBINATIONS:
        errors.append(
            f"The scenarios have {combos:,} combinations in all; the most allowed before sampling is {MAX_COMBINATIONS:,}"
        )
    folders = sum(min(p.total, p.sample_k) if p.sample_k else p.total for p in prepared)
    if folders > HARD_ROW_CAP:
        errors.append(f"The blueprint makes {folders:,} folders; the most it may make is {HARD_ROW_CAP:,}")
    # Any error discards every leaf, and an axis with a bad label has no slug to
    # render, so stop before enumerating.
    if errors:
        return _finish(out)

    seen_paths: dict[str, str] = {}
    for p in prepared:
        for leaf in _build(spec, p, seed, axis_slugs, out) or []:
            if leaf.path in seen_paths:
                errors.append(f"Folder '{leaf.path}' is made by both '{seen_paths[leaf.path]}' and '{p.label}'")
                continue
            seen_paths[leaf.path] = p.label
            out.leaves.append(leaf)
    return _finish(out)


def _finish(out: Expansion) -> Expansion:
    """Discard the leaves on any error, and bound the error list: one slip while
    typing a path can make every leaf a duplicate — thousands of near-identical lines."""
    if out.errors:
        out.leaves = []
        if len(out.errors) > MAX_ERRORS:
            more = len(out.errors) - MAX_ERRORS
            del out.errors[MAX_ERRORS:]
            out.errors.append(f"…and {more:,} more errors")
    return out


@dataclass
class _Prepared:
    """A scenario that passed validation, counted but not yet enumerated."""

    sc: Scenario
    label: str
    combine: list[tuple[str, int]]
    template: str
    var_names: list[str]
    raw_path: str
    sample_k: int | None
    total: int


def _prepare(spec: BlueprintSpec, sc: Scenario, label: str, errors: list[str]) -> _Prepared | None:
    errors_before = len(errors)
    err = errors.append

    combine = _combine_items(sc.combine)
    if not combine:
        err(f"Scenario '{label}' combines no axes")
    axes_used = [a for a, _ in combine]
    for axis, take in combine:
        if axis not in spec.axes:
            err(f"Scenario '{label}' combines unknown axis '{axis}'")
        elif take > len(spec.axes[axis]):
            err(f"Scenario '{label}' takes {take} from axis '{axis}', which has only {len(spec.axes[axis])}")
    if len(set(axes_used)) != len(axes_used):
        err(f"Scenario '{label}' combines the same axis twice")

    sample_k: int | None = None
    mode = sc.mode.strip()
    if mode != "full":
        m = _MODE_RE.match(mode)
        if not m or int(m.group(1)) < 1:
            err(f"Scenario '{label}' has mode '{sc.mode}'; use 'full' or 'sample:K'")
        else:
            sample_k = int(m.group(1))

    # Template: placeholders name a combined axis, `axis.label`, or a variation.
    template = (sc.template or "").strip() or default_template(axes_used, list(spec.variations))
    var_names: list[str] = []
    for name in _PLACEHOLDER_RE.findall(template):
        base, _, attr = name.partition(".")
        if base in axes_used and attr in ("", "label"):
            continue
        if not attr and base in spec.variations:
            if base not in var_names:
                var_names.append(base)
            continue
        if base in spec.axes and base not in axes_used:
            err(f"Scenario '{label}' template names axis '{base}', which it does not combine")
        else:
            err(f"Scenario '{label}' template names '{{{name}}}', which is not an axis it combines or a variation")

    # Path: literal segments and combined axes only. Blank means one level per axis;
    # with no axes combined it stays blank, and "combines no axes" already says why.
    raw_path = sc.path.strip().strip("/") or default_path(axes_used)
    if raw_path:
        for seg in raw_path.replace("\\", "/").split("/"):
            if not seg.strip():
                err(f"Scenario '{label}' path '{sc.path}' has an empty folder name")
            elif seg.strip() in (".", ".."):
                err(f"Scenario '{label}' path '{sc.path}' may not use '{seg.strip()}'")
        for name in _PLACEHOLDER_RE.findall(raw_path):
            if name not in axes_used:
                err(f"Scenario '{label}' path names '{{{name}}}', which is not an axis it combines")

    if len(errors) > errors_before:
        return None
    total = math.prod(math.comb(len(spec.axes[a]), t) for a, t in combine)
    return _Prepared(sc, label, combine, template, var_names, raw_path, sample_k, total)


def _build(spec: BlueprintSpec, p: _Prepared, seed: int, axis_slugs, out: Expansion) -> list[Leaf] | None:
    sc, label = p.sc, p.label
    variations = tuple(
        (v, tuple(x for x in spec.variations[v] if x.strip())) for v in p.var_names
    )
    product = math.prod(len(x) for _, x in variations)
    if sc.count > product:
        out.warnings.append(
            f"Scenario '{label}': each folder wants {sc.count} images but the template makes only "
            f"{product} distinct prompt{'s' if product != 1 else ''}, so prompts repeat"
        )

    combos = list(itertools.product(*(itertools.combinations(spec.axes[a], t) for a, t in p.combine)))
    if p.sample_k is not None and p.sample_k < len(combos):
        # Rank each combination by a hash of its entity ids rather than calling
        # random.sample: that algorithm may change between Python versions, and a
        # hash of the ids keeps most of the sample when an entity is added. Keyed by
        # the path pattern, not the scenario name, so renaming a scenario changes
        # nothing, while a new pattern (which renames every folder anyway) re-draws.
        def rank(i: int) -> int:
            ids = "|".join(e.id for group in combos[i] for e in group)
            return sub_seed(seed, "sample", p.raw_path, ids)

        chosen = sorted(heapq.nsmallest(p.sample_k, range(len(combos)), key=rank))
        combos = [combos[i] for i in chosen]

    leaves: list[Leaf] = []
    for combo in combos:
        ents = {axis: tuple(group) for (axis, _), group in zip(p.combine, combo)}
        slugs = {axis: _SLUG_JOIN.join(axis_slugs[axis][e.id] for e in group) for axis, group in ents.items()}
        path = _PLACEHOLDER_RE.sub(lambda m: slugs[m.group(1)], p.raw_path)
        path = "/".join(seg.strip() for seg in path.replace("\\", "/").split("/"))
        if len(path) > 512:
            out.errors.append(f"Scenario '{label}' makes folder '{path[:60]}…', longer than 512 characters")
            return None
        leaves.append(Leaf(
            scenario=sc.name,
            path=path,
            target=sc.count,
            entity_ids={axis: [e.id for e in group] for axis, group in ents.items()},
            template=p.template,
            entities=ents,
            variations=variations,
            product=product,
            # Keyed by the folder alone: the leaf *is* its folder, so its walk must
            # not change when the scenario that makes it is renamed.
            walk_seed=sub_seed(seed, "walk", path),
        ))
    return leaves


def _walk_index(i: int, product: int, walk_seed: int) -> int:
    """Leaf prompt i's index into the variation product.

    An affine bijection `(a·i + b) mod P` with `a` coprime to P: every index appears
    once per P prompts, consecutive prompts differ in several pieces, and prompt i
    never changes when more are drawn — which is what lets Top up continue the walk.
    """
    if product <= 1:
        return 0
    a = 1 + walk_seed % (product - 1) if product > 2 else 1
    while math.gcd(a, product) != 1:
        a += 1
    b = (walk_seed >> 32) % product
    return (a * (i % product) + b) % product


def compose(leaf: Leaf, start: int, n: int) -> list[str]:
    """The leaf's prompts `start … start+n-1` from its template walk."""
    sizes = [len(p) for _, p in leaf.variations]
    out = []
    for i in range(start, start + n):
        j = _walk_index(i, leaf.product, leaf.walk_seed)
        chosen: dict[str, str] = {}
        for (name, pieces), size in zip(leaf.variations, sizes):
            j, k = divmod(j, size)
            chosen[name] = pieces[k].strip()

        def repl(m: re.Match) -> str:
            base, _, attr = m.group(1).partition(".")
            if base in leaf.entities:
                group = leaf.entities[base]
                if attr == "label":
                    return " and ".join(e.label.strip() for e in group)
                return _join_descriptors(group)
            return chosen[base]

        out.append(_PLACEHOLDER_RE.sub(repl, leaf.template))
    return out


def plan_fill(target: int, kept: int, open_rows: int, yield_: float) -> int:
    """Rows a leaf needs so that it will hold `target` images.

    `kept` is images that still exist, `open_rows` the pending, running and failed
    rows (each counted as `yield_` future images), and `yield_` the leaf's images
    per completed row. Targets are absolute, so a second call adds nothing.
    """
    yield_ = yield_ if yield_ > 0 else 1.0
    need = target - kept - open_rows * yield_
    if need <= 1e-9:
        return 0
    return math.ceil(need / yield_ - 1e-9)
