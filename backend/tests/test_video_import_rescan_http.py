"""Folder import and rescan for videos.

`include_videos` defaults to False on purpose: a video is orders of magnitude
larger than the images beside it, so a mixed folder imported into an image
dataset must not silently pull gigabytes in. The disk preflight has to agree
with that default — counting bytes that will not be copied would fail an import
that fits, and *not* counting bytes that will be copied is worse.

Rescan needs its own pass at all because the image walk is
`images_dir.rglob("*")`, which cannot see videos/.
"""

from pathlib import Path

from sqlalchemy import select

from backend.models import Image, Video
from backend.services.dataset_service import _scan_source_files
from backend.tests.conftest import API, api_env, mp4_bytes, png_bytes, run, upload_video, wait_for_job


def _seed_source(src: Path) -> None:
    src.mkdir(parents=True, exist_ok=True)
    (src / "a.png").write_bytes(png_bytes())
    (src / "b.png").write_bytes(png_bytes(color=(3, 4, 5)))
    (src / "clip.mp4").write_bytes(mp4_bytes())


def test_scan_excludes_video_bytes_when_videos_are_excluded(tmp_path):
    """The size the preflight sees must match the files actually copied."""
    src = tmp_path / "src"
    _seed_source(src)

    images, videos, size_without = _scan_source_files(src, False, include_videos=False)
    assert sorted(p.name for p in images) == ["a.png", "b.png"]
    assert videos == []
    assert size_without == sum(p.stat().st_size for p in images)

    images2, videos2, size_with = _scan_source_files(src, False, include_videos=True)
    assert [p.name for p in videos2] == ["clip.mp4"]
    assert size_with == size_without + (src / "clip.mp4").stat().st_size
    assert images2 == images


def test_import_skips_videos_by_default(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            src = tmp_path / "src"
            _seed_source(src)

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/import", json={"folder_path": str(src)}
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            assert job["result_data"]["added"] == 2
            assert job["result_data"]["videos_added"] == 0
            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalars().all() == []
            assert not (Path(ds["folder_path"]) / "videos").exists()

    run(scenario())


def test_import_with_include_videos_lands_them_flat(tmp_path):
    """Videos ignore subfolder and preserve_structure entirely — a subfolder is
    an image-side concept, and a video's frames get their own at extraction."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            src = tmp_path / "src"
            _seed_source(src)
            (src / "nested").mkdir()
            (src / "nested" / "deep.mp4").write_bytes(mp4_bytes())

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/import",
                json={
                    "folder_path": str(src),
                    "preserve_structure": True,
                    "include_videos": True,
                },
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["videos_added"] == 2

            async with env.Session() as db:
                videos = (await db.execute(select(Video).order_by(Video.filename))).scalars().all()

            assert [v.filename for v in videos] == ["clip.mp4", "deep.mp4"]
            # Flat: both directly inside videos/, despite one source being nested.
            for v in videos:
                assert Path(v.file_path).parent == Path(ds["folder_path"]) / "videos"

    run(scenario())


def test_import_records_video_metadata_and_stats(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            src = tmp_path / "src"
            _seed_source(src)

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/import",
                json={"folder_path": str(src), "include_videos": True},
            )
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            detail = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            assert detail["image_count"] == 2
            assert detail["video_count"] == 1
            assert detail["video_size_bytes"] > 0

            videos = (await env.client.get(f"{API}/videos/", params={"dataset_id": ds["id"]})).json()
            assert len(videos) == 1
            assert videos[0]["width"] == 64 and videos[0]["height"] == 48
            assert videos[0]["duration_ms"] == 1000
            assert videos[0]["codec_label"]

    run(scenario())


def test_an_undecodable_source_video_leaves_no_orphan_file(tmp_path):
    """The copy has to happen before the probe, so a rejected file would sit in
    videos/ with no row — and rescan would try to register it on every run."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            src = tmp_path / "src"
            src.mkdir()
            (src / "broken.mp4").write_bytes(b"not a video" * 64)

            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/import",
                json={"folder_path": str(src), "include_videos": True},
            )
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["videos_added"] == 0
            assert job["result_data"]["failed_count"] == 1

            assert list((Path(ds["folder_path"]) / "videos").glob("*.mp4")) == []
            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalars().all() == []

    run(scenario())


def test_rescan_registers_a_video_dropped_into_videos(tmp_path):
    """The image walk is images_dir.rglob("*"), so videos/ is invisible to it —
    without the second pass a hand-copied video stays permanently unknown."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            root = Path(ds["folder_path"])
            (root / "images").mkdir(parents=True, exist_ok=True)
            (root / "images" / "a.png").write_bytes(png_bytes())
            (root / "videos").mkdir(parents=True, exist_ok=True)
            (root / "videos" / "dropped.mp4").write_bytes(mp4_bytes())
            # An undecodable file in videos/ is skipped, not reported as failed.
            (root / "videos" / "junk.mp4").write_bytes(b"junk" * 32)

            r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            assert job["result_data"]["added"] == 1              # the png
            assert job["result_data"]["videos_added"] == 1       # dropped.mp4 only
            assert job["result_data"]["videos_missing"] == []

            async with env.Session() as db:
                video = (await db.execute(select(Video))).scalar_one()
                assert (await db.execute(select(Image))).scalars().one()
            assert video.filename == "dropped.mp4"
            assert video.duration_ms == 1000

    run(scenario())


def test_rescan_reports_a_video_whose_file_vanished(tmp_path):
    """Reported under its own key, never removed — the same contract images get,
    and not folded into `missing`, whose entries are {subfolder, filename}."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            root = Path(ds["folder_path"])
            (root / "images").mkdir(parents=True, exist_ok=True)
            (root / "videos").mkdir(parents=True, exist_ok=True)
            (root / "videos" / "gone.mp4").write_bytes(mp4_bytes())

            r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            await wait_for_job(env, r.json()["job_id"])

            (root / "videos" / "gone.mp4").unlink()

            r2 = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            job = await wait_for_job(env, r2.json()["job_id"])
            assert job["result_data"]["videos_missing"] == ["gone.mp4"]

            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalar_one().filename == "gone.mp4"

    run(scenario())


def test_rescan_reconciles_videos_even_with_no_images_dir(tmp_path):
    """videos/ can exist without images/ — the image path early-returns there."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            root = Path(ds["folder_path"])
            import shutil
            shutil.rmtree(root / "images", ignore_errors=True)
            (root / "videos").mkdir(parents=True, exist_ok=True)
            (root / "videos" / "solo.mp4").write_bytes(mp4_bytes())

            r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["videos_added"] == 1

            detail = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            assert detail["video_count"] == 1

    run(scenario())


def test_rescan_ignores_the_poster_directory(tmp_path):
    """videos/thumbnails/ is a child of videos/; a recursive walk would try to
    register poster files as videos."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            root = Path(ds["folder_path"])
            (root / "videos" / "thumbnails").mkdir(parents=True, exist_ok=True)
            (root / "videos" / "real.mp4").write_bytes(mp4_bytes())
            (root / "videos" / "thumbnails" / "decoy.mp4").write_bytes(mp4_bytes())

            r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["result_data"]["videos_added"] == 1

            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalar_one().filename == "real.mp4"

    run(scenario())


def test_two_containers_sharing_a_stem_get_separate_posters(tmp_path):
    """`clip.mp4` and `clip.mkv` are two legitimate videos whose posters would
    both be `thumbnails/clip.webp`. Rescan cannot rename what the user dropped
    in, so it moves the *poster* instead — the second gets `clip_001.webp`.
    Without this the second poster written overwrote the first and both rows
    pointed at one file, so one strip card showed the other video's frame and
    deleting either unlinked the survivor's poster."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            vdir = Path(ds["folder_path"]) / "videos"
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / "clip.mp4").write_bytes(mp4_bytes(frames=10))
            (vdir / "clip.mkv").write_bytes(mp4_bytes(frames=10))

            r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["result_data"]["videos_added"] == 2

            async with env.Session() as db:
                rows = (await db.execute(select(Video))).scalars().all()

            # Both files keep the names the user gave them — only the poster moved.
            assert {row.filename for row in rows} == {"clip.mp4", "clip.mkv"}
            posters = {row.poster_path for row in rows}
            assert len(posters) == 2, f"posters collided: {posters}"
            assert all(Path(p).exists() for p in posters)
            assert {Path(p).stem for p in posters} == {"clip", "clip_001"}

    run(scenario())


def test_a_rescan_with_no_collision_renames_nothing(tmp_path):
    """The guard above must not fire on ordinary input: the file being
    registered is already sitting in videos/, so a naive uniqueness check would
    see its own name occupied and step every poster to `_001`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            vdir = Path(ds["folder_path"]) / "videos"
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / "solo.mp4").write_bytes(mp4_bytes(frames=10))

            r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            await wait_for_job(env, r.json()["job_id"])

            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
            assert row.filename == "solo.mp4"
            assert row.poster_path.endswith("/videos/thumbnails/solo.webp")

    run(scenario())


def test_an_upload_will_not_take_a_poster_stem_rescan_disambiguated(tmp_path):
    """The half of the claimed set a filename glob cannot supply. After the
    rescan above, a row holds filename `clip.mkv` with poster `clip_001.webp` —
    the two stems have diverged. An upload named `clip_001` must still not take
    that poster, which it would if the occupied set were seeded from filename
    stems alone."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            vdir = Path(ds["folder_path"]) / "videos"
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / "clip.mp4").write_bytes(mp4_bytes(frames=10))
            (vdir / "clip.mkv").write_bytes(mp4_bytes(frames=10))
            r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            await wait_for_job(env, r.json()["job_id"])

            await upload_video(env, ds["id"], "clip_001.mp4")

            async with env.Session() as db:
                rows = (await db.execute(select(Video))).scalars().all()
            posters = {row.poster_path for row in rows}
            assert len(posters) == len(rows) == 3, f"posters collided: {posters}"
            assert all(Path(p).exists() for p in posters)

    run(scenario())
