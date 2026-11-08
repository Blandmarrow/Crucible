"""Retry for a stopped job (M9 stage 8): `POST /jobs/{id}/retry` and `JobOut.retryable`.

A retry resubmits the job's stored config. For `quality_score` it turns on
`only_unscored`, so it resumes from the rows the stopped job already committed rather than
redoing them (the user's ruling, 2026-11-08). Only failed (interrupted included) and
cancelled jobs qualify, only types with a handler, and never a job an automated run owns.

The scorers are stubbed with `test_score_chunking`'s helpers; the real ones need torch.
"""
from backend.models import BackgroundJob
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.tests.test_score_chunking import _images, _install, _rows, _score
from backend.workers.job_queue import INTERRUPTED_MSG

_TECH_ONLY = {"run_aesthetic": False, "run_technical": True}


async def _job(env, **cols) -> str:
    """Plant a job row directly, as a previous process would have left it. Its
    `dataset_id` follows the config's, as `start_score_job` writes it."""
    defaults = {
        "job_type": "quality_score",
        "status": "failed",
        "dataset_id": (cols.get("config") or {}).get("dataset_id"),
    }
    async with env.Session() as db:
        job = BackgroundJob(**{**defaults, **cols})
        db.add(job)
        await db.commit()
        return job.id


async def _retry(env, job_id: str):
    return await env.client.post(f"{API}/jobs/{job_id}/retry")


async def _retryable(env) -> dict[str, bool]:
    r = await env.client.get(f"{API}/jobs/", params={"limit": 500})
    assert r.status_code == 200, r.text
    return {j["id"]: j["retryable"] for j in r.json()}


def test_retry_resumes_where_the_stopped_job_left_off(tmp_path, monkeypatch):
    def crash_on_second(n, paths):
        if n == 2:
            raise RuntimeError("simulated crash")
        return None

    calls = _install(monkeypatch, on_technical_call=crash_on_second)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 5)

            first = await _score(env, dataset_id=ds["id"], **_TECH_ONLY)
            job = await wait_for_job(env, first["job_id"], timeout=60)
            assert job["status"] == "failed", job
            # What a restart leaves: the startup sweep's message on a failed row.
            async with env.Session() as db:
                (await db.get(BackgroundJob, first["job_id"])).error_msg = INTERRUPTED_MSG
                await db.commit()
            assert (await _retryable(env))[first["job_id"]] is True

            done_before = {n for c in calls["technical"][:1] for n in c}
            r = await _retry(env, first["job_id"])
            assert r.status_code == 200, r.text
            out = r.json()
            # Two rows were committed before the crash, so three are left.
            assert out["total"] == 3

            retried = await wait_for_job(env, out["job_id"], timeout=60)
            assert retried["status"] == "completed", retried
            assert retried["label"].startswith("Retry: ")
            assert retried["config"]["only_unscored"] is True
            assert retried["config"]["retry_of"] == first["job_id"]
            assert "run_id" not in retried["config"]

            rows = (await _rows(env, imgs.values())).values()
            assert all(r.blur_score == 120.0 for r in rows)
            # The retry visited only the rows the first job left, never the finished ones.
            revisited = {n for c in calls["technical"][2:] for n in c}
            assert revisited == set(imgs) - done_before

            flags = await _retryable(env)
            assert flags[first["job_id"]] is True      # still failed, so still retryable
            assert flags[out["job_id"]] is False       # completed

            # Nothing left: a second retry queues nothing and says so.
            again = await _retry(env, first["job_id"])
            assert again.status_code == 200, again.text
            assert again.json() == {"job_id": None, "message": "Nothing left to score"}

    run(scenario())


def test_retry_of_a_two_check_job_resumes_each_check_on_its_own(tmp_path, monkeypatch):
    """The usual real case: aesthetic finished, technical stopped partway. The retry
    must skip aesthetic for every row and run technical on the rows left only."""
    def crash_on_second(n, paths):
        if n == 2:
            raise RuntimeError("simulated crash")
        return None

    calls = _install(monkeypatch, on_technical_call=crash_on_second)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 5)

            first = await _score(env, dataset_id=ds["id"], run_aesthetic=True, run_technical=True)
            assert (await wait_for_job(env, first["job_id"], timeout=60))["status"] == "failed"
            # Aesthetic runs first and finished; technical committed one chunk of two.
            assert sum(len(c) for c in calls["aesthetic"]) == 5
            aesthetic_calls_before = len(calls["aesthetic"])
            done_before = set(calls["technical"][0])

            r = await _retry(env, first["job_id"])
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 3
            retried = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert retried["status"] == "completed", retried

            # No aesthetic call at all on the retry, and technical saw only the rest.
            assert sum(len(c) for c in calls["aesthetic"][aesthetic_calls_before:]) == 0
            assert {n for c in calls["technical"][2:] for n in c} == set(imgs) - done_before
            rows = (await _rows(env, imgs.values())).values()
            assert all(r.aesthetic_score == 0.75 and r.blur_score == 120.0 for r in rows)

    run(scenario())


def test_retry_keeps_the_subfolder_scope(tmp_path, monkeypatch):
    calls = _install(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "root.png", png_bytes(color=(1, 2, 3)))
            await upload_image(env, ds["id"], "nested.png", png_bytes(color=(4, 5, 6)), subfolder="sub")
            job_id = await _job(env, config={"dataset_id": ds["id"], "subfolder": "sub", **_TECH_ONLY})

            r = await _retry(env, job_id)
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 1
            assert (await wait_for_job(env, r.json()["job_id"], timeout=60))["status"] == "completed"
            assert [n for c in calls["technical"] for n in c] == ["nested.png"]

    run(scenario())


def test_retry_keeps_the_selected_image_ids(tmp_path, monkeypatch):
    calls = _install(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 4)
            picked = [imgs["i1.png"], imgs["i3.png"]]
            job_id = await _job(env, config={"dataset_id": ds["id"], "image_ids": picked, **_TECH_ONLY})

            r = await _retry(env, job_id)
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 2
            assert (await wait_for_job(env, r.json()["job_id"], timeout=60))["status"] == "completed"
            assert sorted(n for c in calls["technical"] for n in c) == ["i1.png", "i3.png"]

    run(scenario())


def test_a_retry_of_a_retry_links_to_its_own_parent(tmp_path, monkeypatch):
    _install(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _images(env, ds["id"], 2)
            cfg = {"dataset_id": ds["id"], **_TECH_ONLY}
            original = await _job(env, config=cfg)
            first_retry = await _job(env, config={**cfg, "retry_of": original})

            r = await _retry(env, first_retry)
            assert r.status_code == 200, r.text
            done = await wait_for_job(env, r.json()["job_id"], timeout=60)
            # Replaced, not inherited: the guard keys on the job actually retried.
            assert done["config"]["retry_of"] == first_retry
            # One "Retry: " prefix, never two: the label is rebuilt, not copied.
            assert done["label"].startswith("Retry: Quality:")

    run(scenario())


def test_a_cancelled_job_can_be_retried(tmp_path, monkeypatch):
    _install(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _images(env, ds["id"], 3)
            job_id = await _job(
                env, status="cancelled", config={"dataset_id": ds["id"], **_TECH_ONLY}
            )
            assert (await _retryable(env))[job_id] is True
            r = await _retry(env, job_id)
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 3
            done = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert done["status"] == "completed", done

    run(scenario())


def test_only_mismatched_is_resubmitted_as_it_was(tmp_path, monkeypatch):
    """`only_unscored` cannot join `only_mismatched` (a 422), and needs not: rows the
    first run re-scored now carry the new model's marker, so a re-run skips them."""
    calls = _install(monkeypatch)
    from backend.ml.model_manager import ModelEntry, model_manager

    # `_install` stubs the LAION loader only. V2.5 has its own, which imports a package
    # CI does not install, so without this the job fails there before scoring anything.
    async def _load_v2_5(*a, **kw):
        return ModelEntry(object(), object(), vram_mb=0)

    monkeypatch.setattr(model_manager, "load_aesthetic_v2_5", _load_v2_5)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _images(env, ds["id"], 3)
            # i0 and i1 still carry LAION scores; i2 was moved onto V2.5 before the stop.
            async with env.Session() as db:
                from backend.models import Image

                for name, model in (("i0.png", "laion"), ("i1.png", "laion"), ("i2.png", "v2_5")):
                    row = await db.get(Image, imgs[name])
                    row.aesthetic_score, row.aesthetic_model = 0.5, model
                await db.commit()
            job_id = await _job(env, config={
                "dataset_id": ds["id"], "run_aesthetic": True, "run_technical": False,
                "aesthetic_model": "v2_5", "only_mismatched": True,
            })
            r = await _retry(env, job_id)
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 2
            done = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert done["status"] == "completed", done
            assert done["config"]["only_mismatched"] is True
            assert done["config"]["only_unscored"] is False
            # The one already on the new model is skipped; the other two are moved.
            assert sorted(n for c in calls["aesthetic"] for n in c) == ["i0.png", "i1.png"]
            rows = await _rows(env, imgs.values())
            assert all(r.aesthetic_model == "v2_5" for r in rows.values())

    run(scenario())


def test_refusals_and_the_retryable_flag(tmp_path, monkeypatch):
    _install(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _images(env, ds["id"], 1)
            cfg = {"dataset_id": ds["id"], **_TECH_ONLY}

            completed = await _job(env, status="completed", config=cfg)
            running = await _job(env, status="running", config=cfg)
            caption = await _job(env, job_type="caption", config={"dataset_id": ds["id"]})
            run_owned = await _job(env, config={**cfg, "run_id": "r1", "run_stage": "score"})
            bad_config = await _job(env, config={**cfg, "aesthetic_model": "nope"})
            # A deleted dataset leaves the job's FK NULL (`SET NULL`) and its config as it
            # was: hidden from the list, and a 404 if posted anyway.
            gone_dataset = await _job(
                env, dataset_id=None, config={**cfg, "dataset_id": "no-such-dataset"}
            )
            # The race the list cannot see: the row still names a dataset, but its config
            # points at one deleted since. The handler's own check answers it.
            stale_config = await _job(
                env, dataset_id=ds["id"], config={**cfg, "dataset_id": "no-such-dataset"}
            )
            in_flight = await _job(env, config=cfg)
            # A retry of `in_flight` still queued: planted, never enqueued.
            await _job(env, status="pending", config={**cfg, "retry_of": in_flight})

            flags = await _retryable(env)
            for job_id in (completed, running, caption, run_owned, gone_dataset):
                assert flags[job_id] is False, job_id
            # Settings and the config's dataset are only checked on the POST, and so is a
            # queued retry.
            for job_id in (bad_config, stale_config, in_flight):
                assert flags[job_id] is True, job_id

            expected = {
                completed: 409, running: 409, caption: 409, run_owned: 409,
                bad_config: 409, gone_dataset: 404, stale_config: 404, in_flight: 409,
                "no-such-job": 404,
            }
            for job_id, status in expected.items():
                r = await _retry(env, job_id)
                assert r.status_code == status, (job_id, r.status_code, r.text)

            # No refusal queued anything.
            r = await env.client.get(f"{API}/jobs/", params={"limit": 500})
            assert not any(j["label"] and j["label"].startswith("Retry: ") for j in r.json())

    run(scenario())
