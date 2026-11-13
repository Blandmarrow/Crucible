"""CLIP ViT-L/14 is built in one place, with QuickGELU (the 2026-11 fix).

OpenAI trained CLIP ViT-L/14 with QuickGELU, but open_clip's plain `"ViT-L-14"`
config uses `nn.GELU`. Building it with `create_model_and_transforms("ViT-L-14",
pretrained="openai")` loads fine, runs fine and returns vectors that are only
slightly wrong — every stored `clip_embedding`, LAION score and watermark score
was produced that way until the fix. Nothing fails when a call goes the old way,
so the rule is enforced here, structurally. CI has no torch, so this is a pure
AST walk over the source:

1. Every open_clip model-construction call in `backend/` (app code and
   `scripts/`) sits inside `aesthetic_scorer.build_clip_vit_l14`. That builder
   passes `force_quick_gelu` and checks the result; a second construction site
   would skip both, and a script that builds its own CLIP stops matching the
   vectors the app stores.
2. The builder passes `force_quick_gelu`.
3. Only the measurement script asks the builder for the old build
   (`quick_gelu=False`).
"""

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
BUILDER_FILE = BACKEND / "ml" / "aesthetic_scorer.py"
BUILDER = "build_clip_vit_l14"
CONSTRUCTORS = {"create_model", "create_model_and_transforms", "create_model_from_pretrained"}
# The one caller allowed to rebuild the old, wrong model: it measures what the fix changed.
OLD_BUILD_CALLERS = {BACKEND / "scripts" / "clip_build_delta.py"}


def _sources() -> list[Path]:
    return [
        p for p in BACKEND.rglob("*.py")
        if "tests" not in p.relative_to(BACKEND).parts
        and "alembic" not in p.relative_to(BACKEND).parts
    ]


def _callee(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def _calls_with_enclosing_function(tree: ast.AST):
    """Yield (call, name of the innermost enclosing def or None)."""
    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from walk(child, child.name)
            else:
                if isinstance(child, ast.Call):
                    yield child, fn
                yield from walk(child, fn)
    yield from walk(tree, None)


def _imports_open_clip(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "open_clip" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "open_clip":
            return True
    return False


def test_open_clip_models_are_built_only_by_the_builder():
    offenders = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if not _imports_open_clip(tree):
            continue
        for call, fn in _calls_with_enclosing_function(tree):
            if _callee(call) not in CONSTRUCTORS:
                continue
            if path == BUILDER_FILE and fn == BUILDER:
                continue
            offenders.append(f"{path.relative_to(BACKEND)}:{call.lineno} ({fn or 'module level'})")
    assert not offenders, (
        "open_clip model built outside aesthetic_scorer.build_clip_vit_l14 — that skips "
        "force_quick_gelu and the QuickGELU check, and a CLIP built that way produces "
        "vectors that do not match the stored ones. Call the builder instead:\n  "
        + "\n  ".join(offenders)
    )


def test_builder_passes_force_quick_gelu():
    tree = ast.parse(BUILDER_FILE.read_text(encoding="utf-8"))
    builders = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == BUILDER]
    assert len(builders) == 1, f"expected exactly one {BUILDER} in {BUILDER_FILE.name}"
    calls = [
        c for c in ast.walk(builders[0])
        if isinstance(c, ast.Call) and _callee(c) in CONSTRUCTORS
    ]
    assert calls, f"{BUILDER} no longer builds the model"
    for c in calls:
        assert any(k.arg == "force_quick_gelu" for k in c.keywords), (
            f"{BUILDER} builds CLIP without force_quick_gelu (line {c.lineno})"
        )


def test_only_the_measurement_script_asks_for_the_old_build():
    offenders = []
    for path in _sources():
        if path in OLD_BUILD_CALLERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call, _ in _calls_with_enclosing_function(tree):
            if _callee(call) != BUILDER:
                continue
            for k in call.keywords:
                if k.arg == "quick_gelu" and not (
                    isinstance(k.value, ast.Constant) and k.value.value is True
                ):
                    offenders.append(f"{path.relative_to(BACKEND)}:{call.lineno}")
    assert not offenders, (
        "only scripts/clip_build_delta.py may build the old plain-GELU CLIP:\n  "
        + "\n  ".join(offenders)
    )
