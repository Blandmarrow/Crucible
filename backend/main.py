import os
import signal
import threading
from contextlib import asynccontextmanager
from pathlib import Path

# Triton is unavailable on Windows; disable TorchDynamo so torch.compile is never
# attempted during inference (single-image inference gains nothing from it anyway).
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import init_db

if settings.hf_token:
    os.environ.setdefault("HF_TOKEN", settings.hf_token)
from backend.routers import booru, captions, captioning, datasets, detection, export, filesystem, images, jobs, models, quality, system, upscaling
from backend.workers.job_queue import job_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    await init_db()
    await job_queue.start()
    yield
    await job_queue.stop()


app = FastAPI(
    title="Dataset Manager",
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

@app.post("/api/v1/shutdown", status_code=204)
async def shutdown():
    threading.Thread(target=lambda: os.kill(os.getpid(), signal.SIGTERM), daemon=True).start()


# Serve built React frontend — must come last so API routes take priority
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
