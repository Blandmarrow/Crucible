"""Carrying on a run that stopped on a cap (M6 §6): `run_service.extend_run`.

What is pinned (docs/dev/comfyui/run-budgets.md § Raising a cap and carrying on):
- a loop stopped by a cap records which (`context.loop.cap`) and offers `can_extend`;
- carrying on refuses a cap that goes down, a stopping cap not raised, another live
  run on the dataset, and a run that ended any other way;
- the next pass and a fresh tail are appended, and the new tail captions only the new
  pass's images: `overwrite` is on, so captioning them all again would bill again;
- the rewrite's reserve for captioning counts the captions already made, or a carried
  on run with a billed captioner would stop its rewrite at once.

Torch-free and cv2-free: the image cap stops the loop, so nothing scores or gates.
"""
from sqlalchemy import select

from backend.models import BackgroundJob, Image, Run
from backend.models.comfy import ComfyPlan
from backend.schemas.run import RunRecipe
from backend.services import run_service
from backend.tests.conftest import API, api_env, run
from backend.tests.test_run_loop import GOOD, _loop, _loop_plan, _spec
from backend.tests.test_run_stepping import _block_worker, _caption_model, _start, wait_for_run

GOLD = {**GOOD, "id": "g", "label": "Gold Knight"}


async def _caption_jobs(env) -> list[BackgroundJob]:
    async with env.Session() as db:
        return list((await db.execute(
            select(BackgroundJob).where(BackgroundJob.job_type == "caption").order_by(BackgroundJob.created_at)
        )).scalars())


def test_a_run_stopped_by_a_cap_carries_on_and_captions_only_its_new_images(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, GOLD, count=2))
            model = await _caption_model(env, monkeypatch)
            # Two folders of two; the image cap lets pass 1 make one in each, then stops.
            code, body = await _start(env, ds["id"], plan_id, ["prompts", "generate", "caption"],
                                      caption={"model": model}, loop=_loop(max_images=2))
            assert code == 200, body
            run_id = body["id"]
            done = await wait_for_run(env, run_id, timeout=60)
            assert done["status"] == "completed", done
            assert done["outcome"] == {"outcome": "short", "reason": "the image cap (2) is reached", "cap": "images"}
            assert done["can_extend"] is True
            url = f"{API}/runs/{run_id}/extend"

            # The cap that stopped it must go up, and none may go down.
            r = await env.client.post(url, json=_loop(max_images=2))
            assert r.status_code == 400 and "image cap" in r.json()["detail"], r.text
            r = await env.client.post(url, json=_loop(max_images=4, max_passes=1))
            assert r.status_code == 400 and "pass cap cannot go down" in r.json()["detail"], r.text

            # Not while another run on the dataset is live.
            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            gate = await _block_worker(env)
            code, other = await _start(env, ds["id"], plan_id, ["snapshot"])
            assert code == 200 and other["status"] == "running", other
            r = await env.client.post(url, json=_loop(max_images=4))
            assert r.status_code == 409, r.text
            assert (await env.client.post(f"{API}/runs/{other['id']}/cancel")).status_code == 200
            gate.set()

            r = await env.client.post(url, json=_loop(max_images=4))
            assert r.status_code == 200, r.text
            assert r.json()["status"] in ("running", "completed")
            done = await wait_for_run(env, run_id, timeout=60)
            assert done["status"] == "completed", done
            assert done["outcome"] == {"outcome": "met", "reason": None}
            assert done["can_extend"] is False
            assert done["recipe"]["loop"]["max_images"] == 4
            keys = [s["key"] for s in done["stages"]]
            assert keys.count("caption") == 2 and keys[-1] == "caption"
            assert {s["pass_no"] for s in done["stages"] if s["pass_no"]} == {1, 2}

            # The second tail captioned the new pass's two images, not all four again.
            first, second = await _caption_jobs(env)
            assert (first.total_items, second.total_items) == (2, 2)
            assert set(first.config["image_ids"]).isdisjoint(second.config["image_ids"])
            async with env.Session() as db:
                n = len(list((await db.execute(select(Image.id).where(Image.dataset_id == ds["id"]))).scalars()))
            assert n == 4

            # A run whose folders are all full has nothing to carry on.
            r = await env.client.post(url, json=_loop(max_images=8))
            assert r.status_code == 409, r.text
    run(go())


def test_only_a_run_stopped_by_a_cap_with_a_folder_short_can_carry_on():
    def make(status="completed", loop=None, states=("open",)):
        return Run(status=status, context={
            "loop": loop, "leaves": {str(i): {"state": s} for i, s in enumerate(states)},
        })

    short = {"outcome": "short", "reason": "x", "cap": "passes"}
    assert run_service.can_extend(make(loop=short)) is True
    assert run_service.can_extend(make(loop={**short, "cap": None}, states=("abandoned",))) is False
    assert run_service.can_extend(make(loop=short, states=("met", "abandoned"))) is False
    assert run_service.can_extend(make(loop={"outcome": "met", "reason": None})) is False
    assert run_service.can_extend(make(status="failed", loop=short)) is False
    # A run from before §6 recorded no cap, so it cannot say what to raise.
    assert run_service.can_extend(make(loop={"outcome": "short", "reason": "x"})) is False


def test_the_rewrite_reserve_counts_captions_already_made(tmp_path, monkeypatch):
    """With a billed captioner the rewrite holds back a call for every image the loop
    may still caption. After a tail has captioned some, only the rest is held back."""
    from backend.routers import comfy as comfy_router

    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, rewrite=True))
            r = await env.client.post(f"{API}/providers/", json={
                "name": "paid", "base_url": "https://llm.example.com/v1", "default_model": "m",
            })
            assert r.status_code == 201, r.text
            pid = r.json()["id"]
            seen: dict = {}

            async def _start_rewrite(db, plan, **kw):
                seen.update(kw)
                return {"job_id": None}

            async def _yes(db, recipe):
                return True

            async def _spent(db, run_, recipe):
                return run_service._Spent(minutes=0, images=0, rewrite_calls=3, caption_calls=40)

            async def _no_stop(db, run_, recipe):
                return None

            monkeypatch.setattr(comfy_router, "start_rewrite_job", _start_rewrite)
            monkeypatch.setattr(run_service, "_rewrite_billed", _yes)
            monkeypatch.setattr(run_service, "_caption_billed", _yes)
            monkeypatch.setattr(run_service, "_spent", _spent)
            monkeypatch.setattr(run_service, "_budget_stop", _no_stop)
            recipe = RunRecipe.model_validate({
                "stages": ["prompts", "rewrite", "generate", "caption"],
                "caption": {"model": f"openai_compat:{pid}:m"},
                "rewrite": {"provider_id": pid, "scenarios": ["solo"]},
                "loop": _loop(max_images=50, max_paid_calls=100),
            })
            async with env.Session() as db:
                plan = await db.get(ComfyPlan, plan_id)
                row = Run(dataset_id=ds["id"], plan_id=plan_id, name="x", recipe=recipe.model_dump(),
                          stages=[run_service._entry("rewrite", 1)], context={"passes": [["r1"]], "row_ids": ["r1"]},
                          status="running")
                db.add(row)
                await db.commit()
                await run_service._stage_rewrite(db, row, plan, recipe, 0, "0:rewrite")
            # 100 cap − 43 spent − (50 − 40) still reserved = 47. Reserving all 50 left 7.
            assert seen["max_requests"] == 47
    run(go())
