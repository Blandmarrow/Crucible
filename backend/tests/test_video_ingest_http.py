"""Video ingest through the gallery upload endpoint, at HTTP level.

The load-bearing claim of the video arc is that videos are *sources*, not
images: a separate table, a separate folder, separate stats. Every test here
pins one edge of that separation. If a change makes a video land in `images` —
as an Image row, in images/, or inside image_count — one of these fails.
"""

from pathlib import Path

from sqlalchemy import select

from backend.models import Image, Video
from backend.tests.conftest import API, api_env, jpeg_bytes, mp4_bytes, run, upload_image, upload_video


def test_upload_creates_a_video_row_not_an_image_row(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")

            r = await env.client.post(
                f"{API}/images/upload",
                params={"dataset_id": ds["id"]},
                files=[("files", ("Episode 01.mp4", mp4_bytes(), "video/mp4"))],
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["added"] == 0 and body["files"] == []
            assert body["videos_added"] == 1
            assert body["skipped"] == []

            async with env.Session() as db:
                assert (await db.execute(select(Image))).scalars().all() == []
                video = (await db.execute(select(Video))).scalar_one()

            # Flat in videos/, slugified, never in images/.
            assert video.filename == "episode_01.mp4"
            assert video.file_path.endswith("/videos/episode_01.mp4")
            assert video.width == 64 and video.height == 48
            assert video.duration_ms == 1000  # 25 frames at 25 fps
            assert video.poster_path.endswith("/videos/thumbnails/episode_01.webp")
            assert Path(video.poster_path).exists()

    run(scenario())


def test_videos_dir_is_created_lazily(tmp_path):
    """An image-only dataset must not grow an empty videos/ directory."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png")
            assert not (tmp_path / "datasets" / ds["folder_path"].split("/")[-1] / "videos").exists()

            await upload_video(env, ds["id"], "a.mp4")
            async with env.Session() as db:
                video = (await db.execute(select(Video))).scalar_one()
            from pathlib import Path
            assert Path(video.file_path).parent.name == "videos"

    run(scenario())


def test_undecodable_upload_is_reported_and_leaves_nothing_behind(tmp_path):
    """Before this the loop silently `continue`d and the response counted only
    successes, so a rejected upload was indistinguishable from a stored one.
    The partially-written file must also be removed — an orphan in videos/ would
    be picked up as a new video by the next rescan, forever."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")

            r = await env.client.post(
                f"{API}/images/upload",
                params={"dataset_id": ds["id"]},
                files=[("files", ("broken.mp4", b"not a video at all" * 64, "video/mp4"))],
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["videos_added"] == 0
            assert len(body["skipped"]) == 1
            assert body["skipped"][0]["file"] == "broken.mp4"
            assert "broken.mp4" in body["skipped"][0]["reason"]

            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalars().all() == []

            from pathlib import Path
            videos_dir = Path(ds["folder_path"]) / "videos"
            assert not videos_dir.exists() or list(videos_dir.glob("*.mp4")) == []

    run(scenario())


def test_unsupported_suffix_is_reported_rather_than_dropped(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")

            r = await env.client.post(
                f"{API}/images/upload",
                params={"dataset_id": ds["id"]},
                files=[("files", ("notes.txt", b"hello", "text/plain"))],
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["added"] == 0 and body["videos_added"] == 0
            assert body["skipped"] == [
                {"file": "notes.txt", "reason": "Unsupported file type: .txt"}
            ]

    run(scenario())


def test_a_mixed_upload_routes_each_file_by_kind(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")

            r = await env.client.post(
                f"{API}/images/upload",
                params={"dataset_id": ds["id"]},
                files=[
                    ("files", ("a.png", jpeg_bytes(), "image/png")),
                    ("files", ("b.mp4", mp4_bytes(), "video/mp4")),
                    ("files", ("c.txt", b"x", "text/plain")),
                ],
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["added"] == 1
            assert body["videos_added"] == 1
            assert [s["file"] for s in body["skipped"]] == ["c.txt"]

    run(scenario())


def test_same_stem_different_container_do_not_share_a_poster_path(tmp_path):
    """Poster thumbnails are .webp keyed by stem, exactly like image thumbnails,
    so `a.mp4` and `a.mkv` would resolve to one poster file. The second upload
    must be renamed, or a later poster write clobbers the first video's."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_video(env, ds["id"], "a.mp4")
            await upload_video(env, ds["id"], "a.mkv")

            async with env.Session() as db:
                rows = (await db.execute(select(Video).order_by(Video.filename))).scalars().all()

            from pathlib import Path
            stems = {Path(v.filename).stem for v in rows}
            assert len(rows) == 2
            assert len(stems) == 2, f"poster stems would collide: {stems}"

    run(scenario())


def test_videos_are_counted_separately_from_images(tmp_path):
    """image_count is what a user compares against an export manifest, and a
    video is ~100x the size of the frames it yields — folding either in would
    make every dataset card read as bloated and the count wrong."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png")
            before = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()

            video = await upload_video(env, ds["id"], "clip.mp4")
            after = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()

            assert after["image_count"] == before["image_count"] == 1
            assert after["total_size_bytes"] == before["total_size_bytes"]
            assert after["video_count"] == 1
            assert after["video_size_bytes"] == video["file_size_bytes"] > 0

    run(scenario())


def test_the_dataset_list_reports_video_stats_too(tmp_path):
    """`GET /datasets/` hand-builds each DatasetOut field by field, and both
    video columns default to 0 on the schema — so an omission there reports
    every dataset as video-free instead of failing. The dataset card reads this
    endpoint, not the detail one, which is why it needs its own test."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            listing = (await env.client.get(f"{API}/datasets/")).json()
            row = next(d for d in listing if d["id"] == ds["id"])
            detail = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()

            assert row["video_count"] == detail["video_count"] == 1
            assert row["video_size_bytes"] == detail["video_size_bytes"] == video["file_size_bytes"]

    run(scenario())


def test_video_provenance_inherits_the_dataset_default(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset(
                "d", source_name="Archive", license="cc-by-4.0", attribution="A. Person"
            )
            video = await upload_video(env, ds["id"], "clip.mp4")

            detail = (await env.client.get(f"{API}/videos/{video['id']}")).json()
            # Raw values stay NULL — NULL means inherit, so editing the dataset
            # default retroactively updates the video.
            assert detail["source_name"] is None
            assert detail["license"] is None
            assert detail["provenance"]["source_name"] == "Archive"
            assert detail["provenance"]["license"] == "CC-BY-4.0"  # normalized on the dataset

    run(scenario())


def test_videos_dataset_fk_cascades_on_delete(tmp_path):
    """Deleting a dataset must take its videos with it.

    Asserted against the DDL rather than end to end: `delete_dataset` issues a
    plain `db.delete(ds)` and the videos relationship is `passive_deletes=True`,
    so the DB's ON DELETE CASCADE is what actually removes the rows — and this
    harness builds its schema with `create_all` on its own engine, which never
    gets the `PRAGMA foreign_keys=ON` that backend/database.py installs on the
    app engine. An end-to-end assertion here would pass or fail for reasons
    unrelated to the schema. What can regress is the ondelete clause itself.
    """
    from sqlalchemy import text

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                ddl = (await db.execute(
                    text("SELECT sql FROM sqlite_master WHERE name='videos'")
                )).scalar()

            assert "ON DELETE CASCADE" in ddl
            assert 'REFERENCES datasets' in ddl.replace('"', "")

    run(scenario())
