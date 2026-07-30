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
import os
from pathlib import Path

from sqlalchemy import select

from backend.models import BackgroundJob, Image, Video
from backend.tests.conftest import (
    API,
    api_env,
    needs_cv2,
    png_bytes,
    run,
    upload_image,
    upload_video,
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
def test_moving_a_video_between_datasets_is_refused(tmp_path):
    """The file browser is the one place a media file can move without going
    through `/videos` or `batch_move_dataset`, and it knows how to rewrite a path
    and nothing else. Re-homing means everything `batch_move_dataset` does —
    provenance materialization, stats refresh, the poster or thumbnail beside the
    file — so this endpoint refuses instead, with the filesystem untouched.
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
            assert r.status_code == 409, r.text
            assert "belongs to a dataset" in r.json()["detail"]

            async with env.Session() as db:
                row = (await db.execute(
                    select(Video).where(Video.id == video["id"])
                )).scalar_one()

            # Nothing moved and nothing was rewritten — not the row, not the file.
            assert row.file_path == src
            assert row.dataset_id == a["id"]
            assert row.poster_path == poster_before
            assert Path(src).exists()
            assert not (dst_dir / "clip.mp4").exists()

    run(scenario())


def test_moving_a_registered_image_out_of_its_dataset_is_refused(tmp_path):
    """The `Image` twin of the case above, plus the reason the guard is broader
    than "another dataset": a registered image moved *outside* the datasets tree
    is equally broken, because `utils.safe_dataset_path` then 403s every request
    for its bytes. Both destinations must be refused."""
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            image = await upload_image(env, a["id"], "pic.png")
            await upload_image(env, b["id"], "other.png")

            async with env.Session() as db:
                row = await db.get(Image, image["id"])
                src, thumb_before = row.file_path, row.thumbnail_path

            elsewhere = tmp_path / "outside"
            elsewhere.mkdir()
            for dst_dir in (Path(b["folder_path"]) / "images", elsewhere):
                assert dst_dir.is_dir()
                r = await env.client.post(
                    f"{FS}/move", json={"src": src, "dst_dir": str(dst_dir)}
                )
                assert r.status_code == 409, r.text
                assert not (dst_dir / "pic.png").exists()

            async with env.Session() as db:
                row = (await db.execute(
                    select(Image).where(Image.id == image["id"])
                )).scalar_one()

            assert row.file_path == src
            assert row.dataset_id == a["id"]
            assert row.thumbnail_path == thumb_before
            assert Path(src).exists()
            # The image still serves — the point of refusing in the first place.
            r2 = await env.client.get(f"{API}/images/{image['id']}/file")
            assert r2.status_code == 200, r2.text

    run(scenario())


def test_moving_a_loose_file_into_a_dataset_still_works(tmp_path):
    """The negative control. The guard keys off *having a row*, not off the
    destination, so a file the DB has never heard of keeps moving anywhere —
    including into a dataset folder, which is the file browser's actual job."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("a")
            await upload_image(env, ds["id"], "seed.png")

            loose = tmp_path / "loose.png"
            loose.write_bytes(png_bytes())
            dst_dir = Path(ds["folder_path"]) / "images"

            r = await env.client.post(
                f"{FS}/move", json={"src": str(loose), "dst_dir": str(dst_dir)}
            )
            assert r.status_code == 200, r.text
            assert (dst_dir / "loose.png").exists()
            assert not loose.exists()

    run(scenario())


@needs_cv2
def test_moving_a_registered_video_into_the_images_folder_is_refused(tmp_path):
    """Staying in the same dataset is not enough — a registered file has to stay
    directly in its dataset's canonical media folder. `_rescan_videos` globs
    `videos_dir.glob("*")` non-recursively, so a video parked anywhere else is
    reported under `videos_missing` forever while the file itself is fine."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("a")
            video = await upload_video(env, ds["id"], "clip.mp4")
            await upload_image(env, ds["id"], "seed.png")

            async with env.Session() as db:
                src = (await db.get(Video, video["id"])).file_path
            dst_dir = Path(ds["folder_path"]) / "images"

            r = await env.client.post(
                f"{FS}/move", json={"src": src, "dst_dir": str(dst_dir)}
            )
            assert r.status_code == 409, r.text
            assert "videos/ folder" in r.json()["detail"]
            assert Path(src).exists()
            assert not (dst_dir / "clip.mp4").exists()

    run(scenario())


def test_moving_a_registered_image_into_a_subfolder_of_images_is_refused(tmp_path):
    """The `parent.parent` hazard. Images are stored *flat* — `Image.subfolder`
    is a purely logical column — so a file in `images/sub/` makes
    `thumbnail_path_for` resolve to `images/thumbnails/`, at all eleven sites
    that re-derive a thumbnail path from a filename."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("a")
            image = await upload_image(env, ds["id"], "pic.png")

            async with env.Session() as db:
                row = await db.get(Image, image["id"])
                src, thumb_before = row.file_path, row.thumbnail_path
            sub = Path(src).parent / "sub"
            sub.mkdir()

            r = await env.client.post(f"{FS}/move", json={"src": src, "dst_dir": str(sub)})
            assert r.status_code == 409, r.text
            assert "images/ folder" in r.json()["detail"]

            async with env.Session() as db:
                row = await db.get(Image, image["id"])
            assert row.file_path == src and row.thumbnail_path == thumb_before
            assert Path(src).exists()
            assert not (sub / "pic.png").exists()

    run(scenario())


def test_a_loose_file_still_moves_anywhere_inside_a_dataset(tmp_path):
    """The canonical-root check is nested under `row is not None`, and that
    nesting is load-bearing: an unregistered file still moves wherever the user
    points it, including into a dataset subfolder the guard would refuse for a
    registered one."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("a")
            image = await upload_image(env, ds["id"], "seed.png")

            async with env.Session() as db:
                images_dir = Path((await db.get(Image, image["id"])).file_path).parent
            sub = images_dir / "sub"
            sub.mkdir()
            loose = tmp_path / "loose.png"
            loose.write_bytes(png_bytes())

            r = await env.client.post(f"{FS}/move", json={"src": str(loose), "dst_dir": str(sub)})
            assert r.status_code == 200, r.text
            assert (sub / "loose.png").exists()
            assert not loose.exists()

    run(scenario())


def test_moving_a_datasets_thumbnails_folder_is_refused(tmp_path):
    """V-76. `{ds}/thumbnails` holds no `file_path` at all, so the registered-media
    guard above sees nothing — every stored `thumbnail_path` in it would be
    stranded and the move would return 200.

    Since V-21 this is the only arm of the directory branch that still refuses
    anything on its own: a folder holding `file_path`s never reaches it. The
    distinct `detail` wording is what tells the two guards apart, so both this and
    the registered-media tests assert their own substring.

    The damage is not permanently broken tiles: `serve_thumbnail` regenerates a
    missing thumbnail and `generate_thumbnail` mkdirs its parent. It is a silent
    full-dataset derived-cache regeneration plus a folder of orphaned `.webp` —
    still not something a 200 should describe."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("a")
            image = await upload_image(env, ds["id"], "pic.png")

            async with env.Session() as db:
                thumb_before = (await db.get(Image, image["id"])).thumbnail_path
            thumbs_dir = Path(thumb_before).parent
            archive = Path(ds["folder_path"]) / "archive"
            archive.mkdir()

            r = await env.client.post(
                f"{FS}/move", json={"src": str(thumbs_dir), "dst_dir": str(archive)}
            )
            assert r.status_code == 409, r.text
            assert "strand" in r.json()["detail"]
            assert Path(thumb_before).exists()
            assert not (archive / "thumbnails").exists()

    run(scenario())


@needs_cv2
def test_moving_a_videos_thumbnails_folder_is_refused(tmp_path):
    """The twin. A poster's directory is `{ds}/videos/thumbnails/`, so moving it
    alone strands `Video.poster_path` the same way. Moving `{ds}/videos` itself is
    refused by the registered-media guard rather than this one — a different 409,
    which is why each asserts its own `detail` substring."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("a")
            video = await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                poster_before = (await db.get(Video, video["id"])).poster_path
            poster_dir = Path(poster_before).parent
            archive = Path(ds["folder_path"]) / "archive"
            archive.mkdir()

            r = await env.client.post(
                f"{FS}/move", json={"src": str(poster_dir), "dst_dir": str(archive)}
            )
            assert r.status_code == 409, r.text
            assert "strand" in r.json()["detail"]
            assert Path(poster_before).exists()
            assert not (archive / "thumbnails").exists()

    run(scenario())


def test_moving_a_folder_of_registered_media_out_of_its_dataset_is_refused(tmp_path):
    """The directory twin of the file refusal above. Moving `{ds}/images` is one
    request that re-homes every row in the dataset at once, and the branch
    rewrites paths and nothing else — so out of the datasets tree entirely, where
    `utils.safe_dataset_path` 403s every byte request, and into *another*
    dataset's folder, where `dataset_id` would be left stale, are both refused.

    These are the two destinations that were *always* refused. Since V-21 the
    guard no longer looks at the destination at all — holding one registered
    `file_path` is the whole predicate — so the same-dataset case is a 409 too,
    covered by `test_moving_a_canonical_media_folder_inside_its_own_dataset_is_
    refused`. Keeping both here is deliberate: they are the failures that motivated
    the guard, and a regression that re-permitted only cross-dataset moves would
    otherwise pass."""
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            image = await upload_image(env, a["id"], "pic.png")

            async with env.Session() as db:
                row = await db.get(Image, image["id"])
                images_dir = Path(row.file_path).parent
                src_before, thumb_before = row.file_path, row.thumbnail_path

            outside = tmp_path / "outside"
            outside.mkdir()
            # A sub-folder of B rather than B itself: dropping `images` straight
            # into `{b}` collides with B's own `images/` and would return the
            # name-collision 409, which proves nothing about the guard.
            other_dataset = Path(b["folder_path"]) / "archive"
            other_dataset.mkdir()

            for dst_dir in (outside, other_dataset):
                r = await env.client.post(
                    f"{FS}/move", json={"src": str(images_dir), "dst_dir": str(dst_dir)}
                )
                assert r.status_code == 409, r.text
                # The two branches word their detail differently; this is the
                # substring they share.
                assert "belong to a dataset" in r.json()["detail"]
                assert not (dst_dir / "images").exists()

            async with env.Session() as db:
                row = (await db.execute(
                    select(Image).where(Image.id == image["id"])
                )).scalar_one()

            assert row.file_path == src_before
            assert row.dataset_id == a["id"]
            assert row.thumbnail_path == thumb_before
            assert Path(src_before).exists()
            # Still serving — the point of refusing in the first place.
            r2 = await env.client.get(f"{API}/images/{image['id']}/file")
            assert r2.status_code == 200, r2.text

    run(scenario())


def test_moving_a_folder_with_no_registered_media_still_works(tmp_path):
    """The directory twin of the loose-file negative control: the guard keys off
    the folder *holding rows*, so an ordinary folder still moves anywhere —
    which is the file browser's actual job."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("a")
            await upload_image(env, ds["id"], "seed.png")

            loose_dir = tmp_path / "notes"
            loose_dir.mkdir()
            (loose_dir / "readme.txt").write_text("hello")
            # An unregistered image inside it, so "holds media files" and "holds
            # rows" are not accidentally the same thing here.
            (loose_dir / "stray.png").write_bytes(png_bytes())
            elsewhere = tmp_path / "elsewhere"
            elsewhere.mkdir()

            r = await env.client.post(
                f"{FS}/move", json={"src": str(loose_dir), "dst_dir": str(elsewhere)}
            )
            assert r.status_code == 200, r.text
            assert (elsewhere / "notes" / "readme.txt").read_text() == "hello"
            assert (elsewhere / "notes" / "stray.png").exists()
            assert not loose_dir.exists()

    run(scenario())


@needs_cv2
def test_moving_a_canonical_media_folder_inside_its_own_dataset_is_refused(tmp_path):
    """V-21. The destination that used to be the *permitted* one: same dataset, so
    nothing is re-homed and no row would be stranded. Both canonical folders, both
    409, because the app supports exactly one layout — a registered file directly
    in its dataset's `images/` or `videos/` — and this endpoint will not create a
    layout the rest of the app cannot read.

    Until V-21 both returned 200 and a prefix rewrite kept every row consistent
    with the new location. The rows were right and the app was still wrong:
    `rescan_dataset`/`_rescan_videos` glob the canonical folders non-recursively,
    so every moved row reports missing forever while its file is fine, and
    `thumbnail_path_for`'s `parent.parent` then resolves beside the *new* parent,
    orphaning the real thumbnail at the next rename. The file branch already
    refused this per row, and a folder move can only land its contents one level
    deeper than the canonical folder (`new_path.exists()` refuses the move back),
    so there was no destination the two branches could agree on — the permission
    and its rewrite went together.

    Nothing here restricts a user's own folders: `test_moving_a_folder_with_no_
    registered_media_still_works` is the negative control, and moving files into a
    hand-made layout *outside* the app remains unsupported rather than blocked."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("a")
            image = await upload_image(env, ds["id"], "pic.png")
            video = await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                img = await db.get(Image, image["id"])
                vid = await db.get(Video, video["id"])
                images_dir = Path(img.file_path).parent
                videos_dir = Path(vid.file_path).parent
                before = (img.file_path, img.thumbnail_path, vid.file_path, vid.poster_path)
            # The images thumbnails are *outside* `images/`, which is what used to
            # make this move look harmless: only `file_path` needed rewriting.
            assert not before[1].startswith(str(images_dir) + os.sep)

            archive = Path(ds["folder_path"]) / "archive"
            archive.mkdir()

            for media_dir in (images_dir, videos_dir):
                r = await env.client.post(
                    f"{FS}/move", json={"src": str(media_dir), "dst_dir": str(archive)}
                )
                assert r.status_code == 409, r.text
                assert "belong to a dataset" in r.json()["detail"]
                assert not (archive / media_dir.name).exists()
                assert media_dir.is_dir()

            async with env.Session() as db:
                img = await db.get(Image, image["id"])
                vid = await db.get(Video, video["id"])
                assert (img.file_path, img.thumbnail_path, vid.file_path, vid.poster_path) == before
            for p in before:
                assert Path(p).exists()

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
