# Crucible

A local web-based application for building, curating, and exporting Stable Diffusion training datasets. Manage your image collections with AI-powered captioning, multi-metric quality scoring, and flexible export to the most common training formats.

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Node](https://img.shields.io/badge/node-18%2B-green)

![alt text](docs/images/image-3.png)

▶ [Watch the showcase on YouTube](https://www.youtube.com/watch?v=Ig4j5ijovCI)

---

## Features

- **Import** images from local folders into named datasets with subfolder organization → [details](docs/features.md#datasets--gallery)
- **Caption** images in batch using local ML models (Florence-2, PaliGemma-2, WD14, Ollama) or any OpenAI-compatible API → [details](docs/captioning.md)
- **Score** every image across aesthetic, technical, watermark, and style similarity metrics → [details](docs/scoring.md)
- **Filter & curate** via search, quality flags, score ranges, and detected object labels → [details](docs/features.md#datasets--gallery)
- **Version** datasets with named snapshots and branches — restore any prior state → [details](docs/versioning.md)
- **Batch edit** captions, crops, resizes, and renames across any selection → [details](docs/features.md#batch-operations)
- **Process** images with ML upscaling and LUT color grading → [details](docs/features.md#image-processing)
- **Detect** objects and ground phrases using Florence-2 bounding-box detection → [details](docs/features.md#object-detection)
- **Export** to Kohya, AI Toolkit, or plain folder format with per-export filtering and resizing → [details](docs/export.md)
- **Split view** — run any pages side-by-side in independently scrollable panes → [details](docs/features.md#split-view)
- **Browse** your filesystem, preview generation metadata, and import directly into datasets → [details](docs/features.md#file-browser)
- **Look up** booru tags to build tag vocabularies for your training subjects → [details](docs/features.md#booru-tag-lookup)

All long-running operations run in a background job queue and stream real-time progress to the UI via SSE.

---

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.10+ |
| Node.js | 18+ |
| GPU (ML features) | NVIDIA CUDA · AMD ROCm 6.1+ (Linux only) · Apple Silicon |

Python and Node.js are installed automatically by `setup` if missing. GPU inference requires ~6 GB VRAM minimum; the technical scorer and duplicate detector run on CPU only.

---

## Installation

```bash
git clone https://github.com/Blandmarrow/Crucible
```

**Windows** — double-click `Crucible.bat` and choose **Setup**, or run directly:

```powershell
.\manage.ps1 setup
```

**Linux / macOS**:

```bash
chmod +x manage.sh && ./manage.sh setup
```

Setup auto-detects your GPU and installs the matching PyTorch wheel — no manual pre-install needed.

**Optional: API keys** — copy `.env.example` to `.env` if you need PaliGemma-2 or Gelbooru:

```env
HF_TOKEN=hf_...           # PaliGemma-2 (accept license at huggingface.co first)
GELBOORU_API_KEY=...      # Optional — Safebooru works without a key
GELBOORU_USER_ID=...
```

---

## Usage

**Windows** — double-click `Crucible.bat` and choose **Start**, or:

| Command | Effect |
|---|---|
| `.\manage.ps1 start` | Production server on :8000 |
| `.\manage.ps1 dev` | Backend hot-reload + Vite dev server (:5173) |
| `.\manage.ps1 update` | Pull latest + rebuild |

**Linux / macOS** — same commands via `./manage.sh start`, `./manage.sh dev`, `./manage.sh update`.

To shut down, click the power icon in the top-right of the app, or press `Ctrl+C` in the terminal.

---

## Tech Stack

**Backend:** Python · FastAPI · SQLAlchemy (async) · SQLite · Alembic · Pillow · OpenCV · PyTorch · Transformers · OpenCLIP · spandrel

**Frontend:** React 19 · TypeScript · Vite · TanStack Query · Zustand · Tailwind CSS · Recharts

---

## Docs

| Topic | |
|---|---|
| Full feature reference | [docs/features.md](docs/features.md) |
| AI Captioning | [docs/captioning.md](docs/captioning.md) |
| Quality Scoring | [docs/scoring.md](docs/scoring.md) |
| Dataset Versioning | [docs/versioning.md](docs/versioning.md) |
| Export | [docs/export.md](docs/export.md) |
| Troubleshooting | [docs/troubleshooting.md](docs/troubleshooting.md) |
