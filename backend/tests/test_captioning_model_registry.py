"""The captioning picker offers captioners, and only captioners.

`model_manager.list_models()` is one registry of twelve models covering four
roles, and `GET /captioning/models` used to return it raw. So the captioning
picker offered SAM 3, DINOv2, the Marqo NSFW detector and the MiniLM tag
embedder as things you could caption with — and picking one was a **silent
no-op**: the per-image dispatch is an if/elif chain on id prefixes over a
`caption = ""` starting value, with no `else`, so an unrecognised id fell
through every branch, failed the `if caption:` save, and finished the job green
reporting every image processed and zero failures.

Two mechanisms close that, and this file guards both:

- `kind` on each registry entry, filtered in the endpoint. Tagging *and*
  filtering rather than an id allowlist in the router, because an allowlist is a
  second list of model ids that drifts from the registry — the failure class
  this whole change exists to kill.
- `_caption_backend()`, the dispatch chain's single source of truth, enforced by
  a `@field_validator("model")` on `CaptionJobRequest` and `PipelineStep`. The
  filter alone does not fix the no-op: a stale localStorage default, a saved
  workflow blob or a direct API call all still reach the endpoint.

The validator has a frontend consequence, so this file also guards the hand-mirror
it forced: `captionBackend` in `frontend/src/constants/captionModels.ts`. Rejecting
a stale id at the door turned "nothing happens" into a hard 422 for the whole
request — in a pipeline, step 1 stops running because step 3 is stale — so the UI
has to refuse the Run itself, naming the offending step. It cannot reuse
`modelType` for that (null for the perfectly runnable `wd14:` and
`openai_compat:`), so the prefix chain is written twice and the parity test below
is what keeps the copies honest. Same reasoning, and same shape, as
`test_provenance.py::test_frontend_license_vocabulary_matches_backend`.

`description` is here for the same reason `kind` is. The reported symptom was
JoyCaption described as "Google · requires HF token" — neither true — because a
two-way ternary in the frontend gave every non-Florence model PaliGemma-2's
blurb. The copy now lives beside the loader that knows the repo id, so the
distinctness test below is a direct guard on that symptom.

Torch-free and CI-safe: `model_manager` imports only `backend.ml.device` at
module scope (torch is per-function there on purpose) and `wd14_tagger` needs
only numpy and PIL. No `needs_torch` marker — if one becomes necessary here,
something has moved a torch import to module scope.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.ml import wd14_tagger
from backend.ml.model_manager import model_manager
from backend.models import BackgroundJob
from backend.routers.captioning import _caption_backend
from backend.tests.conftest import API, api_env, run, upload_image

# The registry, partitioned. Both sets are checked against it in both directions,
# so a thirteenth model lands in neither and fails here rather than quietly
# appearing in — or vanishing from — the captioning picker.
CAPTIONERS = {
    "florence2_large",
    "florence2_promptgen",
    "paligemma2",
    "joycaption_alpha",
    "joycaption_beta",
}

NOT_CAPTIONERS = {
    "aesthetic",
    "aesthetic_v2_5",
    "dino",
    "nsfw",
    "sam2",
    "sam3",
    "tag_embedder",
}

_KINDS = {"caption", "score", "detect", "embed"}


@pytest.fixture
def _no_ollama(monkeypatch):
    """`GET /captioning/models` probes a local Ollama over httpx with a 5 s timeout
    and swallows every exception. In CI nothing listens so the refusal is instant,
    but on a dev box that *is* running Ollama the response becomes
    machine-dependent. Stub it, the same reasoning as `test_aesthetic_model_marker`'s
    autouse loader stub."""
    from backend.ml import ollama_captioner

    async def _none():
        return []

    monkeypatch.setattr(ollama_captioner, "list_vision_models", _none)


def test_every_registry_entry_declares_kind_and_description():
    for m in model_manager.list_models():
        assert m.get("kind") in _KINDS, f"{m['id']} has no usable kind: {m.get('kind')!r}"
        assert m.get("description"), f"{m['id']} has no description"


def test_the_two_sets_partition_the_registry():
    ids = {m["id"] for m in model_manager.list_models()}
    declared = CAPTIONERS | NOT_CAPTIONERS
    assert ids - declared == set(), "registry entry in neither set — classify it above"
    assert declared - ids == set(), "set names a model the registry no longer has"
    assert CAPTIONERS & NOT_CAPTIONERS == set()


def test_kind_caption_is_exactly_the_captioner_set():
    by_kind = {m["id"] for m in model_manager.list_models() if m["kind"] == "caption"}
    assert by_kind == CAPTIONERS


def test_descriptions_are_distinct():
    """The reported symptom: five models sharing one (wrong) blurb."""
    descriptions = [m["description"] for m in model_manager.list_models()]
    assert len(set(descriptions)) == len(descriptions)


def test_only_paligemma_mentions_a_token():
    """Deliberate asymmetry — it is the one genuinely gated model. Every other
    loader passes the ambient HF_TOKEN along and needs no token, and pasting
    "requires HF token" onto those is the bug this file exists for."""
    mentions = {
        m["id"] for m in model_manager.list_models()
        if "token" in m["description"].lower()
    }
    assert mentions == {"paligemma2"}


def test_models_endpoint_offers_only_captioners(tmp_path, _no_ollama):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/captioning/models")
            assert r.status_code == 200, r.text
            body = r.json()
            assert {m["id"] for m in body["local_models"]} == CAPTIONERS
            for m in body["local_models"]:
                assert m["kind"] == "caption"
                assert m["description"]

    run(scenario())


def test_every_offered_model_has_a_dispatch_branch(tmp_path, _no_ollama):
    """Whatever the picker offers must reach a branch of the captioning loop —
    the guard against the silent no-op coming back by a different route."""
    async def scenario():
        async with api_env(tmp_path) as env:
            body = (await env.client.get(f"{API}/captioning/models")).json()
            offered = [m["id"] for m in body["local_models"]] + [m["id"] for m in body["wd14_models"]]
            assert offered
            for model_id in offered:
                assert _caption_backend(model_id) is not None, model_id

    run(scenario())


def test_no_registry_non_captioner_has_a_dispatch_branch():
    for model_id in NOT_CAPTIONERS:
        assert _caption_backend(model_id) is None, model_id


def test_every_wd14_variant_has_a_description():
    variants = wd14_tagger.list_wd14_models()
    assert variants
    descriptions = [v["description"] for v in variants]
    assert all(descriptions)
    assert len(set(descriptions)) == len(descriptions)


def test_run_rejects_a_non_captioner_and_creates_no_job(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("caps")
            await upload_image(env, ds["id"])
            r = await env.client.post(
                f"{API}/captioning/run",
                json={"dataset_id": ds["id"], "model": "dino", "overwrite": True},
            )
            assert r.status_code == 422, r.text
            async with env.Session() as s:
                jobs = (await s.execute(select(BackgroundJob))).scalars().all()
            assert jobs == []

    run(scenario())


# ── The frontend hand-mirror ──────────────────────────────────────────────────

_TS_PATH = Path(__file__).parents[2] / "frontend" / "src" / "constants" / "captionModels.ts"

# The branches `_caption_backend` is written as, in order. The chain is ordered, so
# comparing the sequence rather than the set is the point: `florence2` before
# `paligemma2` is arbitrary, but a prefix moved ahead of one it shadows is not.
_EXPECTED_BRANCHES = [
    ("startsWith", "florence2", "florence2"),
    ("===", "paligemma2", "paligemma2"),
    ("startsWith", "joycaption_", "joycaption_"),
    ("startsWith", "ollama:", "ollama:"),
    ("startsWith", "openai_compat:", "openai_compat:"),
    ("startsWith", "wd14:", "wd14:"),
]

# Ids no registry enumerates — the four runtime-constructed forms, plus the three
# ways an id reaches the validator having never been a captioner at all.
_CONTROLS = {
    "ollama:llava": "ollama:",
    "ollama:qwen2.5vl:7b": "ollama:",
    "openai_compat:p": "openai_compat:",
    "openai_compat:p:gpt-4o": "openai_compat:",
    "wd14:x": "wd14:",
    "dino": None,
    "sam3": None,
    "": None,
}


def _ts_branches() -> list[tuple[str, str, str]]:
    """`captionBackend`'s if-chain, parsed out of the TypeScript."""
    src = _TS_PATH.read_text(encoding="utf-8")
    body = src.split("export function captionBackend", 1)[1].split("\n}", 1)[0]
    branches: list[tuple[str, str, str]] = []
    for line in body.splitlines():
        m = re.search(r'if \(model\.startsWith\("([^"]*)"\)\) return "([^"]*)";', line)
        if m:
            branches.append(("startsWith", m.group(1), m.group(2)))
            continue
        m = re.search(r'if \(model === "([^"]*)"\) return "([^"]*)";', line)
        if m:
            branches.append(("===", m.group(1), m.group(2)))
    return branches


def _ts_verdict(model: str, branches: list[tuple[str, str, str]]) -> str | None:
    """Evaluate the parsed chain the way the browser would."""
    for op, literal, ret in branches:
        if op == "startsWith" and model.startswith(literal):
            return ret
        if op == "===" and model == literal:
            return ret
    return None


def test_frontend_caption_backend_mirrors_the_prefix_chain():
    assert _ts_branches() == _EXPECTED_BRANCHES, (
        "captionModels.ts drifted from _caption_backend — reconcile both, then this list"
    )


def test_frontend_caption_backend_agrees_on_every_id():
    """Parse the mirror and run it against the real thing over everything a picker
    can produce. This is what makes the two copies safe: a prefix added to one and
    not the other means the UI either blocks a runnable model or lets a 422 through."""
    branches = _ts_branches()
    corpus: dict[str, str | None] = dict(_CONTROLS)
    for model_id in CAPTIONERS:
        corpus[model_id] = _caption_backend(model_id)
        assert corpus[model_id] is not None, model_id
    for model_id in NOT_CAPTIONERS:
        corpus[model_id] = None
    for v in wd14_tagger.list_wd14_models():
        corpus[v["id"]] = "wd14:"

    for model_id, expected in corpus.items():
        assert _caption_backend(model_id) == expected, f"backend: {model_id}"
        assert _ts_verdict(model_id, branches) == expected, f"frontend: {model_id}"


def test_pipeline_rejects_a_non_captioner_step(tmp_path):
    """The validator sits on `PipelineStep`, not on the request, so every step is
    covered — including a bad one in second position."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("caps")
            await upload_image(env, ds["id"])
            r = await env.client.post(
                f"{API}/captioning/pipeline",
                json={
                    "dataset_id": ds["id"],
                    "steps": [{"model": "florence2_large"}, {"model": "sam3"}],
                },
            )
            assert r.status_code == 422, r.text
            async with env.Session() as s:
                jobs = (await s.execute(select(BackgroundJob))).scalars().all()
            assert jobs == []

    run(scenario())
