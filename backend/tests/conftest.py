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
import os
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

# Sentinel for "the variable was absent", which is not the same state as "" — clearing an
# override pops HF_TOKEN rather than blanking it, and
# test_settings_secrets.py::test_clearing_with_no_env_value_removes_the_variable asserts on
# exactly that distinction. `os.environ.get(var, "")` would erase it.
_ENV_UNSET = object()

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

# The same guard for torch, which — unlike cv2 — CI will never have: every job
# installs `backend/requirements-ci.txt`, whose header explains why a torch-sized
# wheel stays out of it. A test that imports anything under `backend/ml/` past the
# pure-numpy modules therefore *errors* on the runner rather than skipping, and
# `find_spec` is the only way to ask without triggering the import. Applied
# per-test, not per-module: the files that need it (`test_scorer_failure_contract.py`)
# also hold cases that only touch the ORM, and those must keep running in CI.
needs_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None, reason="torch is not installed"
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
async def api_env(tmp_path: Path, *, foreign_keys: bool = False):
    # Snapshotted *before* the import below, not beside the module globals further down:
    # `backend.main` calls `secrets_service.sync_env(None)` at import, so the very first
    # api_env in a session projects `settings.hf_token` — the developer's real .env value,
    # or whatever the test monkeypatched onto the singleton — into a variable that may
    # have been absent. That write happens on the import line, and a snapshot taken after
    # it would record the leak as the value to restore.
    prev_hf_token = os.environ.get("HF_TOKEN", _ENV_UNSET)

    from backend.main import app

    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    if foreign_keys:
        # SQLite defaults `foreign_keys` OFF *per connection*, and this harness
        # builds its schema with `create_all` on its own engine, so it never gets
        # the `PRAGMA foreign_keys=ON` that backend/database.py installs on the
        # app engine. Every FK in the schema is therefore unenforced here by
        # default — a blind spot that has hidden at least one real IntegrityError
        # (see test_duplicate_video_fk_enforced.py). Opt in per test rather than
        # globally: turning it on for the whole suite is a behaviour change to
        # every existing scenario, which belongs in its own piece of work.
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

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
        # KNOWN HAZARD — a scenario that enqueues a job and returns without
        # awaiting it can hang here, forever, in `stop()`. Reproduced 4 runs in 5
        # with: enqueue a duplicate, issue one more successful request, return.
        # `stop()` cancels the worker task and awaits it; the task goes to
        # "cancelling" and stays stuck on the pending future inside its
        # `await self._queue.get()`, so the await never returns and the loop
        # sits idle in `select()`. It is a race, not a rule — enqueue-and-return
        # with no second request, enqueue-then-sleep-to-completion, and
        # enqueue-then-tear-down-mid-job all pass repeatedly, so do not read the
        # repro as the boundary. Root cause not identified.
        #
        # The fix in a test is simply to `wait_for_job(...)` before returning,
        # which every scenario should be doing anyway. Left unfixed here because
        # the cure belongs in `JobQueue.stop()` (a `wait_for` around the await,
        # or draining before cancelling) and that is a change to production
        # shutdown — `main.py`'s lifespan awaits this same unguarded `stop()`.
        # Whether production can hit it is **unverified**: this harness rebuilds
        # `job_queue._queue` and re-`start()`s the singleton once per test, which
        # the app never does, so the repro does not transfer on its own.
        await job_queue.stop()
        job_queue_mod.AsyncSessionLocal = prev_worker_session_local
        app.dependency_overrides.pop(get_db, None)
        settings.datasets_dir = prev_datasets_dir
        database.AsyncSessionLocal = prev_session_local
        # Runs before `monkeypatch.undo()`: `api_env` exits inside the test body while
        # monkeypatch unwinds at teardown, so where a test also recorded HF_TOKEN with
        # monkeypatch, that recorded value is what this restore just wrote back.
        if prev_hf_token is _ENV_UNSET:
            os.environ.pop("HF_TOKEN", None)
        else:
            os.environ["HF_TOKEN"] = prev_hf_token
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


def bmp_bytes(colour=(200, 90, 40), size=(32, 24)) -> bytes:
    """A real BMP — the fixture for the PNG-fallback paths.

    `.bmp` is in `media_types.IMAGE_EXTENSIONS` and is one of the four suffixes
    `utils.normalize_image_format` refuses to write back, so every save path
    reached with one of these writes a `.png` beside it instead (PM-009). Prefer
    it to `.avif`, also in the set: AVIF is a build-time Pillow feature, so a
    fixture in that format may not be readable in CI.
    """
    from PIL import Image as PilImage

    buf = io.BytesIO()
    PilImage.new("RGB", size, colour).save(buf, "BMP")
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


def mp4_telecine_bytes(
    frames: int = 60,
    size=(320, 240),
    fps: float = 29.97,
    shift: int = 7,
) -> bytes:
    """A real .mp4 carrying a 3:2 pulldown pattern — the telecine fixture.

    `probe_samples`' telecine pass is gated on ~29.97/30 fps, so **every other
    `mp4_*` helper here (all 25.0 fps) leaves that branch unreachable** — not
    under-asserted, never executed. That gate is the whole reason this fixture
    exists; `fps` must stay inside `abs(fps - 29.97) < 0.2` or the pass it is
    written for silently does not run and the test still passes.

    Three constraints, each of which quietly breaks detection if changed:

    - **The pan must be whole-frame.** `combing_ratio` averages row differences
      over the *entire* frame, so a small moving object dilutes to nothing: a
      50x60 block on this fixture's grating peaks at 0.65, under the 0.9
      threshold. A full-frame pan of a diagonal grating reads 2.77 combed
      against 0.51 clean — wide margins on both sides, so encoder noise cannot
      flip a frame.
    - **The grating must be diagonal.** `combing_ratio` returns 0.0 when
      same-parity rows are identical (`d2 < COMBING_D2_FLOOR`), which a purely
      horizontal pan of purely horizontal stripes produces. The diagonal gives
      both the vertical detail `d2` needs and the horizontal structure a pan can
      move.
    - **>= `PROBE_TELECINE_RUN` frames after the midpoint.** The pass seeks to
      the middle sample and reads 20 *consecutive* frames from there; 60 leaves
      room. `telecine_from_series` needs `TELECINE_MIN_SAMPLES` (13) of them.

    The 5-frame cadence is 3 clean : 2 combed, which is what 24 fps film becomes
    at 30: duty 0.40, mid-range of `TELECINE_DUTY_RANGE`. Phase alignment does
    not matter — the decision is a lag-5 autocorrelation.
    """
    import tempfile

    import cv2
    import numpy as np

    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]

    def source(t: int) -> np.ndarray:
        """One progressive 24 fps frame: the grating panned by `t * shift`."""
        v = (np.sin((xx + yy + t * shift) / 3.0) * 60 + 128).astype(np.uint8)
        return np.dstack([v] * 3)

    def weave(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Even rows from one instant, odd rows from the next — a combed frame."""
        out = a.copy()
        out[1::2] = b[1::2]
        return out

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "telecine.mp4"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
        assert writer.isOpened(), "cv2 could not open an mp4v VideoWriter"
        for i in range(frames):
            src = i * 4 // 5  # 24 -> 30
            writer.write(
                weave(source(src), source(src + 1)) if i % 5 in (3, 4) else source(src)
            )
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


async def upload_image(
    env, dataset_id: str, name: str = "a.png", data: bytes | None = None, subfolder: str = ""
) -> dict:
    """Upload one image and return its row. The endpoint returns filenames only."""
    r = await env.client.post(
        f"{API}/images/upload",
        params={"dataset_id": dataset_id, "subfolder": subfolder},
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

    **Always call this before a scenario that enqueued a job returns.** Leaving a
    job in flight can hang the whole pytest process at teardown, in
    `api_env`'s `await job_queue.stop()` — see the note there. There is no pytest
    timeout plugin installed, so that hang has no upper bound.
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
