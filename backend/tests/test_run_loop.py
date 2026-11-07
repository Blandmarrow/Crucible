"""The goal loop (M6 §5) end to end against FakeComfy in varied mode: passes repeat
until every folder holds its target of images that pass the gate, within hard caps.

What is pinned (docs/dev/comfyui/run-loop.md, run-gate.md and run-budgets.md):
- a pass is the body stages, inserted before the tail when a folder is still short;
- the gate flags and files rejects under `<base>/rejects/<leaf>`, and neither the
  fill, the caption tail nor the export counts them;
- a folder with no gain in two passes it had rows in is abandoned;
- the pass and image caps stop the loop, and the image cap clamps a pass round-robin;
- `next_pass` is idempotent; Apply and Top up refuse while a run is live;
- the seed and paid-call start checks; the rewrite stage inside the loop.

Torch-free through `conftest.stub_aesthetic_scorer`; the gate's blur test needs cv2
(`needs_cv2`), like the varied fake's.
"""
import copy
import functools

import pytest
from sqlalchemy import select

from backend.models import Image, Run
from backend.models.comfy import ComfyPlan, ComfyRow
from backend.schemas.run import RunRecipe
from backend.services import run_service
from backend.services.comfy_service import ComfyClient
from backend.tests.conftest import API, api_env, needs_cv2, run
from backend.tests.fake_comfy import BLURRY_MARKER, FakeComfy, transport
from backend.tests.test_blueprint_rewrite_http import _fake as _fake_rewrite
from backend.tests.test_comfy_run_body import PROMPT, _workflow
from backend.tests.test_run_stepping import _block_worker, _start, wait_for_run

# The Score stage's job imports the aesthetic scorer, which needs torch.
pytestmark = pytest.mark.usefixtures("stub_aesthetic_scorer")

GOOD ={"id": "k", "label": "Iron Knight", "descriptor": "an iron knight"}
BLURRY = {"id": "w", "label": "Salt Witch", "descriptor": f"a salt witch {BLURRY_MARKER}"}


def _spec(*characters, count=2, rewrite=False):
    return {
        "axes": {"character": list(characters)},
        "variations": {"camera": ["low angle", "wide shot"]},
        "scenarios": [{"name": "solo", "combine": ["character"], "count": count, "rewrite": rewrite,
                       "path": "knights/{character}", "template": "{character}, {camera}"}],
    }


def _seeded_workflow() -> dict:
    wf = _workflow(("9", "SaveImage"))
    wf["3"] = {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 4}}
    return wf


PROMPT_PIN = {"node_id": "6", "input": "text", "alias": PROMPT, "is_prompt": True}
SEED_RANDOM = {"node_id": "3", "input": "seed", "alias": "seed", "is_prompt": False,
               "per_row": False, "value": None, "int_mode": "random"}


async def _loop_plan(env, monkeypatch, spec, *, seed_pin=SEED_RANDOM, workflow=None, name="loop-ds", outputs=("9",)):
    import backend.routers.comfy as comfy_router

    fake = FakeComfy(canvas_running=False, complete=True, varied=True)
    monkeypatch.setattr(comfy_router, "ComfyClient", functools.partial(ComfyClient, transport=transport(fake)))
    r = await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fakecomfy:8188"})
    assert r.status_code == 200, r.text
    ds = await env.create_dataset(name)
    pins = [PROMPT_PIN] + ([seed_pin] if seed_pin else [])
    r = await env.client.post(f"{API}/comfy/plans", json={
        "dataset_id": ds["id"], "name": "plan", "workflow_json": workflow or _seeded_workflow(),
        "pinned_params": pins, "output_node_ids": list(outputs),
    })
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]
    r = await env.client.put(f"{API}/comfy/plans/{plan_id}/blueprint", json={"spec": spec, "seed": 3})
    assert r.status_code == 200, r.text
    return fake, ds, plan_id


LOOP_STAGES = ["prompts", "generate", "score", "gate"]


def _loop(**over):
    return {"max_passes": 5, "max_images": 200, "max_minutes": 60, **over}


async def _images(env, ds_id) -> list[Image]:
    async with env.Session() as db:
        return list((await db.execute(select(Image).where(Image.dataset_id == ds_id))).scalars())


@needs_cv2
def test_a_loop_refills_rejects_until_it_gives_up_on_a_stuck_folder(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, BLURRY))
            out_dir = tmp_path / "export"
            code, body = await _start(
                env, ds["id"], plan_id, LOOP_STAGES + ["export"],
                generate={"subfolder": "gen"}, gate={"exclude_flags": ["is_blurry", "is_duplicate"]},
                loop=_loop(), export={"kind": "plain", "output_dir": str(out_dir)},
            )
            assert code == 200, body
            done = await wait_for_run(env, body["id"], timeout=90)
            assert done["status"] == "completed", done
            assert done["outcome"]["outcome"] == "short", done["outcome"]
            states = {l["path"]: l["state"] for l in done["leaves"]}
            assert states == {"knights/iron_knight": "met", "knights/salt_witch": "abandoned"}

            # Pass 1 made 2 + 2; the witch kept nothing, so pass 2 planned at the yield
            # floor (2 / 0.25 = 8 rows), kept nothing again and was abandoned.
            passes = {s["pass_no"] for s in done["stages"] if s["pass_no"]}
            assert passes == {1, 2}
            assert [s["key"] for s in done["stages"]][-1] == "export"
            assert done["stages"][-1]["status"] == "done"

            imgs = await _images(env, ds["id"])
            good = [i for i in imgs if i.subfolder == "gen/knights/iron_knight"]
            rejects = [i for i in imgs if i.subfolder == "gen/rejects/knights/salt_witch"]
            assert len(good) == 2 and len(rejects) == 2 + 8 and len(imgs) == 12
            assert all(i.quality_flags["gate_rejected"] for i in rejects)
            assert all("is_blurry" in i.quality_flags["gate_reasons"] for i in rejects)
            assert not any((i.quality_flags or {}).get("gate_rejected") for i in good)
            # The export leaves rejects out by default.
            assert len(list(out_dir.rglob("*.png"))) == 2
    run(go())


@needs_cv2
def test_a_loop_whose_first_pass_fills_every_folder_runs_once(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD))
            code, body = await _start(env, ds["id"], plan_id, LOOP_STAGES,
                                      gate={"exclude_flags": ["is_blurry", "is_duplicate"]}, loop=_loop())
            assert code == 200, body
            done = await wait_for_run(env, body["id"], timeout=60)
            assert done["status"] == "completed", done
            assert done["outcome"] == {"outcome": "met", "reason": None}
            assert {s["pass_no"] for s in done["stages"]} == {1}
            assert done["stages"][-1]["summary"] == "Pass 1: every folder is full (1)"
    run(go())


@needs_cv2
def test_the_pass_cap_stops_the_loop_and_next_pass_is_idempotent(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, BLURRY))
            code, body = await _start(env, ds["id"], plan_id, LOOP_STAGES,
                                      gate={"exclude_flags": ["is_blurry"]}, loop=_loop(max_passes=1))
            assert code == 200, body
            done = await wait_for_run(env, body["id"], timeout=60)
            assert done["outcome"] == {"outcome": "short", "reason": "the pass cap (1) is reached", "cap": "passes"}

            # Run `next_pass` again by hand, as a restart or resume would, with a
            # higher cap: it inserts pass 2 once, and does not recount pass 1.
            async with env.Session() as db:
                r = await db.get(Run, body["id"])
                plan = await db.get(ComfyPlan, plan_id)
                recipe = RunRecipe.model_validate({**r.recipe, "loop": _loop(max_passes=5)})
                idx = next(i for i, s in enumerate(r.stages) if s["key"] == "next_pass")
                before = copy.deepcopy(r.context["leaves"])
                for _ in range(2):
                    await run_service._stage_next_pass(db, r, plan, recipe, idx)
                assert sum(1 for s in r.stages if s.get("pass_no") == 2) == len(LOOP_STAGES) + 1
                assert r.context["leaves"] == before
    run(go())


@needs_cv2
def test_the_image_cap_clamps_a_pass_round_robin_and_stops_the_loop(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, {**GOOD, "id": "g", "label": "Gold Knight"}))
            code, body = await _start(env, ds["id"], plan_id, LOOP_STAGES,
                                      gate={"exclude_flags": ["is_blurry"]}, loop=_loop(max_images=3))
            assert code == 200, body
            done = await wait_for_run(env, body["id"], timeout=60)
            assert done["stages"][0]["summary"] == "4 rows added, 0 reset, 3 to generate (the image cap leaves room for 3 of 4)"
            assert done["outcome"] == {"outcome": "short", "reason": "the image cap (3) is reached", "cap": "images"}
            # Round-robin: both folders got rows, so neither is starved of the budget.
            async with env.Session() as db:
                rows = (await db.execute(select(ComfyRow).where(ComfyRow.plan_id == plan_id))).scalars().all()
            done_by_folder: dict[str, int] = {}
            for row in rows:
                if row.status == "completed":
                    done_by_folder[row.subfolder] = done_by_folder.get(row.subfolder, 0) + 1
            assert sorted(done_by_folder.values()) == [1, 2]
    run(go())


def test_a_repeating_run_needs_its_seed_pinned_random(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            cases = ((None, False), ({**SEED_RANDOM, "int_mode": "fixed"}, False), (SEED_RANDOM, True))
            for n, (pin, ok) in enumerate(cases):
                _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD), seed_pin=pin, name=f"ds{n}")
                code, body = await _start(env, ds["id"], plan_id, ["prompts", "generate"], loop=_loop())
                if ok:
                    assert code == 200, body
                    await wait_for_run(env, body["id"], timeout=60)
                else:
                    assert code == 400, body
                    assert "KSampler #3 (seed)" in body["detail"]
    run(go())


def test_a_billed_provider_needs_a_paid_call_cap_that_covers_captioning(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD))
            r = await env.client.post(f"{API}/providers/", json={
                "name": "cloud", "base_url": "https://api.example.com/v1", "default_model": "vl",
            })
            assert r.status_code == 201, r.text
            model = f"openai_compat:{r.json()['id']}:vl"
            stages = ["prompts", "generate", "caption"]
            code, body = await _start(env, ds["id"], plan_id, stages, caption={"model": model}, loop=_loop(max_images=10))
            assert code == 400 and "needs a paid-call cap" in body["detail"], body
            code, body = await _start(env, ds["id"], plan_id, stages, caption={"model": model},
                                      loop=_loop(max_images=10, max_paid_calls=5))
            assert code == 400 and "must cover captioning up to 10 images" in body["detail"], body
            # A local provider is not billed, so it needs no cap.
            r = await env.client.post(f"{API}/providers/", json={
                "name": "local", "base_url": "http://127.0.0.1:1234/v1", "default_model": "vl",
            })
            local = f"openai_compat:{r.json()['id']}:vl"
            code, body = await _start(env, ds["id"], plan_id, ["prompts", "generate"], loop=_loop(),
                                      caption={"model": local})
            assert code == 200, body
            await wait_for_run(env, body["id"], timeout=60)
    run(go())


def test_apply_and_top_up_refuse_while_a_run_on_the_plan_is_live(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD))
            gate = await _block_worker(env)
            try:
                code, body = await _start(env, ds["id"], plan_id, ["prompts", "generate"])
                assert code == 200, body
                spec = {"spec": _spec(GOOD), "seed": 3}
                r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/apply", json={**spec, "confirm_total": 0})
                assert r.status_code == 409 and "automated run" in r.text, r.text
                r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/top-up", json={**spec, "confirm_total": 0})
                assert r.status_code == 409 and "automated run" in r.text, r.text
                r = await env.client.post(f"{API}/runs/{body['id']}/cancel")
                assert r.status_code == 200, r.text
            finally:
                gate.set()
    run(go())


def test_the_rewrite_stage_rewrites_each_pass_and_a_failed_folder_sits_out(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            spec = _spec(GOOD, {**GOOD, "id": "g", "label": "Gold Knight"}, count=1, rewrite=True)
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, spec)
            r = await env.client.post(f"{API}/providers/", json={
                "name": "llm", "base_url": "http://127.0.0.1:9/v1", "default_model": "m",
            })
            provider_id = r.json()["id"]
            # The first folder's call is rejected; the second's succeeds.
            _fake_rewrite(monkeypatch, "rejected")
            code, body = await _start(
                env, ds["id"], plan_id, ["prompts", "rewrite", "generate"],
                rewrite={"provider_id": provider_id, "scenarios": ["solo"]}, loop=_loop(max_passes=1),
            )
            assert code == 200, body
            done = await wait_for_run(env, body["id"], timeout=60)
            assert done["status"] == "completed", done
            st = {s["key"]: s for s in done["stages"]}
            assert st["rewrite"]["status"] == "done", st["rewrite"]
            assert st["rewrite"]["summary"].startswith("1 prompt rewritten, 1 folder failed")
            async with env.Session() as db:
                rows = (await db.execute(select(ComfyRow).where(ComfyRow.plan_id == plan_id))).scalars().all()
            # The rewritten row ran; the failed folder's row sat the pass out as template text.
            assert sorted((r.prompt_origin, r.status) for r in rows) == [("rewritten", "completed"), ("template", "pending")]
            waiting = next(r for r in rows if r.prompt_origin == "template")
            assert waiting.image_ids in (None, [])
    run(go())


def test_a_rewrite_that_rewrote_nothing_fails_a_run_that_does_not_loop(tmp_path, monkeypatch):
    """No next pass retries a folder in a single-pass run, so PM-004's rule holds there:
    a rewrite that rewrote nothing fails, rather than the run ending green with
    nothing made."""
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, count=1, rewrite=True))
            r = await env.client.post(f"{API}/providers/", json={
                "name": "llm", "base_url": "http://127.0.0.1:9/v1", "default_model": "m",
            })
            _fake_rewrite(monkeypatch, "rejected", "rejected", "rejected", "rejected")
            code, body = await _start(
                env, ds["id"], plan_id, ["prompts", "rewrite", "generate"],
                rewrite={"provider_id": r.json()["id"], "scenarios": ["solo"]},
            )
            assert code == 200, body
            done = await wait_for_run(env, body["id"], timeout=60)
            assert done["status"] == "failed", done
            assert {s["key"]: s["status"] for s in done["stages"]}["rewrite"] == "failed"
    run(go())


def test_a_stopped_pass_still_frees_comfyuis_vram_and_downtime_is_not_work(tmp_path):
    """`_stop_loop` keeps a handoff whose partner ran this pass; `_spent` does not count
    a job the restart marked interrupted, whose span includes the downtime."""
    from datetime import datetime, timedelta

    from backend.models import BackgroundJob
    from backend.workers.job_queue import INTERRUPTED_MSG

    stages = [run_service._entry(k, 1) for k in ("prompts", "handoff_to_comfy", "generate", "handoff_to_local", "score", "next_pass")]
    stages[0]["status"] = stages[1]["status"] = "done"
    r = Run(id="r", dataset_id="d", plan_id="p", name="r", recipe={}, stages=stages, context={}, status="running")
    run_service._stop_loop(r, 2, "the time cap (1 min) is spent", "minutes")
    assert [s["status"] for s in r.stages] == ["done", "done", "pending", "pending", "skipped", "skipped"]
    assert r.context["loop"] == {"outcome": "short", "reason": "the time cap (1 min) is spent", "cap": "minutes"}

    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("spent-ds")
            now = datetime.utcnow()
            async with env.Session() as db:
                run_row = Run(dataset_id=ds["id"], plan_id=None, name="r", recipe={}, stages=[], context={}, status="paused")
                db.add(run_row)
                await db.commit()
                for minutes, err in ((2, None), (600, INTERRUPTED_MSG)):
                    db.add(BackgroundJob(
                        job_type="comfy_generate", status="failed" if err else "completed", error_msg=err,
                        started_at=now - timedelta(minutes=minutes), finished_at=now,
                        config={"run_id": run_row.id},
                    ))
                await db.commit()
                recipe = RunRecipe.model_validate({"stages": ["prompts", "generate"], "loop": _loop()})
                spent = await run_service._spent(db, run_row, recipe)
            assert 1.9 < spent.minutes < 2.1
    run(go())


def test_a_billed_caption_tail_captions_what_the_cap_allows(tmp_path, monkeypatch):
    """Two images a row, one planned: the cap (= max_images, which the start check
    allows) covers one caption, so the tail captions one and says why."""
    async def go():
        async with api_env(tmp_path) as env:
            import backend.ml.openai_compat_captioner as mod

            async def _fake_caption(image_path, **kwargs):
                return "a stub caption"

            monkeypatch.setattr(mod, "caption_image", _fake_caption)
            wf = _seeded_workflow()
            wf["10"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}}
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, count=1), workflow=wf, outputs=("9", "10"))
            r = await env.client.post(f"{API}/providers/", json={
                "name": "cloud", "base_url": "https://api.example.com/v1", "default_model": "vl",
            })
            model = f"openai_compat:{r.json()['id']}:vl"
            code, body = await _start(env, ds["id"], plan_id, ["prompts", "generate", "caption"],
                                      caption={"model": model}, loop=_loop(max_images=1, max_paid_calls=1))
            assert code == 200, body
            done = await wait_for_run(env, body["id"], timeout=60)
            assert done["status"] == "completed", done
            cap = next(s for s in done["stages"] if s["key"] == "caption")
            assert cap["note"] == "The paid-call cap leaves 1 calls, so 1 of 2 images are not captioned", cap
    run(go())


@needs_cv2
def test_the_row_cap_holds_on_pass_one_and_lifts_from_pass_two(tmp_path, monkeypatch):
    """Pass 1 is the fill Preview shows, so it keeps a manual Apply's row cap; from pass 2
    the yield floor may plan past it (the user's ruling, 2026-11-04). The cap is cut to 5
    here: pass 1 adds 4 rows, and pass 2 asks 8 for the blurry folder."""
    from backend.services import blueprint_service

    monkeypatch.setattr(blueprint_service, "DEFAULT_ROW_CAP", 5)

    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, BLURRY))
            code, body = await _start(env, ds["id"], plan_id, LOOP_STAGES,
                                      gate={"exclude_flags": ["is_blurry"]}, loop=_loop(max_passes=2))
            assert code == 200, body
            done = await wait_for_run(env, body["id"], timeout=60)
            assert done["status"] == "completed", done
            assert {s["pass_no"] for s in done["stages"] if s["pass_no"]} == {1, 2}

            # Three folders' worth is over the cap on pass 1. Since M6 §6 the start check
            # runs the fill's count, so the start refuses and no run is written.
            _fake, ds2, plan2 = await _loop_plan(
                env, monkeypatch, _spec(GOOD, BLURRY, {**GOOD, "id": "g", "label": "Gold Knight"}), name="ds2",
            )
            code, body = await _start(env, ds2["id"], plan2, LOOP_STAGES,
                                      gate={"exclude_flags": ["is_blurry"]}, loop=_loop())
            assert code == 400, body
            assert "over the limit of 5" in body["detail"], body
            async with env.Session() as db:
                assert (await db.execute(select(Run).where(Run.dataset_id == ds2["id"]))).first() is None
    run(go())


@needs_cv2
def test_a_pass_that_made_no_rows_stops_the_loop(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, BLURRY))
            code, body = await _start(env, ds["id"], plan_id, LOOP_STAGES,
                                      gate={"exclude_flags": ["is_blurry"]}, loop=_loop(max_passes=1))
            assert code == 200, body
            done = await wait_for_run(env, body["id"], timeout=60)
            assert done["outcome"]["cap"] == "passes"

            # Re-run `next_pass` with room to go on, but a pass that submitted nothing:
            # folders are still open, so the loop stops instead of inserting pass 2.
            async with env.Session() as db:
                r = await db.get(Run, body["id"])
                plan = await db.get(ComfyPlan, plan_id)
                recipe = RunRecipe.model_validate({**r.recipe, "loop": _loop(max_passes=5)})
                idx = next(i for i, s in enumerate(r.stages) if s["key"] == "next_pass")
                assert any(e["state"] == "open" for e in r.context["leaves"].values())
                r.context = {**r.context, "passes": [[]], "row_ids": []}
                out = await run_service._stage_next_pass(db, r, plan, recipe, idx)
                assert out.kind == "done"
                assert r.context["loop"] == {"outcome": "short", "reason": "the pass made no rows", "cap": None}
                assert not any(s.get("pass_no") == 2 for s in r.stages)
    run(go())
