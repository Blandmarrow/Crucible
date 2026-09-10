"""Request-level regression tests for the empty-caption guard (issue #94).

An image that finished a caption run with no caption text used to fall through
`if caption:` with no exception: no failure count, no log line, no `.txt`
sidecar, job green. The user's only signal was noticing, later, that a handful of
images were uncaptioned. Worse, when post-processing was what emptied the text,
the code still called `set_caption(…, "")` — blanking a caption the image
already had, and writing an empty sidecar over a good one.

What is pinned here:

- **Reason A** — the provider returns 200 with empty content → a counted, named
  failure under `result_data["empty"]`, and no sidecar written.
- **Reason C** — post-processing strips the whole reply → counted under
  `result_data["stripped"]`, and the pre-existing caption *and its sidecar* are
  left byte-identical (the regression the old `set_caption(…, "")` caused).
- the happy path still writes, with no `result_data` at all.
- `_failure_headline`'s composition, including the provider-`None` case where
  the Max-tokens advice would be meaningless (a local Florence-2/WD14 run).

The stub replaces `backend.ml.openai_compat_captioner.caption_image`; the router
imports that name *inside* the loop, so patching the module attribute reaches it.
Torch-free — that module imports only PIL and `backend/ml/image_utils.py` — so
this file needs no `needs_torch` gate.
"""
from pathlib import Path

from backend.models.image import Image
from backend.routers.captioning import _failure_headline
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _provider(env, name: str = "lmstudio") -> dict:
    r = await env.client.post(
        f"{API}/providers/",
        json={
            "name": name,
            "base_url": "http://127.0.0.1:1234/v1",
            "default_model": "qwen-vl",
            "max_tokens": 16,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _stub(monkeypatch, reply: str):
    """Make every openai-compat call return `reply` without touching the network."""
    import backend.ml.openai_compat_captioner as mod

    async def _fake(image_path, **kwargs):
        return reply

    monkeypatch.setattr(mod, "caption_image", _fake)


async def _run_caption(env, ds_id: str, provider_id: str, **body) -> dict:
    from backend.tests.conftest import wait_for_job

    r = await env.client.post(
        f"{API}/captioning/run",
        json={
            "dataset_id": ds_id,
            "model": f"openai_compat:{provider_id}:qwen-vl",
            "style": "detailed",
            **body,
        },
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert job_id, r.text
    return await wait_for_job(env, job_id)


def _sidecar(dataset: dict, image_row: dict) -> Path:
    return Path(dataset["folder_path"]) / "images" / (Path(image_row["filename"]).stem + ".txt")


def test_empty_provider_reply_is_a_counted_failure_and_writes_no_sidecar(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())
            prov = await _provider(env)
            _stub(monkeypatch, "")

            job = await _run_caption(env, ds["id"], prov["id"])
            assert job["status"] == "completed", job

            data = job["result_data"]
            assert data["failed_count"] == 1
            assert data["empty"] == 1
            assert data["stripped"] == 0
            assert data["timed_out"] == 0
            assert data["failed"][0]["file"] == "a.png"
            assert data["failed"][0]["error"] == "provider returned an empty caption"
            assert "came back empty" in data["failure_summary"]
            # A provider is in play, so the actionable advice is included.
            assert "Max tokens" in data["failure_summary"]

            async with env.Session() as db:
                assert (await db.get(Image, img["id"])).caption_text == ""
            assert not _sidecar(ds, img).exists()

    run(scenario())


def test_post_processing_emptying_the_caption_leaves_the_existing_one_intact(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())
            r = await env.client.put(
                f"{API}/captions/image/{img['id']}", json={"caption_text": "a good caption"}
            )
            assert r.status_code == 200, r.text
            before = _sidecar(ds, img).read_bytes()

            prov = await _provider(env)
            _stub(monkeypatch, "<think>only thinking</think>")

            job = await _run_caption(
                env, ds["id"], prov["id"], overwrite=True, strip_thinking=True
            )
            assert job["status"] == "completed", job

            data = job["result_data"]
            assert data["failed_count"] == 1
            assert data["stripped"] == 1
            assert data["empty"] == 0
            assert data["failed"][0]["error"] == "post-processing removed the entire caption"
            assert "removed by post-processing" in data["failure_summary"]

            # The whole point: nothing was overwritten with "".
            async with env.Session() as db:
                assert (await db.get(Image, img["id"])).caption_text == "a good caption"
            assert _sidecar(ds, img).read_bytes() == before

    run(scenario())


def test_a_real_caption_still_saves_and_reports_no_failure(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())
            prov = await _provider(env)
            _stub(monkeypatch, "a blue square on a white ground")

            job = await _run_caption(env, ds["id"], prov["id"])
            assert job["status"] == "completed", job
            assert job["result_data"] == {}

            async with env.Session() as db:
                assert (await db.get(Image, img["id"])).caption_text == (
                    "a blue square on a white ground"
                )
            assert _sidecar(ds, img).read_text(encoding="utf-8") == (
                "a blue square on a white ground"
            )

    run(scenario())


# ── _failure_headline composition ─────────────────────────────────────────────

class _Prov:
    name = "lmstudio"
    timeout_s = 300


def test_failure_headline_is_none_when_nothing_is_diagnosed():
    assert _failure_headline(0, _Prov(), 0, 0) is None
    assert _failure_headline(0, None) is None


def test_failure_headline_one_sentence_per_tally():
    timed = _failure_headline(2, _Prov())
    assert timed is not None and "2 image(s) timed out" in timed
    assert "came back empty" not in timed

    empty = _failure_headline(0, _Prov(), empty=3)
    assert empty == (
        "3 image(s) came back empty — the model returned no caption text. "
        "Raise Max tokens in Settings → LLM Providers."
    )

    stripped = _failure_headline(0, _Prov(), stripped=1)
    assert stripped == (
        "1 image(s) had their whole caption removed by post-processing "
        "— turn off Strip thinking blocks or Strip refusals."
    )


def test_failure_headline_joins_two_diagnoses():
    both = _failure_headline(1, _Prov(), empty=2)
    assert both is not None
    assert "1 image(s) timed out" in both
    assert "2 image(s) came back empty" in both


def test_failure_headline_drops_max_tokens_advice_without_a_provider():
    # A local Florence-2/WD14 run has no Max tokens setting to raise.
    local = _failure_headline(0, None, empty=4)
    assert local == "4 image(s) came back empty — the model returned no caption text."
