"""The 409 branches: name collisions in the file browser, and one-job-per-plan.

Both families are pure guard code — a few lines that only ever run when a user
does the wrong thing twice — and both fail in the same expensive direction if
they regress. A missing file-browser collision check overwrites a file the DB
still points at; a missing comfy guard lets two runs (or two LLM prompt-
generation jobs) work the same plan, double-billing the provider and racing on
row status.

The file-browser cases therefore assert the *filesystem is untouched* after the
409, not just the status code: returning 409 while having already clobbered the
destination is the failure that matters.

The comfy cases each pair the 409 with a discriminator — the same request with
the pending job tagged to a **different** plan, or to the **other** job_type —
which must fall through the guard to the next validation error (400). Asserting
only the 409 would pass equally well for a guard that fires unconditionally, and
the discriminator can be checked without enqueueing anything real: both
endpoints have a cheap 400 immediately after the guard.
"""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.models import BackgroundJob, Video
from backend.tests.conftest import API, api_env, run, upload_video

# Only the video-move test below encodes a container; the rest of the module is
# media-free, so the guard is per-test rather than a module-level importorskip.
needs_cv2 = pytest.mark.skipif(
    importlib.util.find_spec("cv2") is None, reason="opencv is not installed"
)

FS = f"{API}/filesystem"
PROMPT_ALIAS = "prompt"
WORKFLOW = {
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "template prompt"}},
    "9": {"class_type": "SaveImage", "inputs": {}},
}


# ── File browser ─────────────────────────────────────────────────────────────

def test_move_onto_an_existing_name_is_a_409(tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()
    src = src_dir / "a.txt"
    src.write_text("source")
    (dst_dir / "a.txt").write_text("destination")

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(
                f"{FS}/move", json={"src": str(src), "dst_dir": str(dst_dir)}
            )
            assert r.status_code == 409, r.text
            assert "already exists" in r.json()["detail"]

    run(scenario())
    # Neither side moved: the guard runs before shutil.move.
    assert src.read_text() == "source"
    assert (dst_dir / "a.txt").read_text() == "destination"


def test_rename_onto_an_existing_name_is_a_409(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("A")
    b.write_text("B")

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{FS}/rename", json={"path": str(a), "new_name": "b.txt"})
            assert r.status_code == 409, r.text
            assert "already exists" in r.json()["detail"]

            # A path separator in new_name is a different failure (400) and must
            # not be conflated with the collision case.
            r = await env.client.post(
                f"{FS}/rename", json={"path": str(a), "new_name": "sub/b.txt"}
            )
            assert r.status_code == 400, r.text

    run(scenario())
    assert a.read_text() == "A"
    assert b.read_text() == "B"


def test_mkdir_over_an_existing_entry_is_a_409(tmp_path):
    existing_dir = tmp_path / "already"
    existing_dir.mkdir()
    (existing_dir / "keep.txt").write_text("keep")
    # A *file* of that name collides too — `new_dir.exists()` is not is_dir().
    (tmp_path / "afile").write_text("f")

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{FS}/mkdir", json={"parent": str(tmp_path), "name": "already"})
            assert r.status_code == 409, r.text

            r = await env.client.post(f"{FS}/mkdir", json={"parent": str(tmp_path), "name": "afile"})
            assert r.status_code == 409, r.text

            r = await env.client.post(f"{FS}/mkdir", json={"parent": str(tmp_path), "name": "fresh"})
            assert r.status_code == 200, r.text

    run(scenario())
    assert (existing_dir / "keep.txt").read_text() == "keep"
    assert (tmp_path / "fresh").is_dir()


@needs_cv2
def test_moving_a_video_between_datasets_rewrites_its_row(tmp_path):
    """The file browser is the one place a video's file can move without going
    through `/videos`, and its DB sync treats `Video` exactly like `Image` — a
    moved file whose row still points at the old path is a dangling record
    either way.

    **Recorded, not fixed:** the sync rewrites `file_path`, `filename` and
    `dataset_id`, and *not* `poster_path`, so after a cross-dataset move the row
    still points into the source dataset's `videos/thumbnails/`. Pinned here as
    current behaviour, the way `test_video_rename_http.py` records its own known
    divergence; changing it is a code change and belongs in its own commit.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            video = await upload_video(env, a["id"], "clip.mp4")
            # B needs a video of its own: `videos/` is created lazily on first
            # ingest, and `/filesystem/move` refuses a destination that is not a
            # directory rather than creating one.
            await upload_video(env, b["id"], "other.mp4")

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                src, poster_before = row.file_path, row.poster_path
            dst_dir = Path(b["folder_path"]) / "videos"
            assert dst_dir.is_dir()

            r = await env.client.post(
                f"{FS}/move", json={"src": src, "dst_dir": str(dst_dir)}
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                row = (await db.execute(
                    select(Video).where(Video.id == video["id"])
                )).scalar_one()

            assert row.file_path == str(dst_dir / "clip.mp4")
            assert row.filename == "clip.mp4"
            assert row.dataset_id == b["id"]
            assert Path(row.file_path).exists()
            # The gap: the poster stayed behind, in dataset A.
            assert row.poster_path == poster_before
            assert str(Path(a["folder_path"])) in row.poster_path

    run(scenario())


@needs_cv2
def test_moving_a_folder_rewrites_the_paths_of_the_media_inside_it(tmp_path):
    """The directory half of the same sync, which iterates `(Image, Video)` and
    rewrites `file_path` by prefix. It shares the file half's one hazard: both
    branches are chosen from `src`, which no longer exists once `shutil.move` has
    run, so a classification made too late turns the whole block into dead code
    and leaves every row pointing at nothing."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("a")
            video = await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                videos_dir = Path(row.file_path).parent

            archive = tmp_path / "archive"
            archive.mkdir()
            r = await env.client.post(
                f"{FS}/move", json={"src": str(videos_dir), "dst_dir": str(archive)}
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                row = await db.get(Video, video["id"])

            assert row.file_path == str(archive / "videos" / "clip.mp4")
            assert Path(row.file_path).exists()
            # Unlike the file branch, this one rewrites paths only — the folder
            # is not necessarily a dataset's, so there is no dataset to re-home to.
            assert row.dataset_id == ds["id"]

    run(scenario())


# ── One comfy job per plan ────────────────────────────────────────────────────

async def _seed_pending_job(env, job_type: str, plan_id: str) -> None:
    """A queued job as the guards see it: status pending, plan_id in config."""
    async with env.Session() as db:
        db.add(BackgroundJob(job_type=job_type, status="pending", config={"plan_id": plan_id}))
        await db.commit()


async def _make_plan(env, name: str) -> str:
    ds = await env.create_dataset(name + "-ds")
    r = await env.client.post(f"{API}/comfy/plans", json={
        "dataset_id": ds["id"],
        "name": name,
        "workflow_json": WORKFLOW,
        "pinned_params": [
            {"node_id": "6", "input": "text", "alias": PROMPT_ALIAS, "is_prompt": True},
        ],
        "output_node_ids": ["9"],
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_second_prompt_generation_job_for_a_plan_is_a_409(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            plan_id = await _make_plan(env, "prompts-plan")
            other_plan_id = await _make_plan(env, "other-plan")

            # One row already holds a prompt, so the post-guard 400 ("already
            # holds N prompts") is reachable as the discriminator below.
            r = await env.client.post(
                f"{API}/comfy/plans/{plan_id}/rows", json={"values": {PROMPT_ALIAS: "a cat"}}
            )
            assert r.status_code == 200, r.text

            r = await env.client.post(f"{API}/providers/", json={
                "name": "fake-llm", "base_url": "http://127.0.0.1:9/v1", "default_model": "m",
            })
            assert r.status_code == 201, r.text
            provider_id = r.json()["id"]

            body = {"provider_id": provider_id, "instruction": "more cats", "target_count": 1}
            url = f"{API}/comfy/plans/{plan_id}/generate-prompts"

            await _seed_pending_job(env, "comfy_prompts", plan_id)
            r = await env.client.post(url, json=body)
            assert r.status_code == 409, r.text
            assert "already queued or running" in r.json()["detail"]

            # Discriminator: a third plan whose only pending jobs are (a) a
            # prompt job tagged to a *different* plan and (b) a *run* job of the
            # other job_type. Neither may block it — the request must fall
            # through the guard to the target-count 400, enqueueing nothing.
            plan3_id = await _make_plan(env, "runbusy-plan")
            r = await env.client.post(
                f"{API}/comfy/plans/{plan3_id}/rows", json={"values": {PROMPT_ALIAS: "a dog"}}
            )
            assert r.status_code == 200, r.text
            await _seed_pending_job(env, "comfy_prompts", other_plan_id)
            await _seed_pending_job(env, "comfy_generate", plan3_id)
            r = await env.client.post(
                f"{API}/comfy/plans/{plan3_id}/generate-prompts",
                json={"provider_id": provider_id, "instruction": "x", "target_count": 1},
            )
            assert r.status_code == 400, r.text
            assert "already holds 1 prompt" in r.json()["detail"]

    run(scenario())


def test_second_run_for_a_plan_is_a_409(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(
                f"{API}/settings/thresholds", json={"comfyui_url": "http://127.0.0.1:9"}
            )
            assert r.status_code == 200, r.text

            plan_id = await _make_plan(env, "run-plan")
            await _seed_pending_job(env, "comfy_generate", plan_id)

            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
            assert r.status_code == 409, r.text
            assert "already queued or running" in r.json()["detail"]

            # The plan has no rows, so once the guard correctly declines to fire
            # the request stops at "No rows to run" — nothing is enqueued.
            other_plan_id = await _make_plan(env, "run-plan-2")
            await _seed_pending_job(env, "comfy_prompts", other_plan_id)
            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": other_plan_id})
            assert r.status_code == 400, r.text
            assert "No rows to run" in r.json()["detail"]

    run(scenario())
