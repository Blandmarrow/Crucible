import os
import signal
import threading
import time
import warnings
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# Triton is unavailable on Windows; disable TorchDynamo so torch.compile is never
# attempted during inference (single-image inference gains nothing from it anyway).
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
# HuggingFace symlink warning: expected on Windows without Developer Mode — cache still works fine
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# torchao: no Triton on Windows — expected, no runtime effect
warnings.filterwarnings("ignore", message=".*Detected no triton.*")
# open_clip: ViT-L-14 weights trained with QuickGELU; no effect on inference output
warnings.filterwarnings("ignore", category=UserWarning, message=".*QuickGELU activation.*")
# pydantic: HuggingFace internals use Field(repr=, frozen=) in a way Pydantic v2 flags; no runtime effect
try:
    from pydantic.warnings import UnsupportedFieldAttributeWarning
    warnings.filterwarnings("ignore", category=UnsupportedFieldAttributeWarning)
except ImportError:
    pass
# torch.distributed: Windows doesn't support process stream redirects; logged via logging not warnings
logging.getLogger("torch.distributed.elastic.multiprocessing.redirects").setLevel(logging.ERROR)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import init_db

if settings.hf_token:
    os.environ.setdefault("HF_TOKEN", settings.hf_token)
from backend.routers import booru, captions, captioning, comfy, datasets, detection, export, filesystem, images, jobs, lut, models, providers, quality, settings as settings_router, system, tag_consolidation, upscaling, versioning
from backend.workers.job_queue import job_queue, mark_interrupted_jobs


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    await init_db()
    await mark_interrupted_jobs()
    await job_queue.start()
    yield
    await job_queue.stop()


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

PREFIX = "/api/v1"
app.include_router(datasets.router, prefix=PREFIX)
app.include_router(images.router, prefix=PREFIX)
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

    # Catch-all: serve index.html for any unmatched path so React Router handles
    # client-side navigation on hard refresh / direct URL access.
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Real file in dist (favicon.svg, apple-touch-icon.png, …) → serve it.
        candidate = frontend_dist / full_path
        if candidate.is_file():
            return FileResponse(str(candidate), headers=_NO_CACHE)
        # Otherwise hand off to the SPA so React Router owns the route.
        return FileResponse(str(frontend_dist / "index.html"), headers=_NO_CACHE)
