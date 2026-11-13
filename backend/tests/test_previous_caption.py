"""`{previous_caption}` in a custom prompt is filled with the image's caption (#105).

What is pinned (docs/dev/captioning.md § The `{previous_caption}` placeholder):
- `/run` fills it from the caption each image holds when the job *starts* (not when it
  was queued), and with `""` for an uncaptioned image; a prompt without it is sent
  unchanged;
- `/pipeline` fills it on **every** step, step 0 included: step 0 sees the stored
  caption, a later step sees the step before's output;
- the body the detail page's Fix mode sends replaces a tag-style caption outright —
  the old tags are not merged back in.

Torch-free: the provider is stubbed at `openai_compat_captioner.caption_image`, as in
`test_caption_concurrency.py`.
"""
import asyncio
from pathlib import Path

from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


class _Recorder:
    """A fake `caption_image` that records each prompt it is sent, per file, in order."""

    def __init__(self, reply=lambda name, prompt: f"fixed {name}"):
        self.reply = reply
        self.prompts: dict[str, list[str]] = {}

    async def __call__(self, image_path, **kwargs):
        name = Path(image_path).name
        prompt = kwargs.get("custom_prompt", "")
        self.prompts.setdefault(name, []).append(prompt)
        return self.reply(name, prompt)


def _install(monkeypatch, reply=None) -> _Recorder:
    import backend.ml.openai_compat_captioner as mod

    fake = _Recorder(reply) if reply else _Recorder()
    monkeypatch.setattr(mod, "caption_image", fake)
    return fake


async def _seed(env, n: int = 2) -> tuple[dict, list[dict], str]:
    ds = await env.create_dataset("d")
    imgs = [
        await upload_image(env, ds["id"], f"i{i}.png", png_bytes(color=(i * 40, 10, 10)))
        for i in range(n)
    ]
    r = await env.client.post(
        f"{API}/providers/",
        json={"name": "lmstudio", "base_url": "http://127.0.0.1:1234/v1", "default_model": "vl"},
    )
    assert r.status_code == 201, r.text
    return ds, imgs, f"openai_compat:{r.json()['id']}:vl"


async def _set_caption(env, image_id: str, text: str) -> None:
    async with env.Session() as db:
        img = await db.get(Image, image_id)
        img.caption_text = text
        await db.commit()


async def _captions(env, ds_id: str) -> dict[str, str]:
    async with env.Session() as db:
        rows = (await db.execute(
            select(Image.filename, Image.caption_text).where(Image.dataset_id == ds_id)
        )).all()
    return {r.filename: r.caption_text for r in rows}


async def _finish(env, path: str, body: dict) -> None:
    r = await env.client.post(f"{API}/captioning/{path}", json=body)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert job_id, r.text
    job = await wait_for_job(env, job_id)
    assert job["status"] == "completed", job


def test_run_fills_the_placeholder_with_each_images_caption(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env)
            await _set_caption(env, imgs[0]["id"], "a cat on a mat")
            await _set_caption(env, imgs[1]["id"], "a dog on a log")
            fake = _install(monkeypatch)

            await _finish(env, "run", {
                "dataset_id": ds["id"], "model": model, "overwrite": True,
                "custom_prompt": "fix: {previous_caption}",
            })

            assert fake.prompts == {
                "i0.png": ["fix: a cat on a mat"],
                "i1.png": ["fix: a dog on a log"],
            }
            assert await _captions(env, ds["id"]) == {"i0.png": "fixed i0.png", "i1.png": "fixed i1.png"}

    run(scenario())


def test_run_fills_an_uncaptioned_image_with_empty(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, n=1)
            fake = _install(monkeypatch)

            await _finish(env, "run", {
                "dataset_id": ds["id"], "model": model,
                "custom_prompt": "[{previous_caption}]",
            })

            assert fake.prompts == {"i0.png": ["[]"]}

    run(scenario())


def test_run_sends_a_prompt_without_the_placeholder_unchanged(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, n=1)
            await _set_caption(env, imgs[0]["id"], "old")
            fake = _install(monkeypatch)

            await _finish(env, "run", {
                "dataset_id": ds["id"], "model": model, "overwrite": True,
                "custom_prompt": "describe {this} image",
            })

            assert fake.prompts == {"i0.png": ["describe {this} image"]}

    run(scenario())


def test_run_reads_the_caption_when_the_job_starts_not_when_it_is_queued(tmp_path, monkeypatch):
    """The queue runs one job at a time. A Fix queued behind a caption job that rewrites the
    same image must refine that job's output, not the caption the request saw."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, n=1)
            await _set_caption(env, imgs[0]["id"], "queued-time caption")
            gate = asyncio.Event()
            started = asyncio.Event()
            prompts: list[str] = []

            async def fake(image_path, **kwargs):
                prompt = kwargs.get("custom_prompt", "")
                prompts.append(prompt)
                if not prompt:          # the first job: hold the queue
                    started.set()
                    await gate.wait()
                    return "written by the first job"
                return f"<{prompt}>"

            import backend.ml.openai_compat_captioner as mod
            monkeypatch.setattr(mod, "caption_image", fake)

            first = await env.client.post(f"{API}/captioning/run", json={
                "dataset_id": ds["id"], "model": model, "overwrite": True,
            })
            await asyncio.wait_for(started.wait(), 10)
            second = await env.client.post(f"{API}/captioning/run", json={
                "dataset_id": ds["id"], "model": model, "overwrite": True,
                "custom_prompt": "fix: {previous_caption}",
            })
            gate.set()
            for r in (first, second):
                job = await wait_for_job(env, r.json()["job_id"])
                assert job["status"] == "completed", job

            assert prompts == ["", "fix: written by the first job"]
            assert await _captions(env, ds["id"]) == {"i0.png": "<fix: written by the first job>"}

    run(scenario())


def test_pipeline_step_zero_sees_the_stored_caption(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, n=1)
            await _set_caption(env, imgs[0]["id"], "stored")
            # Each step's reply wraps its prompt, so step 1's prompt shows step 0's output.
            fake = _install(monkeypatch, reply=lambda name, prompt: f"<{prompt}>")

            await _finish(env, "pipeline", {
                "dataset_id": ds["id"],
                "steps": [
                    {"model": model, "custom_prompt": "one: {previous_caption}"},
                    {"model": model, "custom_prompt": "two: {previous_caption}"},
                ],
            })

            assert fake.prompts == {"i0.png": ["one: stored", "two: <one: stored>"]}
            assert await _captions(env, ds["id"]) == {"i0.png": "<two: <one: stored>>"}

    run(scenario())


def test_fix_mode_body_replaces_a_tag_caption(tmp_path, monkeypatch):
    """The body ImageDetailPage's Fix mode sends, with a stand-in prompt (the real one is
    `buildFixPrompt` in `frontend/src/constants/captionModels.ts`; its body is pinned by
    `frontend/e2e/detail-caption-fix.spec.ts`): the corrected tags replace the old ones.
    `delimiter_mode: "overwrite"` is what stops the tag merge; `append_tags: false` is
    belt and braces, so this would also pass with it true."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, n=1)
            await _set_caption(env, imgs[0]["id"], "1girl, broken glass, window")
            fake = _install(monkeypatch, reply=lambda name, prompt: "1girl, broken mirror, reflection")

            await _finish(env, "run", {
                "dataset_id": ds["id"], "model": model, "image_ids": [imgs[0]["id"]],
                "style": "tags", "overwrite": True, "append_tags": False,
                "delimiter_mode": "overwrite",
                "custom_prompt": "Caption: {previous_caption}\nCorrection: it is a mirror",
            })

            assert fake.prompts == {
                "i0.png": ["Caption: 1girl, broken glass, window\nCorrection: it is a mirror"],
            }
            assert await _captions(env, ds["id"]) == {"i0.png": "1girl, broken mirror, reflection"}

    run(scenario())
