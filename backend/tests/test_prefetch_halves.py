"""A `prepare_*` half never touches the device (M10 T5, PM-035).

`prefetch.run_prefetched` runs each GPU scorer's `prepare_*` half on asyncio's
default pool, whose threads never exit. A CUDA call there would build a set of
CUDA handles on that thread and keep it for the life of the server, which is the
leak PM-035 fixed (`docs/dev/ml-models.md` § One GPU thread). The executor guard
(`test_gpu_executor_guard.py`) exempts `run_prefetched` by name and cannot see
which callable it is handed, so this file checks the other side, structurally:

- every `prepare_*` function in `backend/ml/` opens its image through
  `open_rgb`/`open_rgb_bytes`, closes it, and never moves a tensor to the device,
  enters autocast, dispatches to the GPU thread or reaches a model; and
- every `run_prefetched` call's prepare argument is a `prepare_*` function, and
  what it binds into that function is a transform or processor
  (`entry["preprocess"]`, `entry["processor"]`, `entry.processor`) — so nothing
  else, a model's `encode_image` say, can be slipped onto the pool through it.

CI has no torch, so this is a pure AST walk.
"""

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ML_DIR = BACKEND / "ml"
# What a prepare half may be handed beyond its path.
TRANSFORM_KEYS = {"preprocess", "processor"}

# Calls that reach the device or the GPU thread. `.to` covers `.to(device)`; a
# prepare half has no reason to change a tensor's dtype either, since the model's
# dtype is read from the model, which belongs to the GPU half.
FORBIDDEN_CALLS = {"to", "cuda", "get_device", "autocast_ctx", "autocast", "run_on_gpu"}
# Model-entry members a prepare half must never read. It is handed the transform
# or processor alone.
FORBIDDEN_ATTRS = {"model"}
FORBIDDEN_KEYS = {"model", "clip", "mlp"}


def _modules() -> list[tuple[str, ast.Module]]:
    return [(p.name, ast.parse(p.read_text(encoding="utf-8"))) for p in sorted(ML_DIR.glob("*.py"))]


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _prepare_functions():
    for name, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("prepare_"):
                yield name, node


def test_there_are_prepare_halves_to_check():
    found = {f"{m}:{fn.name}" for m, fn in _prepare_functions()}
    assert {
        "aesthetic_scorer.py:prepare_clip",
        "aesthetic_scorer.py:prepare_clip_bytes",
        "aesthetic_v2_5_scorer.py:prepare_v2_5",
        "dino_scorer.py:prepare_dino",
        "nsfw_scorer.py:prepare_nsfw",
    } <= found


def test_no_prepare_half_reaches_the_device_or_a_model():
    offenders = []
    for module, fn in _prepare_functions():
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and _call_name(node) in FORBIDDEN_CALLS:
                offenders.append(f"{module}:{fn.name} line {node.lineno}: calls {_call_name(node)}")
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
                offenders.append(f"{module}:{fn.name} line {node.lineno}: reads .{node.attr}")
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value in FORBIDDEN_KEYS
            ):
                offenders.append(f"{module}:{fn.name} line {node.lineno}: reads [{node.slice.value!r}]")
    assert not offenders, "a prepare_* half runs on the default pool:\n" + "\n".join(offenders)


def test_every_prepare_half_opens_through_the_helper_and_closes():
    """CLAUDE.md § Key invariants: open with `open_rgb`, close right after
    preprocessing. The close is what keeps a window of prepared images from
    holding several decoded buffers."""
    offenders = []
    for module, fn in _prepare_functions():
        calls = {_call_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        if not calls & {"open_rgb", "open_rgb_bytes"}:
            offenders.append(f"{module}:{fn.name}: no open_rgb")
        if "close" not in calls:
            offenders.append(f"{module}:{fn.name}: no close()")
    assert not offenders, "\n".join(offenders)


def _is_transform(node: ast.AST) -> bool:
    """`x["preprocess"]`, `x["processor"]` or `x.processor`, and nothing else."""
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value in TRANSFORM_KEYS
    if isinstance(node, ast.Attribute):
        return node.attr in TRANSFORM_KEYS
    return False


def _is_prepare_partial(node: ast.AST) -> bool:
    """`functools.partial(prepare_x, <transform>)`, with every bound value a
    transform or processor read off a model entry."""
    if not (
        isinstance(node, ast.Call)
        and _call_name(node) == "partial"
        and bool(node.args)
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id.startswith("prepare_")
    ):
        return False
    bound = list(node.args[1:]) + [kw.value for kw in node.keywords]
    return all(_is_transform(v) for v in bound)


def _backend_modules() -> list[tuple[str, ast.Module]]:
    """Every module that could call `run_prefetched`, not only `ml/`."""
    out = []
    for p in sorted(BACKEND.rglob("*.py")):
        if "tests" in p.relative_to(BACKEND).parts:
            continue
        out.append((str(p.relative_to(BACKEND)), ast.parse(p.read_text(encoding="utf-8"))))
    return out


def test_the_partial_check_refuses_a_model_bound_into_a_prepare_half():
    """The guard's own self-test: a model's forward pass bound in as the
    'preprocess' would run CUDA on a pool thread, and must not pass."""
    bad = ast.parse(
        'functools.partial(prepare_clip, preprocess=entry["clip"].encode_image)', mode="eval",
    ).body
    good = ast.parse('functools.partial(prepare_clip, preprocess=entry["preprocess"])', mode="eval").body
    assert not _is_prepare_partial(bad)
    assert _is_prepare_partial(good)


def test_every_run_prefetched_call_hands_it_a_prepare_half():
    offenders = []
    calls_seen = 0
    for module, tree in _backend_modules():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assigned: dict[str, list[ast.AST]] = {}
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            assigned.setdefault(target.id, []).append(node.value)
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call) and _call_name(node) == "run_prefetched"):
                    continue
                calls_seen += 1
                arg = node.args[1] if len(node.args) > 1 else None
                ok = arg is not None and (
                    _is_prepare_partial(arg)
                    or (
                        isinstance(arg, ast.Name)
                        and arg.id in assigned
                        and all(_is_prepare_partial(v) for v in assigned[arg.id])
                    )
                )
                if not ok:
                    offenders.append(f"{module}:{fn.name} line {node.lineno}")
    assert calls_seen >= 6, "the six scorer loops no longer call run_prefetched"
    assert not offenders, (
        "run_prefetched runs its prepare argument on the default pool; hand it "
        "functools.partial(prepare_*, <a preprocess or processor>):\n" + "\n".join(offenders)
    )
