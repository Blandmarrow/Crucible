"""An automated run's paid-call cap counts caption calls (M9 stage 10, PM-037), over real
`BackgroundJob` rows and the real caption job body. Nothing here stubs `_spent`.

What is pinned (docs/dev/comfyui/run-budgets.md § Budgets, docs/dev/captioning.md):
- the caption job writes `done_items` after each call, whatever the call returned, so
  the count survives a completed, a failed and a cancelled job alike;
- `_spent` sums it over the run's `caption` children in every end state, an interrupted
  one included, and only when the caption provider is billed;
- a run with a cap of N makes N caption calls and no more, across a failed job, a job
  cancelled from outside and the resumes after them;
- a resumed caption stage captions only what is left: an image its model has already
  captioned is not sent again, and a stage with nothing left is skipped.

Torch-free and cv2-free: the provider call is faked, and nothing scores or gates.
"""
import asyncio
from pathlib import Path

from sqlalchemy import select

from backend.models import BackgroundJob, Image, Run
from backend.schemas.run import RunRecipe
from backend.services import run_service
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.tests.test_run_loop import GOOD, _loop, _loop_plan, _spec
from backend.tests.test_run_stepping import _start, wait_for_run

GOLD = {**GOOD, "id": "g", "label": "Gold Knight"}
CAPTION = "a faked caption"
RUN_STAGES = ["prompts", "generate", "caption"]


def _fake_captioner(monkeypatch, behave):
    """Fake the provider call. `behave(n)` is awaited for the n-th call (from 1) and
    returns the caption or raises. Returns the list of files called for, in order."""
    import backend.ml.openai_compat_captioner as mod

    calls: list[str] = []

    async def _fake(image_path, **kwargs):
        calls.append(Path(image_path).name)
        return await behave(len(calls))

    monkeypatch.setattr(mod, "caption_image", _fake)
    return calls


def _break_next_save(monkeypatch) -> dict:
    """Make `set_caption` raise once each time `state["armed"]` is set. A raise there is
    outside the job's per-image `try`, so it fails the whole job after a call was made."""
    from backend.services import caption_service

    real = caption_service.set_caption
    state = {"armed": False}

    async def _set_caption(*args, **kwargs):
        if state["armed"]:
            state["armed"] = False
            raise RuntimeError("the caption could not be saved")
        return await real(*args, **kwargs)

    monkeypatch.setattr(caption_service, "set_caption", _set_caption)
    return state


async def _provider(env, *, billed: bool) -> str:
    """A caption model id. A remote base URL is billed (`provider_is_billed`); a local one is not."""
    r = await env.client.post(f"{API}/providers/", json={
        "name": "cloud" if billed else "lmstudio",
        "base_url": "https://api.example.com/v1" if billed else "http://127.0.0.1:1234/v1",
        "default_model": "vl",
    })
    assert r.status_code == 201, r.text
    return f"openai_compat:{r.json()['id']}:vl"


async def _dataset(env, n: int) -> dict:
    ds = await env.create_dataset("d")
    for i in range(n):
        await upload_image(env, ds["id"], f"i{i}.png", png_bytes(color=(i * 40, 10, 10)))
    return ds


async def _caption(env, ds_id: str, model: str, **body) -> str:
    r = await env.client.post(f"{API}/captioning/run", json={"dataset_id": ds_id, "model": model, **body})
    assert r.status_code == 200, r.text
    assert r.json()["job_id"], r.text
    return r.json()["job_id"]


async def _caption_jobs(env) -> list[BackgroundJob]:
    async with env.Session() as db:
        return list((await db.execute(
            select(BackgroundJob).where(BackgroundJob.job_type == "caption").order_by(BackgroundJob.created_at)
        )).scalars())


async def _captioned_by(env, ds_id: str, model: str) -> set[str]:
    async with env.Session() as db:
        return set((await db.execute(
            select(Image.id).where(Image.dataset_id == ds_id, Image.captioned_by == model)
        )).scalars())


async def _cancel_caption_job(env, run_id: str) -> None:
    """Cancel the run's running caption job from outside the run, as the Logs page does."""
    r = await env.client.get(f"{API}/runs/{run_id}")
    job_id = next(s for s in r.json()["stages"] if s["key"] == "caption" and s["status"] == "running")["job_id"]
    assert (await env.client.delete(f"{API}/jobs/{job_id}")).status_code == 204


async def _resume(env, run_id: str) -> dict:
    r = await env.client.post(f"{API}/runs/{run_id}/resume")
    assert r.status_code == 200, r.text
    return await wait_for_run(env, run_id, timeout=60)


def _caption_stage(run_out: dict) -> dict:
    return [s for s in run_out["stages"] if s["key"] == "caption"][-1]


# ── The caption job records each call ────────────────────────────────────────

def test_a_caption_job_counts_every_call_it_made(tmp_path, monkeypatch):
    """A clean job counts them all, and so does one whose every call failed: the
    provider was still asked each time."""
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 3)
            model = await _provider(env, billed=False)

            async def ok(n):
                return CAPTION

            _fake_captioner(monkeypatch, ok)
            job = await wait_for_job(env, await _caption(env, ds["id"], model))
            assert (job["status"], job["done_items"], job["total_items"]) == ("completed", 3, 3), job

            async def down(n):
                raise RuntimeError("provider is down")

            _fake_captioner(monkeypatch, down)
            # `overwrite`, or the three captions just written leave nothing in scope.
            job = await wait_for_job(env, await _caption(env, ds["id"], model, overwrite=True))
            assert (job["status"], job["done_items"]) == ("completed", 3), job
            assert job["result_data"]["failed_count"] == 3, job
    run(go())


def test_a_cancelled_caption_job_keeps_the_calls_it_made(tmp_path, monkeypatch):
    """The stop lands between images, so the call in flight when it was asked for
    finishes and is counted. The third image is never sent."""
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 3)
            model = await _provider(env, billed=False)
            in_flight, release = asyncio.Event(), asyncio.Event()

            async def behave(n):
                if n == 2:
                    in_flight.set()
                    await release.wait()
                return CAPTION

            calls = _fake_captioner(monkeypatch, behave)
            job_id = await _caption(env, ds["id"], model)
            await asyncio.wait_for(in_flight.wait(), 20)
            assert (await env.client.delete(f"{API}/jobs/{job_id}")).status_code == 204
            release.set()
            job = await wait_for_job(env, job_id)
            assert (job["status"], job["done_items"]) == ("cancelled", 2), job
            assert len(calls) == 2
    run(go())


def test_a_caption_job_that_fails_after_a_call_still_counts_it(tmp_path, monkeypatch):
    """The count is written when the call comes back, before the caption is saved, so
    a save that fails the whole job does not lose the call that was paid for."""
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 3)
            model = await _provider(env, billed=False)
            save = _break_next_save(monkeypatch)

            async def behave(n):
                save["armed"] = n == 2
                return CAPTION

            calls = _fake_captioner(monkeypatch, behave)
            job = await wait_for_job(env, await _caption(env, ds["id"], model))
            assert (job["status"], job["done_items"]) == ("failed", 2), job
            assert "could not be saved" in job["error_msg"]
            assert len(calls) == 2
    run(go())


# ── `_spent` over real rows ──────────────────────────────────────────────────

def test_spent_counts_caption_calls_from_every_way_a_job_can_end(tmp_path):
    from backend.workers.job_queue import INTERRUPTED_MSG, mark_interrupted_jobs

    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("spent-ds")
            paid = await _provider(env, billed=True)
            free = await _provider(env, billed=False)
            pid = paid.split(":")[1]

            def recipe(model):
                return RunRecipe.model_validate({
                    "stages": ["prompts", "rewrite", "generate", "caption"],
                    "caption": {"model": model},
                    "rewrite": {"provider_id": pid, "scenarios": ["solo"]},
                    "loop": _loop(max_images=50, max_paid_calls=100),
                })

            async with env.Session() as db:
                mine = Run(dataset_id=ds["id"], plan_id=None, name="mine", recipe={}, stages=[], context={}, status="paused")
                other = Run(dataset_id=ds["id"], plan_id=None, name="other", recipe={}, stages=[], context={}, status="paused")
                db.add_all([mine, other])
                await db.commit()
                for status, done in (("completed", 4), ("failed", 3), ("cancelled", 2), ("running", 1)):
                    db.add(BackgroundJob(job_type="caption", status=status, total_items=5, done_items=done,
                                         config={"run_id": mine.id}))
                # Not this run's, and not a caption job: neither is a caption call of this run.
                db.add(BackgroundJob(job_type="caption", status="completed", total_items=9, done_items=9,
                                     config={"run_id": other.id}))
                db.add(BackgroundJob(job_type="quality_score", status="completed", total_items=7, done_items=7,
                                     config={"run_id": mine.id}))
                db.add(BackgroundJob(job_type="comfy_rewrite", status="failed", result_data={"calls": 5},
                                     config={"run_id": mine.id}))
                await db.commit()

            # A restart: the job left running is marked interrupted, and keeps its count.
            await mark_interrupted_jobs()

            async with env.Session() as db:
                mine = await db.get(Run, mine.id)
                rows = (await db.execute(select(BackgroundJob).where(BackgroundJob.error_msg == INTERRUPTED_MSG))).scalars().all()
                assert [(r.status, r.done_items) for r in rows] == [("failed", 1)]
                spent = await run_service._spent(db, mine, recipe(paid))
                assert (spent.caption_calls, spent.rewrite_calls, spent.paid_calls) == (10, 5, 15)
                # A local caption model bills nothing, whatever its jobs counted.
                spent = await run_service._spent(db, mine, recipe(free))
                assert (spent.caption_calls, spent.rewrite_calls) == (0, 5)
    run(go())


# ── The cap across a failed job, a cancelled job and the resumes ─────────────

def test_a_run_makes_no_more_caption_calls_than_its_cap_across_stopped_jobs(tmp_path, monkeypatch):
    """Four images and a cap of five. The first caption job fails after one call, the
    second is cancelled from outside after three more (one of them a failed call), and
    the last resume has one call left for the two images still without a caption."""
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, GOLD, count=2))
            model = await _provider(env, billed=True)
            save = _break_next_save(monkeypatch)
            in_flight, release = asyncio.Event(), asyncio.Event()

            async def behave(n):
                if n == 1:
                    save["armed"] = True     # the job fails once this call has come back
                if n == 3:
                    raise RuntimeError("provider is down")
                if n == 4:
                    in_flight.set()
                    await release.wait()
                return CAPTION

            calls = _fake_captioner(monkeypatch, behave)
            code, body = await _start(env, ds["id"], plan_id, RUN_STAGES,
                                      caption={"model": model}, loop=_loop(max_images=4, max_paid_calls=5))
            assert code == 200, body
            run_id = body["id"]

            # 1. The job fails: one call made, nothing saved.
            out = await wait_for_run(env, run_id, timeout=60)
            assert out["status"] == "failed" and "could not be saved" in out["error_msg"], out
            assert [(j.status, j.done_items) for j in await _caption_jobs(env)] == [("failed", 1)]
            assert await _captioned_by(env, ds["id"], model) == set()

            # 2. Resume: four calls are left and all four images still need one. The job
            #    is cancelled from outside during its third call, which then finishes.
            r = await env.client.post(f"{API}/runs/{run_id}/resume")
            assert r.status_code == 200, r.text
            await asyncio.wait_for(in_flight.wait(), 30)
            await _cancel_caption_job(env, run_id)
            release.set()
            out = await wait_for_run(env, run_id, timeout=60)
            assert out["status"] == "failed" and "cancelled outside the run" in out["error_msg"], out
            jobs = await _caption_jobs(env)
            assert [(j.status, j.done_items, j.total_items) for j in jobs] == [("failed", 1, 4), ("cancelled", 3, 4)]
            done_before = await _captioned_by(env, ds["id"], model)
            assert len(done_before) == 2          # the failed call's image is not among them

            # 3. Resume: 5 − 4 leaves one call, for the two images still without a caption.
            out = await _resume(env, run_id)
            assert out["status"] == "completed", out
            assert _caption_stage(out)["note"] == "The paid-call cap leaves 1 calls, so 1 of 2 images are not captioned"
            jobs = await _caption_jobs(env)
            assert [(j.status, j.done_items, j.total_items) for j in jobs][-1] == ("completed", 1, 1)
            assert done_before.isdisjoint(jobs[-1].config["image_ids"])

            # The cap held: five calls reached the provider, and five are on the rows.
            assert len(calls) == 5
            assert sum(j.done_items for j in jobs) == 5
            assert len(await _captioned_by(env, ds["id"], model)) == 3
    run(go())


# ── A resumed caption stage captions only what is left ───────────────────────

def test_a_resumed_caption_stage_skips_what_its_model_already_captioned(tmp_path, monkeypatch):
    """No cap here (a local model): the rule saves the time, and with `overwrite` on it
    keeps a resume from captioning the same image twice."""
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, GOLD, count=2))
            model = await _provider(env, billed=False)
            in_flight, release = asyncio.Event(), asyncio.Event()

            async def behave(n):
                if n == 2:
                    in_flight.set()
                    await release.wait()
                return CAPTION

            calls = _fake_captioner(monkeypatch, behave)
            code, body = await _start(env, ds["id"], plan_id, RUN_STAGES,
                                      caption={"model": model}, loop=_loop(max_images=4))
            assert code == 200, body
            run_id = body["id"]
            await asyncio.wait_for(in_flight.wait(), 30)
            await _cancel_caption_job(env, run_id)
            release.set()
            out = await wait_for_run(env, run_id, timeout=60)
            assert out["status"] == "failed", out
            done_before = await _captioned_by(env, ds["id"], model)
            assert len(done_before) == 2

            out = await _resume(env, run_id)
            assert out["status"] == "completed", out
            first, second = await _caption_jobs(env)
            assert (first.total_items, second.total_items) == (4, 2)
            assert done_before.isdisjoint(second.config["image_ids"])
            assert len(await _captioned_by(env, ds["id"], model)) == 4
            assert len(calls) == 4 and len(set(calls)) == 4      # no image was sent twice
    run(go())


def test_a_resumed_caption_stage_with_nothing_left_is_skipped(tmp_path, monkeypatch):
    """Every image is captioned, then the job fails in its tail. The resume must not
    start a job with no ids: `[]` would widen to the whole dataset."""
    from backend.services import dataset_service

    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, GOLD, count=2))
            model = await _provider(env, billed=False)
            real = dataset_service.refresh_stats
            state = {"armed": False}

            async def _refresh_stats(*args, **kwargs):
                if state["armed"]:
                    state["armed"] = False
                    raise RuntimeError("the stats refresh failed")
                return await real(*args, **kwargs)

            monkeypatch.setattr(dataset_service, "refresh_stats", _refresh_stats)

            async def behave(n):
                state["armed"] = n == 4
                return CAPTION

            calls = _fake_captioner(monkeypatch, behave)
            code, body = await _start(env, ds["id"], plan_id, RUN_STAGES,
                                      caption={"model": model}, loop=_loop(max_images=4))
            assert code == 200, body
            run_id = body["id"]
            out = await wait_for_run(env, run_id, timeout=60)
            assert out["status"] == "failed" and "stats refresh failed" in out["error_msg"], out
            assert len(await _captioned_by(env, ds["id"], model)) == 4

            out = await _resume(env, run_id)
            assert out["status"] == "completed", out
            stage = _caption_stage(out)
            assert stage["status"] == "skipped", stage
            assert stage["note"] == "Nothing left to caption: every image already has this model's caption"
            assert len(await _caption_jobs(env)) == 1 and len(calls) == 4
    run(go())
