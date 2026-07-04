# Crucible

Crucible is a local dataset engineering platform for AI image training. Instead of juggling folders, captioning scripts, scoring tools, duplicate finders, and export scripts, it brings the entire dataset workflow into one application.

**Import → Organize → Caption → Score & Curate → Refine → Version → Export**

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Node](https://img.shields.io/badge/node-18%2B-green)

![alt text](docs/images/image-3.png)

▶ [Watch the showcase on YouTube](https://www.youtube.com/watch?v=Ig4j5ijovCI)

---

## Workflow

Every step from a raw folder of images to a training-ready export, in order:

1. **Import** — pull images from local folders (with subfolder organization, an optional native "Browse…" picker, and `.txt` caption sidecars), or browse your filesystem and import directly → [details](docs/features.md#datasets--gallery)
2. **Organize** — group datasets into named categories; drag cards between sections to reassign → [details](docs/features.md#datasets--gallery)
3. **Caption** — batch-caption with local ML models (Florence-2, PaliGemma-2, JoyCaption, WD14, Ollama) or any OpenAI-compatible API → [details](docs/captioning.md)
4. **Score & Curate** — score every image across aesthetic, technical, watermark, NSFW, and style-similarity metrics, then filter by search, quality flags, score ranges, and detected object labels → [details](docs/scoring.md)
5. **Refine** — consolidate tags, batch-edit captions/crops/resizes, upscale, LUT color grade, detect & segment objects, and reorder manually → [details](docs/features.md#batch-operations)
6. **Version** — capture named snapshots and branches, and restore any prior state → [details](docs/versioning.md)
7. **Export** — output to Kohya, AI Toolkit, or plain folder format — ready to train Stable Diffusion, SDXL, Flux, and more — with per-export filtering and resizing → [details](docs/export.md)

---

## Features

### Dataset management
- **Organize** datasets into named categories; drag cards between sections to reassign → [details](docs/features.md#datasets--gallery)
- **Import** images from local folders into named datasets with subfolder organization, an optional native "Browse…" folder picker, and optional import of `.txt` caption sidecars → [details](docs/features.md#datasets--gallery)
- **Sync** a dataset with its folder on disk — rescan to register images and pick up `.txt` captions added outside the app, import captions from a folder, or auto-rescan on open → [details](docs/features.md#datasets--gallery)

### Captioning
- **Caption** images in batch using local ML models (Florence-2, PaliGemma-2, JoyCaption, WD14, Ollama) or any OpenAI-compatible API — or drag a `.txt` file onto an image to set its caption → [details](docs/captioning.md)

### Quality & curation
- **Score** every image across aesthetic, technical, watermark, NSFW, and style similarity metrics → [details](docs/scoring.md)
- **Filter & curate** via search, quality flags, score ranges, and detected object labels → [details](docs/features.md#datasets--gallery)

### Object detection
- **Detect** objects with Florence-2 bounding-box detection, NudeNet body-part detection, or Grounded SAM2 (SAM2 + Grounding DINO) segmentation masks with text or point prompts → [details](docs/features.md#object-detection)

### Editing & processing
- **Batch edit** captions, crops, resizes, and renames across any selection → [details](docs/features.md#batch-operations)
- **Consolidate tags** — merge semantically similar tags or phrases (e.g. `car` / `automobile`) dataset-wide with a preview, and drop redundant wording (`tail` when `long tail` is present) per-image or across a selection; works on booru tags and natural-language captions alike → [details](docs/features.md#tag-consolidation)
- **Process** images with ML upscaling and LUT color grading → [details](docs/features.md#image-processing)
- **Reorder** images manually with drag-and-drop; lock a custom sequence and renumber files to match — export always follows the custom order → [details](docs/features.md#manual-image-ordering)

### Versioning & export
- **Version** datasets with named snapshots and branches — restore any prior state → [details](docs/versioning.md)
- **Export** to Kohya, AI Toolkit, or plain folder format with per-export filtering and resizing → [details](docs/export.md)

### Workspace & tooling
- **Statistics** — inspect dataset composition with caption-length, token, resolution, and score histograms, plus CSV export → [details](docs/features.md#statistics-dashboard)
- **Split view** — run any pages side-by-side in independently scrollable panes → [details](docs/features.md#split-view)
- **Browse** your filesystem, preview generation metadata, and import directly into datasets → [details](docs/features.md#file-browser)
- **Look up** booru tags to build tag vocabularies for your training subjects → [details](docs/features.md#booru-tag-lookup)
- **Logs** — review job history (status, duration, errors) and captured JS runtime errors; a persistent overlay auto-surfaces errors without navigating away → [details](docs/features.md#logs)

All long-running operations run in a background job queue and stream real-time progress to the UI via SSE.

---

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.12+ |
| Node.js | 18+ |
| GPU (ML features) | NVIDIA CUDA 12.6+ · AMD ROCm 6.3+ (Linux only) · Apple Silicon |

Python and Node.js will be installed by `setup` if missing — you will be prompted before each download. GPU inference requires ~6 GB VRAM for most models; JoyCaption requires ~17 GB. The technical scorer and duplicate detector run on CPU only.

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

During setup you are also prompted to install **SAM2** (Segment Anything Model 2, ~50 MB from GitHub) — required only for Grounded SAM2 segmentation; all other features work without it.

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
pip install "torch>=2.7" --index-url <INDEX_URL>
```

**NVIDIA GPU** — check your CUDA version with `nvidia-smi`. CUDA 12.6 or newer is required:

| CUDA version | `<INDEX_URL>` |
|---|---|
| ≥ 12.8 | `https://download.pytorch.org/whl/cu128` |
| 12.6 | `https://download.pytorch.org/whl/cu126` |

> If your driver reports CUDA 12.4 or older, update your NVIDIA driver to version 560.94 or newer before proceeding.

**AMD GPU / ROCm** (Linux only) — ROCm 6.3 or newer is required:

| ROCm version | `<INDEX_URL>` |
|---|---|
| ≥ 6.3 | `https://download.pytorch.org/whl/rocm6.3` |

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

**4.5. Install SAM2 (optional — segmentation features)**

```bash
pip install "git+https://github.com/facebookresearch/sam2.git" pycocotools
```

Skipping this disables Grounded SAM2 segmentation only; everything else still works.

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

**Backend:** Python · FastAPI · SQLAlchemy (async) · SQLite · Alembic · Pillow · OpenCV · PyTorch · Transformers · OpenCLIP · sentence-transformers · spandrel

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
