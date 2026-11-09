"""GPU work runs on the one GPU thread, never on asyncio's default pool (PM-035).

Every thread that runs CUDA math keeps its own cuBLAS/cuBLASLt/cuDNN handles and
a cuBLAS workspace until it exits, and the default pool's threads never exit. A
625-image scoring run spread its per-image calls over all 32 of them and left
~3 GB of VRAM behind after every model had unloaded. `device.run_on_gpu` sends GPU
calls to a single dedicated thread instead. Nothing fails when a call goes the
old way — the memory is simply never returned — so the rule is enforced here,
structurally, over the source. CI has no torch, so this is a pure AST walk.

Two rules, because GPU calls are dispatched from two places:

- **Rule A, `backend/ml/`.** Any `run_in_executor` or `to_thread` call outside
  `device.py` is an offender unless `ML_CPU_DISPATCHES` names its enclosing
  function with a reason. Every module in the folder is in scope, not only the
  `*_scorer.py` ones, so a new module is covered the day it is added.
- **Rule B, everything else that dispatches work** — `backend/` itself,
  `routers/`, `services/` and `workers/`. These hold most of the CPU executor
  work (thumbnails, file IO, ffmpeg), so only a call off the GPU thread (the
  default pool, or any executor but `gpu_executor()`) whose callable leads into
  GPU code is an offender. GPU code is a torch-importing `backend/ml` module, a
  module in `GPU_MODULES_WITHOUT_TORCH`, or anything reached through a model
  entry's `.model`/`.processor`. The callable is followed through a name, a
  dotted `module.attr` (including `backend.ml.x.f`), `functools.partial(X, ...)`,
  a lambda, a local def the call can see, and local assignments — the nearest in
  the dispatch's own block, else every one above it in the function, so both
  arms of an `if`/`else` count. One hop at a time, a few hops deep.

  A callable it cannot resolve is **not** an offender, because flagging every
  unknown would need an allowlist of every CPU call in the routers. That covers
  an attribute of a parameter or other non-import (`spec.process`), a helper
  imported from another non-`ml` module that itself calls GPU code, and a
  callable passed through a container. `services/image_edit_job.py` routes
  `spec.process` through its `EditSpec.gpu` flag by hand for exactly that reason.
"""

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ML_DIR = BACKEND / "ml"

# Rule A's exemptions: (module, enclosing function) → why it is CPU work.
ML_CPU_DISPATCHES = {
    ("technical_scorer.py", "score_images_technical"): "cv2/numpy blur, noise and uniformity",
    ("ollama_captioner.py", "caption_image"): "PIL resize before an HTTP call",
    ("openai_compat_captioner.py", "caption_image"): "PIL encode + an HTTP request",
}

# Rule B's exemptions: callables in a GPU module that run on the CPU.
CPU_CALLEES_IN_TORCH_MODULES = {
    "tag_embedder.cluster_texts_sync": "numpy agglomerative clustering over stored vectors",
}

# Rule B counts a module as GPU code when it imports torch. These reach the
# accelerator without importing it, so they are named by hand.
GPU_MODULES_WITHOUT_TORCH = {
    "tag_embedder": "embed_texts_sync runs a SentenceTransformer that model_manager put on the GPU",
}

# Every other torch-free module, with why it never touches the accelerator. A new
# module must be triaged into one list or the other
# (`test_every_torch_free_ml_module_is_triaged`).
CPU_ONLY_MODULES = {
    "__init__": "package marker",
    "download_progress": "tqdm patch and SSE emits",
    "image_utils": "PIL opening and resizing",
    "lut_processor": "numpy colour grading",
    "mask_utils": "numpy/PIL mask geometry",
    "nudenet_scorer": "NudeNet's ONNX session, CPU-only",
    "ollama_captioner": "HTTP to an Ollama server",
    "openai_compat_captioner": "HTTP to an OpenAI-compatible server",
    "prompt_generator": "HTTP to an LLM provider",
    "similarity_scorer": "numpy over stored embeddings",
    "technical_scorer": "cv2/numpy",
    "wd14_tagger": "onnxruntime with CPUExecutionProvider",
}

EXECUTOR_ATTRS = {"run_in_executor", "to_thread"}
MAX_HOPS = 4


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports_torch(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "torch" or a.name.startswith("torch.") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "torch" or node.module.startswith("torch."):
                return True
    return False


def torch_modules() -> set[str]:
    """Stems of the `backend/ml` modules whose code can run on the accelerator."""
    found = {p.stem for p in ML_DIR.glob("*.py") if _imports_torch(_parse(p))}
    return found | set(GPU_MODULES_WITHOUT_TORCH)


def _is_executor_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else None
    return name in EXECUTOR_ATTRS


def _is_gpu_executor_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    return (isinstance(fn, ast.Attribute) and fn.attr == "gpu_executor") or (
        isinstance(fn, ast.Name) and fn.id == "gpu_executor"
    )


def _dispatched(call: ast.Call) -> ast.AST | None:
    """The callable a dispatch runs off the GPU thread, or None when it is not one.

    `to_thread(fn, ...)` always uses the default pool. `run_in_executor(ex, fn, ...)`
    is off the GPU thread for any executor except `gpu_executor()`: the literal
    None, and equally a pool of the route's own, which would have the same
    per-thread cost.
    """
    fn = call.func
    name = fn.attr if isinstance(fn, ast.Attribute) else fn.id
    if name == "to_thread":
        return call.args[0] if call.args else None
    if len(call.args) >= 2 and not _is_gpu_executor_call(call.args[0]):
        return call.args[1]
    return None


# A callable reached through a loaded model entry (`entry.model.encode`,
# `nsfw_entry.processor`) is GPU code whichever module dispatches it.
MODEL_ENTRY = "<model entry>"
MODEL_ENTRY_ATTRS = {"model", "processor"}


def _dotted(expr: ast.AST) -> list[str] | None:
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    parts.append(expr.id)
    return parts[::-1]


def _ml_imports(tree: ast.Module) -> dict[str, tuple[str, str | None]]:
    """Local name → (ml module stem, function or None for the module itself)."""
    out: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "backend.ml":
                for a in node.names:
                    out[a.asname or a.name] = (a.name, None)
            elif node.module.startswith("backend.ml."):
                stem = node.module.split(".")[2]
                for a in node.names:
                    out[a.asname or a.name] = (stem, a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("backend.ml.") and a.asname:
                    out[a.asname] = (a.name.split(".")[2], None)
    return out


class _Resolver:
    """Follows a dispatched callable to the `backend/ml` functions it reaches."""

    def __init__(self, tree: ast.Module):
        self.imports = _ml_imports(tree)
        self.parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
        self.defs: dict[str, list[ast.AST]] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.defs.setdefault(node.name, []).append(node)

    def _scope(self, node: ast.AST) -> ast.AST | None:
        cur = self.parents.get(node)
        while cur is not None and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cur = self.parents.get(cur)
        return cur

    def _scopes(self, node: ast.AST) -> list[ast.AST | None]:
        """The enclosing functions, innermost first, ending with None (the module)."""
        out: list[ast.AST | None] = []
        cur = self._scope(node)
        while cur is not None:
            out.append(cur)
            cur = self._scope(cur)
        return out + [None]

    def _statement_block(self, node: ast.AST) -> tuple[ast.stmt, list] | None:
        """The statement holding `node`, and the statement list it sits in."""
        cur = node
        while cur is not None:
            parent = self.parents.get(cur)
            if isinstance(cur, ast.stmt) and parent is not None:
                for _, value in ast.iter_fields(parent):
                    if isinstance(value, list) and any(s is cur for s in value):
                        return cur, value
            cur = parent
        return None

    @staticmethod
    def _assigns(node: ast.AST, name: str) -> bool:
        if isinstance(node, ast.Assign):
            return any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        if isinstance(node, ast.AnnAssign):
            return node.value is not None and isinstance(node.target, ast.Name) and node.target.id == name
        return False

    def _bindings(self, use: ast.Name) -> list[ast.AST]:
        """The assignments to `use.id` that can reach this use.

        An assignment earlier in the same statement list wins alone, so a route
        that reuses `fn` across branches does not have each branch charged with
        the others. Without one, every assignment above the use in the function
        counts, so an `if`/`else` that binds `fn` in both arms is followed into
        both.
        """
        located = self._statement_block(use)
        if located is not None:
            stmt, block = located
            same_block = [s for s in block if s.lineno < stmt.lineno and self._assigns(s, use.id)]
            if same_block:
                return [max(same_block, key=lambda n: n.lineno).value]
        scope = self._scope(use)
        if scope is None:
            return []
        return [
            node.value for node in ast.walk(scope)
            if self._assigns(node, use.id) and node.lineno <= use.lineno
        ]

    def targets(self, expr: ast.AST, hops: int = 0) -> set[str]:
        """`module.func` (or `module.*`) names in `backend/ml` that `expr` leads to."""
        if hops > MAX_HOPS or expr is None:
            return set()
        if isinstance(expr, ast.Call):
            fn = expr.func
            is_partial = (isinstance(fn, ast.Name) and fn.id == "partial") or (
                isinstance(fn, ast.Attribute) and fn.attr == "partial"
            )
            if is_partial and expr.args:
                return self.targets(expr.args[0], hops + 1)
            return self.targets(fn, hops + 1)
        if isinstance(expr, ast.Lambda):
            return self._calls_in(expr.body, hops)
        if isinstance(expr, ast.Attribute):
            parts = _dotted(expr)
            if parts is None:
                return set()
            if parts[:2] == ["backend", "ml"] and len(parts) >= 4:  # `import backend.ml.x`
                return {f"{parts[2]}.{parts[3]}"}
            if parts[0] in self.imports:
                stem, func = self.imports[parts[0]]
                if func is None:
                    return {f"{stem}.{parts[1]}"}
            if MODEL_ENTRY_ATTRS & set(parts[1:]):
                return {f"{MODEL_ENTRY}.{'.'.join(parts[1:])}"}
            return set()
        if isinstance(expr, ast.Name):
            if expr.id in self.imports:
                stem, func = self.imports[expr.id]
                return {f"{stem}.{func or '*'}"}
            found: set[str] = set()
            for value in self._bindings(expr):
                found |= self.targets(value, hops + 1)
            # Only a def the use can see: another route's `_run` is not this one's.
            visible = self._scopes(expr)
            for fdef in self.defs.get(expr.id, []):
                if any(self._scope(fdef) is s for s in visible):
                    found |= self._calls_in(fdef, hops)
            return found
        return set()

    def _calls_in(self, node: ast.AST, hops: int) -> set[str]:
        found: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                found |= self.targets(sub.func, hops + 1)
        return found


def _enclosing_function(tree: ast.Module, target: ast.AST) -> str:
    best = "<module>"
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(sub is target for sub in ast.walk(node)):
                best = node.name  # ast.walk is breadth-first: the last hit is innermost
    return best


def rule_a_offenders(sources: dict[str, str]) -> list[str]:
    """`backend/ml` executor calls outside `device.py` that are not exempted."""
    offenders = []
    for name, src in sorted(sources.items()):
        if name == "device.py":
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if _is_executor_call(node):
                fn = _enclosing_function(tree, node)
                if (name, fn) not in ML_CPU_DISPATCHES:
                    offenders.append(f"{name}:{node.lineno} (in {fn})")
    return offenders


def rule_b_offenders(sources: dict[str, str], torch_mods: set[str]) -> list[str]:
    """Dispatches off the GPU thread whose callable reaches GPU code."""
    offenders = []
    gpu_roots = torch_mods | {MODEL_ENTRY}
    for name, src in sorted(sources.items()):
        tree = ast.parse(src)
        resolver = None
        for node in ast.walk(tree):
            if not _is_executor_call(node):
                continue
            target = _dispatched(node)
            if target is None:
                continue
            resolver = resolver or _Resolver(tree)
            hits = sorted(
                t for t in resolver.targets(target)
                if t.split(".")[0] in gpu_roots and t not in CPU_CALLEES_IN_TORCH_MODULES
            )
            if hits:
                offenders.append(f"{name}:{node.lineno} → {', '.join(hits)}")
    return offenders


def _sources(*dirs: Path) -> dict[str, str]:
    return {
        f"{p.parent.name}/{p.name}" if p.parent != ML_DIR else p.name: p.read_text(encoding="utf-8")
        for d in dirs for p in d.glob("*.py")
    }


# Everything outside `backend/ml` that dispatches work: the routers and services
# hold nearly all of it, but a worker or a top-level module (`main.py`, `utils.py`)
# can dispatch too. Tests and migrations are not app code.
RULE_B_DIRS = (BACKEND, BACKEND / "routers", BACKEND / "services", BACKEND / "workers")


# ── The rules over the real tree ───────────────────────────────────────────────


def test_ml_modules_dispatch_gpu_work_only_through_run_on_gpu():
    offenders = rule_a_offenders(_sources(ML_DIR))
    assert offenders == [], (
        "these backend/ml executor calls run on asyncio's default pool; GPU work must "
        "go through `device.run_on_gpu`, and genuine CPU work needs an entry in "
        "ML_CPU_DISPATCHES with its reason: " + ", ".join(offenders)
    )


def test_routers_and_services_send_torch_calls_to_the_gpu_thread():
    offenders = rule_b_offenders(
        _sources(*RULE_B_DIRS), torch_modules()
    )
    assert offenders == [], (
        "these default-pool dispatches reach a torch-importing backend/ml function; "
        "use `device.run_on_gpu` instead, or, for CPU work in such a module, add it "
        "to CPU_CALLEES_IN_TORCH_MODULES with its reason: " + "; ".join(offenders)
    )


def test_every_torch_free_ml_module_is_triaged():
    """A module that reaches the GPU without importing torch is invisible to Rule B
    unless it is named, so every torch-free module is accounted for, and no list
    names a module that has gone or now imports torch."""
    torch_free = {p.stem for p in ML_DIR.glob("*.py") if not _imports_torch(_parse(p))}
    listed = set(GPU_MODULES_WITHOUT_TORCH) | set(CPU_ONLY_MODULES)
    assert not set(GPU_MODULES_WITHOUT_TORCH) & set(CPU_ONLY_MODULES)
    assert torch_free - listed == set(), (
        "untriaged torch-free backend/ml modules — add each to GPU_MODULES_WITHOUT_TORCH "
        f"or CPU_ONLY_MODULES with a reason: {sorted(torch_free - listed)}"
    )
    assert listed - torch_free == set(), (
        f"listed modules that are gone or now import torch: {sorted(listed - torch_free)}"
    )


def test_every_exemption_still_exists():
    """A stale entry would silently exempt the next function given that name."""
    for (module, fn) in ML_CPU_DISPATCHES:
        tree = _parse(ML_DIR / module)
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert fn in names, f"ML_CPU_DISPATCHES names {module}::{fn}, which no longer exists"
    for dotted in CPU_CALLEES_IN_TORCH_MODULES:
        module, fn = dotted.split(".")
        tree = _parse(ML_DIR / f"{module}.py")
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert fn in names, f"CPU_CALLEES_IN_TORCH_MODULES names {dotted}, which no longer exists"


# ── The guard can see what it is for ───────────────────────────────────────────

_TORCH = {"sam2_predictor", "lama_inpainter", "upscaler"}


def test_rule_b_sees_a_partial_through_a_local_name():
    src = (
        "import functools\n"
        "from backend.ml.sam2_predictor import predict_sync\n"
        "async def route(loop):\n"
        "    fn = functools.partial(predict_sync, 1, box=2)\n"
        "    return await loop.run_in_executor(None, fn)\n"
    )
    assert rule_b_offenders({"routers/x.py": src}, _TORCH) == [
        "routers/x.py:5 → sam2_predictor.predict_sync"
    ]


def test_rule_b_sees_a_module_attribute_a_lambda_and_a_local_def():
    src = (
        "import asyncio\n"
        "from backend.ml import lama_inpainter, upscaler\n"
        "async def route(loop):\n"
        "    def _work():\n"
        "        return upscaler.upscale_image_sync(1)\n"
        "    await loop.run_in_executor(None, lama_inpainter.inpaint_image_sync, 1)\n"
        "    await loop.run_in_executor(None, lambda: upscaler.upscale_image_sync(2))\n"
        "    await loop.run_in_executor(None, _work)\n"
        "    await asyncio.to_thread(lama_inpainter.inpaint_image_sync, 3)\n"
    )
    assert [o.split(" →")[0] for o in rule_b_offenders({"routers/x.py": src}, _TORCH)] == [
        "routers/x.py:6", "routers/x.py:7", "routers/x.py:8", "routers/x.py:9",
    ]


def test_rule_b_follows_only_the_closest_assignment_of_a_reused_name():
    """`routers/detection.py` reuses `fn` across its NudeNet and SAM branches; the
    CPU branch must not be charged with the GPU one's callee."""
    src = (
        "import functools\n"
        "from backend.ml.nudenet_scorer import detect_sync\n"
        "from backend.ml.sam2_predictor import predict_sync\n"
        "async def route(loop, model):\n"
        "    if model == 'nudenet':\n"
        "        fn = functools.partial(detect_sync, 1)\n"
        "        await loop.run_in_executor(None, fn)\n"
        "    else:\n"
        "        fn = functools.partial(predict_sync, 1)\n"
        "        await loop.run_in_executor(None, fn)\n"
    )
    assert rule_b_offenders({"routers/x.py": src}, _TORCH) == [
        "routers/x.py:10 → sam2_predictor.predict_sync"
    ]


def test_rule_b_follows_both_arms_of_an_if_else_binding():
    """With no assignment in the dispatch's own block, every binding above it
    counts — a GPU arm must not hide behind a later CPU one."""
    src = (
        "import functools\n"
        "from backend.ml.nudenet_scorer import detect_sync\n"
        "from backend.ml.sam2_predictor import predict_sync\n"
        "async def route(loop, model):\n"
        "    if model == 'sam2':\n"
        "        fn = functools.partial(predict_sync, 1)\n"
        "    else:\n"
        "        fn = functools.partial(detect_sync, 1)\n"
        "    await loop.run_in_executor(None, fn)\n"
    )
    assert rule_b_offenders({"routers/x.py": src}, _TORCH) == [
        "routers/x.py:9 → sam2_predictor.predict_sync"
    ]


def test_rule_b_sees_model_entries_annotations_dotted_imports_and_own_pools():
    src = (
        "import functools\n"
        "import backend.ml.upscaler\n"
        "from backend.ml.lama_inpainter import inpaint_image_sync\n"
        "async def route(loop, entry, pool):\n"
        "    await loop.run_in_executor(None, entry.model.encode, 1)\n"
        "    fn: object = functools.partial(inpaint_image_sync, 1)\n"
        "    await loop.run_in_executor(None, fn)\n"
        "    await loop.run_in_executor(None, backend.ml.upscaler.upscale_image_sync, 1)\n"
        "    await loop.run_in_executor(pool, inpaint_image_sync, 1)\n"
    )
    assert [o.split(" →")[0] for o in rule_b_offenders({"routers/x.py": src}, _TORCH)] == [
        "routers/x.py:5", "routers/x.py:7", "routers/x.py:8", "routers/x.py:9",
    ]


def test_rule_b_charges_a_dispatch_only_with_the_local_def_it_can_see():
    """Two routes each defining `_work`: the CPU one is not charged with the GPU one's."""
    src = (
        "from backend.ml import upscaler\n"
        "async def gpu_route(loop):\n"
        "    def _work():\n"
        "        return upscaler.upscale_image_sync(1)\n"
        "    return _work\n"
        "async def cpu_route(loop):\n"
        "    def _work():\n"
        "        return sum([1, 2])\n"
        "    await loop.run_in_executor(None, _work)\n"
    )
    assert rule_b_offenders({"routers/x.py": src}, _TORCH) == []


def test_rule_b_passes_run_on_gpu_cpu_modules_and_exempt_callees():
    src = (
        "from backend.ml import device as _device, tag_embedder, technical_scorer\n"
        "from backend.ml.sam2_predictor import predict_sync\n"
        "async def route(loop):\n"
        "    await _device.run_on_gpu(predict_sync, 1)\n"
        "    await loop.run_in_executor(None, technical_scorer.score_technical_sync, 1)\n"
        "    await loop.run_in_executor(None, tag_embedder.cluster_texts_sync, 1)\n"
        "    await loop.run_in_executor(_device.gpu_executor(), predict_sync, 1)\n"
    )
    torch_mods = _TORCH | {"tag_embedder"}
    assert rule_b_offenders({"services/x.py": src}, torch_mods) == []


def test_rule_a_flags_any_executor_call_outside_device_py():
    src = (
        "import asyncio\n"
        "async def score(loop, fn):\n"
        "    await loop.run_in_executor(None, fn)\n"
        "    await asyncio.to_thread(fn)\n"
    )
    assert rule_a_offenders({"new_scorer.py": src, "device.py": src}) == [
        "new_scorer.py:3 (in score)", "new_scorer.py:4 (in score)",
    ]
