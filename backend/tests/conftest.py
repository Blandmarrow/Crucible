"""HTTP-level test infrastructure.

Every other test module in this package exercises services and helpers directly.
That is why a hard crash in a router — `_register_file_sync` growing a third
return value while a caller still unpacked two — shipped with a green suite: no
test had ever driven a request through the real app.

This module closes that. `api_env()` drives `backend.main.app` over
`httpx.ASGITransport` against a throwaway SQLite file, with `settings.datasets_dir`
pointed at the same temp directory, so a test can call the endpoints a user calls.

Deliberately no `pytest_asyncio`: it is not in the venv, and the existing tests use
a hand-rolled `run(scenario())` (`asyncio.run`) helper. `api_env` is an async
context manager used inside such a scenario, so no plugin and no new dependency:

    def test_something(tmp_path):
        async def scenario():
            async with api_env(tmp_path) as env:
                r = await env.client.post("/api/v1/datasets/", json={"name": "d"})
                assert r.status_code == 201
        run(scenario())

The live `dataset_manager.db` at the repo root is never touched: the app's own
engine is never connected to (nothing calls `init_db`), `get_db` is overridden via
FastAPI dependency overrides, and `backend.database.AsyncSessionLocal` — which the
background job runners import at call time — is swapped for the temp one and
restored afterwards.
"""
import asyncio
import importlib.util
import io
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.database as database
import backend.models  # noqa: F401 — register every model on Base
from backend.config import settings
from backend.database import Base, get_db

API = "/api/v1"

# The per-test cv2 guard, defined once. Modules that need a decodable video for
# only *some* of their tests import this rather than declaring an identical
# `skipif` of their own — three had it copied before it lived here, which is the
# same drift `backend/media_types.py`'s header describes for extension sets. Use
# a module-level `pytest.importorskip("cv2")` only where every test in the file
# needs a container; per-test keeps the media-free cases running on a machine
# without opencv, including the structural mirror guards CLAUDE.md relies on.
needs_cv2 = pytest.mark.skipif(
    importlib.util.find_spec("cv2") is None, reason="opencv is not installed"
)


def run(coro):
    """Run one async scenario. Mirrors the helper the other test modules use."""
    return asyncio.run(coro)


@dataclass
class ApiEnv:
    """Everything a request-level test needs."""
    client: httpx.AsyncClient
    Session: async_sessionmaker[AsyncSession]
    datasets_dir: Path

    async def create_dataset(self, name: str = "ds", **provenance) -> dict:
        """POST a dataset and return its JSON (the usual first line of a scenario)."""
        r = await self.client.post(f"{API}/datasets/", json={"name": name, **provenance})
        assert r.status_code == 201, r.text
        return r.json()


@asynccontextmanager
async def api_env(tmp_path: Path):
    from backend.main import app

    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db():
        async with Session() as session:
            yield session

    prev_datasets_dir = settings.datasets_dir
    prev_session_local = database.AsyncSessionLocal
    settings.datasets_dir = datasets_dir
    # Background job runners do `from backend.database import AsyncSessionLocal`
    # *inside* the coroutine, so patching the module attribute reaches them.
    database.AsyncSessionLocal = Session
    app.dependency_overrides[get_db] = _override_get_db

    # ASGITransport does not run the app's lifespan, and the lifespan would touch
    # the real database (init_db). Start just the piece a request-level test needs:
    # the background job worker, without which every enqueued job stays "pending".
    #
    # `job_queue` is a module-level singleton whose asyncio.Queue was created at
    # import time, and each scenario runs on a fresh `asyncio.run` loop — so the
    # queue has to be rebuilt here or `put()` raises "bound to a different event
    # loop". The worker resolves its session factory from its own module global, so
    # that is patched separately from `backend.database`.
    import backend.workers.job_queue as job_queue_mod
    from backend.workers.job_queue import job_queue

    prev_worker_session_local = job_queue_mod.AsyncSessionLocal
    job_queue_mod.AsyncSessionLocal = Session
    job_queue._queue = asyncio.Queue()
    job_queue._cancel_requested = set()
    job_queue._current_job_id = None
    await job_queue.start()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield ApiEnv(client=client, Session=Session, datasets_dir=datasets_dir)
    finally:
        await job_queue.stop()
        job_queue_mod.AsyncSessionLocal = prev_worker_session_local
        app.dependency_overrides.pop(get_db, None)
        settings.datasets_dir = prev_datasets_dir
        database.AsyncSessionLocal = prev_session_local
        await engine.dispose()


def png_bytes(color=(10, 120, 200), size=(16, 16), **text) -> bytes:
    """A real PNG, optionally carrying tEXt chunks (Author/Copyright/…).

    Shared by every request-level test that needs a file to upload; kept here so
    a new smoke test does not reach into another test module for it.
    """
    from PIL import Image as PilImage
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    for k, v in text.items():
        info.add_text(k, v)
    buf = io.BytesIO()
    PilImage.new("RGB", size, color).save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


def jpeg_bytes(color=(200, 60, 20), size=(16, 16)) -> bytes:
    """A real JPEG. Companion to `png_bytes` for tests that need two files whose
    stems can collide but whose extensions differ (thumbnail-stem clobber cases)."""
    from PIL import Image as PilImage

    buf = io.BytesIO()
    PilImage.new("RGB", size, color).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def mp4_bytes(frames: int = 25, size=(64, 48), fps: float = 25.0) -> bytes:
    """A real, decodable .mp4 — the video counterpart of `png_bytes`.

    The repo ships no sample media, so video fixtures are synthesized. cv2's
    VideoWriter cannot write to a buffer, hence the temp file. `mp4v` is the
    fourcc to use: `avc1` needs an h264 encoder that is not present in the
    opencv-python wheel, and its writer silently fails to open. 50 frames at
    64x48 come to about 3.4 KB.

    Each frame gets a different flat colour so shot/frame-picking code has
    something to distinguish, and the file stays trivially compressible.
    """
    import tempfile

    import cv2
    import numpy as np

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "fixture.mp4"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
        assert writer.isOpened(), "cv2 could not open an mp4v VideoWriter"
        for i in range(frames):
            writer.write(np.full((size[1], size[0], 3), (i * 5) % 255, np.uint8))
        writer.release()
        return path.read_bytes()


def mp4_shots_bytes(
    shots: int = 3,
    frames_per_shot: int = 30,
    size=(320, 240),
    fps: float = 25.0,
) -> bytes:
    """A real .mp4 with hard cuts between distinctly-coloured shots.

    Two departures from `mp4_bytes`, both forced:

    - **320x240, not 64x48.** `AdaptiveDetector` auto-sizes its edge kernel from
      the frame size, and at 64x48 that degenerates — it finds nothing at all on
      a file whose cuts are obvious to the eye.
    - **>= 24 frames per shot.** `min_scene_len` defaults to 15 frames and the
      extraction job raises it further; a shorter shot is merged into its
      neighbour and the fixture silently tests a different thing.

    Each shot is a flat saturated colour with a little texture, so
    `frame_colour()` can read a written frame back and say which shot it came
    from — the video equivalent of `test_video_poster.py::_grey`.
    """
    import tempfile

    import cv2
    import numpy as np

    palette = [
        (40, 40, 220), (40, 220, 40), (220, 40, 40),
        (220, 220, 40), (220, 40, 220), (40, 220, 220),
    ]
    w, h = size
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "shots.mp4"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
        assert writer.isOpened(), "cv2 could not open an mp4v VideoWriter"
        for s in range(shots):
            base = np.full((h, w, 3), palette[s % len(palette)], np.uint8)
            # A moving bright block: without any change inside a shot the encoder
            # emits near-identical frames and every sharpness score ties.
            for i in range(frames_per_shot):
                frame = base.copy()
                x = (i * 7) % max(w - 40, 1)
                frame[h // 3: h // 3 + 40, x: x + 40] = 255
                writer.write(frame)
        writer.release()
        return path.read_bytes()


def mp4_corrupt_bytes(keep_frac: float = 0.6, **kwargs) -> bytes:
    """A real mp4 with its tail cut off — a file cv2 cannot open at all.

    Named for what it *is*, not for what it was meant to be. The intent was a
    "posters fine, dies partway through extraction" fixture, and truncation does
    not produce one: `cv2.VideoWriter` puts the `moov` atom at the **end** of
    the file, so cutting the tail removes the index and `isOpened()` returns
    False. Verified — 0 frames grabbed.

    That makes it the right fixture for the *ingest gate* and for
    `detect_shots` refusing to fall back on a file that will not open, and the
    wrong one for the consecutive-failure circuit breaker. Test that by
    injecting failures into `render_shot`, which is deterministic; a fixture
    that happens to die at the right moment is not.
    """
    full = mp4_shots_bytes(**kwargs)
    return full[: int(len(full) * keep_frac)]


def frame_colour(path) -> tuple[int, int, int]:
    """Dominant RGB of an extracted frame, for asserting *which shot* it came
    from. Reads the median rather than the mean so the moving white block in
    `mp4_shots_bytes` does not shift the answer."""
    import numpy as np
    from PIL import Image as PilImage

    with PilImage.open(path) as img:
        arr = np.asarray(img.convert("RGB")).reshape(-1, 3)
    return tuple(int(v) for v in np.median(arr, axis=0))


async def upload_video(env, dataset_id: str, name: str = "a.mp4", data: bytes | None = None) -> dict:
    """Upload one video through the gallery upload endpoint and return its row."""
    r = await env.client.post(
        f"{API}/images/upload",
        params={"dataset_id": dataset_id},
        files=[("files", (name, data if data is not None else mp4_bytes(), "video/mp4"))],
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["videos"], f"video was not ingested: {body}"
    filename = body["videos"][0]
    listing = (await env.client.get(f"{API}/videos/", params={"dataset_id": dataset_id})).json()
    return next(v for v in listing if v["filename"] == filename)


async def upload_image(env, dataset_id: str, name: str = "a.png", data: bytes | None = None) -> dict:
    """Upload one image and return its row. The endpoint returns filenames only."""
    r = await env.client.post(
        f"{API}/images/upload",
        params={"dataset_id": dataset_id},
        files=[("files", (name, data or png_bytes(), "image/png"))],
    )
    assert r.status_code == 201, r.text
    filename = r.json()["files"][0]
    listing = (await env.client.get(f"{API}/images/", params={"dataset_id": dataset_id})).json()
    return next(i for i in listing if i["filename"] == filename)


async def wait_for_job(env: ApiEnv, job_id: str, timeout: float = 20.0) -> dict:
    """Poll a background job to a terminal state and return its row.

    Jobs are queued coroutines, so a test that returns straight after the 202 sees
    nothing; this is the request-level equivalent of awaiting the worker.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        r = await env.client.get(f"{API}/jobs/{job_id}")
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"job {job_id} still {job['status']} after {timeout}s")
        await asyncio.sleep(0.05)
