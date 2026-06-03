# Crucible

A local web-based application for building, curating, and exporting Stable Diffusion training datasets. Manage your image collections with AI-powered captioning, multi-metric quality scoring, and flexible export to the most common training formats.

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Node](https://img.shields.io/badge/node-18%2B-green)

![alt text](docs/images/image-3.png)

▶ [Watch the showcase on YouTube](https://www.youtube.com/watch?v=Ig4j5ijovCI)

---

## Features

- **Organize** datasets into named categories; drag cards between sections to reassign → [details](docs/features.md#datasets--gallery)
- **Import** images from local folders into named datasets with subfolder organization → [details](docs/features.md#datasets--gallery)
- **Caption** images in batch using local ML models (Florence-2, PaliGemma-2, WD14, Ollama) or any OpenAI-compatible API → [details](docs/captioning.md)
- **Score** every image across aesthetic, technical, watermark, and style similarity metrics → [details](docs/scoring.md)
- **Filter & curate** via search, quality flags, score ranges, and detected object labels → [details](docs/features.md#datasets--gallery)
- **Version** datasets with named snapshots and branches — restore any prior state → [details](docs/versioning.md)
- **Batch edit** captions, crops, resizes, and renames across any selection → [details](docs/features.md#batch-operations)
- **Process** images with ML upscaling and LUT color grading → [details](docs/features.md#image-processing)
- **Detect** objects and ground phrases using Florence-2 bounding-box detection → [details](docs/features.md#object-detection)
- **Reorder** images manually with drag-and-drop; lock a custom sequence and renumber files to match — export always follows the custom order → [details](docs/features.md#manual-image-ordering)
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

Python and Node.js will be installed by `setup` if missing — you will be prompted before each download. GPU inference requires ~6 GB VRAM minimum; the technical scorer and duplicate detector run on CPU only.

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

Setup auto-detects your GPU and prompts before downloading the matching PyTorch wheel (~2.5 GB) — no manual pre-install needed.

<details>
<summary><strong>Manual installation (if you prefer not to use the setup script)</strong></summary>

**1. Create the Python virtual environment**

```powershell
# Windows
python -m venv venv
```
```bash
# Linux / macOS
python3 -m venv venv
```

> **Tip**: Add `--system-site-packages` if you already have a GPU-capable PyTorch installed in your system Python and want to reuse it instead of downloading it again.

**2. Activate the virtual environment**

```powershell
# Windows
venv\Scripts\Activate.ps1
```
```bash
# Linux / macOS
source venv/bin/activate
```

Keep the venv active for all remaining steps.

**3. Install PyTorch**

This must happen *before* `requirements.txt` so that packages like `open_clip_torch` link against the GPU build. Replace `<INDEX_URL>` with the URL matching your hardware from the tables below:

```bash
pip install "torch>=2.0" --index-url <INDEX_URL>
```

**NVIDIA GPU** — check your CUDA version with `nvidia-smi`:

| CUDA version | `<INDEX_URL>` |
|---|---|
| ≥ 12.8 | `https://download.pytorch.org/whl/cu128` |
| 12.6 | `https://download.pytorch.org/whl/cu126` |
| 12.4 | `https://download.pytorch.org/whl/cu124` |
| 12.1 | `https://download.pytorch.org/whl/cu121` |
| 11.8 | `https://download.pytorch.org/whl/cu118` |

**AMD GPU / ROCm** (Linux only) — check your ROCm version with `rocminfo`:

| ROCm version | `<INDEX_URL>` |
|---|---|
| ≥ 6.3 | `https://download.pytorch.org/whl/rocm6.3` |
| 6.2 | `https://download.pytorch.org/whl/rocm6.2` |
| 6.1 | `https://download.pytorch.org/whl/rocm6.1` |

**Apple Silicon** — no special wheel needed; standard PyTorch already includes MPS support. Skip this step.

**CPU only** — skip this step; PyTorch will be installed as a CPU-only build in step 4.

**4. Install Python dependencies**

```powershell
# Windows
pip install -r backend\requirements.txt
```
```bash
# Linux / macOS
pip install -r backend/requirements.txt
```

**5. Build the frontend**

```bash
cd frontend
npm install
npm run build
```

Database migrations run automatically the first time you start the app with `.\manage.ps1 start` / `./manage.sh start`.

</details>

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
