"""Florence-2's remote code against current transformers: the two settings that load it.

Florence-2 ships its own modelling code (`trust_remote_code=True`), written for an
older transformers, and on the installed 4.57 it failed twice before producing a
token. Captioning and Florence detection were both dead on arrival, with nothing in
the suite to say so — every test fakes the model:

- **Load.** Left unset, `from_pretrained` probes `_supports_sdpa`, which the remote
  code reads off a sub-model its `__init__` has not built yet → AttributeError.
  `attn_implementation="eager"` skips the probe.
- **Generate.** The remote `prepare_inputs_for_generation` indexes the Cache object
  newer transformers passes as the legacy tuple (`past_key_values[0][0].shape`), and
  the first beam-search step raised on the empty cache. `use_cache=False` avoids it.

Both look like removable clutter, which is why they are pinned here. Structural on
purpose: the real failure needs the ~1.5 GB weights and a GPU, and these checks have
to run in CI, which has no torch (`florence_captioner` imports it lazily).
"""

import ast
from pathlib import Path

from backend.ml import florence_captioner

ML = Path(__file__).resolve().parent.parent / "ml"


def test_generate_runs_without_the_kv_cache():
    assert florence_captioner._GENERATE_KWARGS["use_cache"] is False


def test_every_florence_generate_call_passes_the_shared_kwargs():
    tree = ast.parse((ML / "florence_captioner.py").read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "generate"
    ]
    assert len(calls) == 2, "caption + detection; a new generate() call must pass _GENERATE_KWARGS too"
    for call in calls:
        assert any(
            kw.arg is None and isinstance(kw.value, ast.Name) and kw.value.id == "_GENERATE_KWARGS"
            for kw in call.keywords
        ), f"generate() at line {call.lineno} does not pass **_GENERATE_KWARGS"


def test_the_florence_load_asks_for_eager_attention():
    tree = ast.parse((ML / "model_manager.py").read_text(encoding="utf-8"))
    loader = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_load_florence2_sync"
    )
    pairs = {
        (k.value, v.value)
        for d in ast.walk(loader) if isinstance(d, ast.Dict)
        for k, v in zip(d.keys, d.values)
        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)
    }
    assert ("attn_implementation", "eager") in pairs
