import asyncio
import os
import signal
import threading
import time
import warnings
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# Disable TorchDynamo so torch.compile is never attempted during inference
# (single-image inference gains nothing from it anyway). torch ships no triton on
# Windows, so compile would fail there outright; SAM3 users install triton-windows
# separately, which makes triton importable but is not a reason to enable compile.
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
# HuggingFace symlink warning: expected on Windows without Developer Mode — cache still works fine
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# torchao: no Triton on Windows — expected, no runtime effect
warnings.filterwarnings("ignore", message=".*Detected no triton.*")
# open_clip: ViT-L-14 weights trained with QuickGELU; no effect on inference output
warnings.filterwarnings("ignore", category=UserWarning, message=".*QuickGELU activation.*")
# sam3 imports the deprecated timm.models.layers path; third-party, nothing to fix here.
# NOTE: sam3's other import-time warning ("CUDA is not available ... Disabling
# autocast") is deliberately NOT filtered - it is the clearest signal that the venv
# has a CPU-only torch, which is otherwise only visible as unexplained slowness.
warnings.filterwarnings(
    "ignore", category=FutureWarning, message=".*timm.models.layers is deprecated.*"
)
# pydantic: HuggingFace internals use Field(repr=, frozen=) in a way Pydantic v2 flags; no runtime effect
try:
    from pydantic.warnings import UnsupportedFieldAttributeWarning
    warnings.filterwarnings("ignore", category=UnsupportedFieldAttributeWarning)
except ImportError:
    pass
# torch.distributed: Windows doesn't support process stream redirects; logged via logging not warnings
logging.getLogger("torch.distributed.elastic.multiprocessing.redirects").setLevel(logging.ERROR)

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import init_db
from backend.utils import FileInUseError

# Project the .env/OS-env HF_TOKEN into the process environment, where the ten HuggingFace-hub
# loaders — none of which pass token= — will find it. Stays here, between the config and router
# imports, so it applies in contexts that never run the lifespan (notably conftest.api_env).
# row=None means "DB not consulted"; the lifespan re-runs this against the DB below.
from backend.services.secrets_service import sync_env

sync_env(None)
from backend.routers import booru, captions, captioning, comfy, datasets, detection, export, filesystem, images, jobs, lut, models, providers, quality, settings as settings_router, system, tag_consolidation, upscaling, versioning, videos
from backend.workers.job_queue import job_queue, mark_interrupted_jobs, sweep_old_jobs


_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    await init_db()
    await _seed_secret_env()
    await mark_interrupted_jobs()
    await _sweep_old_jobs()
    await _sweep_orphan_dataset_folders()
    await job_queue.start()
    # Integrity check + rotating backup: seconds of disk I/O on a large database, and
    # nothing requested by the user waits on it. Fire and forget, after the app is
    # already serving. The reference keeps the task from being garbage-collected
    # mid-run (asyncio only holds a weak one).
    _background_tasks.add(asyncio.create_task(_startup_db_maintenance()))
    yield
    await job_queue.stop()


async def _seed_secret_env() -> None:
    """Re-project HF_TOKEN from the DB, so a token saved in Settings survives a restart.

    Must run after init_db() — the DB has to exist to be read — and is awaited rather than
    fired-and-forgotten, since a model load must not race it. One indexed single-row read.
    Never fatal: a failure here leaves the .env value that import time already projected.
    """
    from backend.database import AsyncSessionLocal
    from backend.services.secrets_service import sync_env_from_db

    try:
        async with AsyncSessionLocal() as session:
            await sync_env_from_db(session)
    except Exception:
        logging.getLogger(__name__).exception("Secret environment seeding failed")


async def _startup_db_maintenance() -> None:
    """Back up the database off the startup path. Never fatal to the app."""
    from backend.services.db_maintenance import run_startup_maintenance_sync

    try:
        await asyncio.get_running_loop().run_in_executor(None, run_startup_maintenance_sync)
    except Exception:
        logging.getLogger(__name__).exception("Startup database maintenance failed")
    finally:
        _background_tasks.discard(asyncio.current_task())


async def _sweep_old_jobs() -> None:
    """Apply the background_jobs retention policy. Never fatal to startup."""
    try:
        await sweep_old_jobs()
    except Exception:
        logging.getLogger(__name__).exception("Job retention sweep failed")


async def _sweep_orphan_dataset_folders() -> None:
    """Reconcile data/datasets/ against the DB at startup, removing folders with no row."""
    from backend.database import AsyncSessionLocal
    from backend.services.dataset_service import sweep_orphan_dataset_folders

    try:
        async with AsyncSessionLocal() as session:
            await sweep_orphan_dataset_folders(session)
    except Exception:
        logging.getLogger(__name__).exception("Orphan dataset-folder sweep failed")


app = FastAPI(
    title="Crucible",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(FileInUseError)
async def file_in_use_handler(request: Request, exc: FileInUseError):
    """A file the app could not delete or rename because something holds it open.

    App-level rather than per-route so every site that adopts
    `utils.unlink_retrying`/`rename_retrying` inherits the translation — the
    `filesystem.py` routes have the same exposure and are not converted yet.
    409 (not 500): the request was well-formed and retrying later can work.
    """
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    """422 without the rejected value echoed back.

    FastAPI's default handler returns each error entry verbatim, and pydantic puts the
    input it refused in `input` — so `PATCH /settings/secrets` with an over-long
    `hf_token`, or `POST /providers` with a bad `api_key`, answered with the submitted
    secret in the response body. Dropping `input` (and `ctx`, which carries the same
    value for some error types) is what makes "the plaintext appears in no response" a
    property of the app rather than of the happy path.

    App-level rather than per-route, on the same reasoning as the `FileInUseError`
    handler above: a secret can reach a validation error through any endpoint that grows
    one, and a per-route defence is a rule every future route has to remember.

    Nothing is lost. `msg` already restates what `ctx` holds — pydantic renders a
    `value_error` as `"Value error, <the ValueError's text>"` and a constraint failure as
    `"String should have at most 500 characters"` — and `loc` still names the field, which
    is all `frontend/src/utils/apiError.ts` reads. `jsonable_encoder` matches FastAPI's
    own default: `loc` arrives as a tuple, and a couple of entries are hand-built outside
    pydantic.

    Scope: rejected *values* only. A secret reaching a 500 traceback or a log line is a
    different exposure and this handler does not touch it.
    """
    detail = [
        {k: v for k, v in e.items() if k not in ("input", "ctx")}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(detail)})


PREFIX = "/api/v1"
app.include_router(datasets.router, prefix=PREFIX)
app.include_router(images.router, prefix=PREFIX)
app.include_router(videos.router, prefix=PREFIX)
app.include_router(captions.router, prefix=PREFIX)
app.include_router(captioning.router, prefix=PREFIX)
app.include_router(quality.router, prefix=PREFIX)
app.include_router(models.router, prefix=PREFIX)
app.include_router(booru.router, prefix=PREFIX)
app.include_router(export.router, prefix=PREFIX)
app.include_router(jobs.router, prefix=PREFIX)
app.include_router(system.router, prefix=PREFIX)
app.include_router(filesystem.router, prefix=PREFIX)
app.include_router(detection.router, prefix=PREFIX)
app.include_router(upscaling.router, prefix=PREFIX)
app.include_router(lut.router, prefix=PREFIX)
app.include_router(settings_router.router, prefix=PREFIX)
app.include_router(providers.router, prefix=PREFIX)
app.include_router(versioning.router, prefix=PREFIX)
app.include_router(tag_consolidation.router, prefix=PREFIX)
app.include_router(comfy.router, prefix=PREFIX)

_RESTART_SENTINEL = Path(__file__).parent.parent / ".restart"
_SHUTDOWN_SENTINEL = Path(__file__).parent.parent / ".shutdown"
_START_TIME = time.time()


@app.post("/api/v1/shutdown", status_code=204)
async def shutdown():
    _SHUTDOWN_SENTINEL.touch()
    threading.Thread(target=lambda: os.kill(os.getpid(), signal.SIGTERM), daemon=True).start()


@app.get("/api/v1/health", status_code=200)
async def health():
    return {"status": "ok", "start_time": _START_TIME}


@app.post("/api/v1/restart", status_code=204)
async def restart():
    def _do_restart():
        _SHUTDOWN_SENTINEL.unlink(missing_ok=True)
        _RESTART_SENTINEL.touch()
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=_do_restart, daemon=True).start()


# Serve built React frontend — must come last so API routes take priority
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    # Mount static assets (JS/CSS/images) at their exact paths
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    # The built SPA has exactly one class of cacheable file: the content-hashed
    # /assets/* bundles (served by the StaticFiles mount above), whose filenames
    # change whenever their contents do. Everything served below by literal path
    # keeps a *stable* URL across rebuilds — index.html plus the unhashed root
    # files it references (favicon.svg, favicon-32.png, apple-touch-icon.png, …).
    # Those must be revalidated, or a logo/markup change only appears after a
    # manual browser cache clear. no-cache makes the browser revalidate (a cheap
    # 304 when unchanged); it does not disable caching.
    _NO_CACHE = {"Cache-Control": "no-cache"}

    _INDEX_HTML = frontend_dist / "index.html"
    _DIST_ROOT = frontend_dist.resolve()

    # Catch-all: serve index.html for any unmatched path so React Router handles
    # client-side navigation on hard refresh / direct URL access.
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Containment first, then is_file() — the same order every router uses, and
        # for the same reason: `full_path` is raw client input and this is a
        # FileResponse. Starlette percent-decodes the path parameter, so `%2e%2e`
        # arrives as `..` having survived the normalization every well-behaved
        # client does on literal `../`; and `Path.__truediv__` *discards* the left
        # side when the right is absolute, so a leading `/` (sent as `//etc/passwd`)
        # escapes without needing dots at all. Anything not inside dist falls
        # through to the SPA, which is what an unknown route should do regardless.
        candidate = frontend_dist / full_path
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = None
        if resolved is not None and resolved.is_relative_to(_DIST_ROOT) and resolved.is_file():
            return FileResponse(str(resolved), headers=_NO_CACHE)
        # Otherwise hand off to the SPA so React Router owns the route.
        return FileResponse(str(_INDEX_HTML), headers=_NO_CACHE)
