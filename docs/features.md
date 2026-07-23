# Feature Reference

An index of Crucible's documentation. Each topic below has its own page.

## By workflow

The order images travel through the app:

| Step | Topic | |
|---|---|---|
| 1 | Import images, organize datasets, browse and filter the gallery | [Datasets & Gallery](gallery.md) |
| 1 | Generate images with your own ComfyUI workflow | [ComfyUI Generation](comfyui.md) |
| 2 | Caption with local ML models or any OpenAI-compatible API | [AI Captioning](captioning.md) |
| 3 | Score for aesthetics, technical quality, watermarks, NSFW, and style | [Quality Scoring](scoring.md) |
| 4 | Detect and segment objects; crop to subject; locate watermarks | [Object Detection](detection.md) |
| 5 | Batch-edit captions, crops, resizes; upscale and LUT color grade | [Batch Editing & Image Processing](editing.md) |
| 5 | Merge synonymous and redundant tags across a dataset | [Tag Consolidation](tag-consolidation.md) |
| 6 | Snapshot, branch, diff, and restore dataset state | [Dataset Versioning](versioning.md) |
| 7 | Output to Kohya, AI Toolkit, or a plain folder — with loss masks | [Export](export.md) |

## By page

| Sidebar item | Documented in |
|---|---|
| Datasets, Gallery, image detail | [gallery.md](gallery.md) |
| Captioning | [captioning.md](captioning.md) |
| Score images | [scoring.md](scoring.md) |
| Stats | [statistics.md](statistics.md) |
| Bulk Edit | [editing.md](editing.md) · [detection.md](detection.md) |
| Consolidate Tags | [tag-consolidation.md](tag-consolidation.md) |
| Versions | [versioning.md](versioning.md) |
| ComfyUI | [comfyui.md](comfyui.md) |
| Export | [export.md](export.md) |
| Booru Browser | [workspace.md](workspace.md#booru-tag-lookup) |
| File Browser | [workspace.md](workspace.md#file-browser) |
| Logs | [workspace.md](workspace.md#logs) |
| Settings | [settings.md](settings.md) |

## Everything else

| Topic | |
|---|---|
| Source & license provenance — where images came from, what you may do with them, export credits | [provenance.md](provenance.md) |
| Statistics dashboard — histograms, tag frequency, detection audit, CSV export | [statistics.md](statistics.md) |
| Settings reference — all seven tabs | [settings.md](settings.md) |
| Background jobs, restart & shutdown, hardware meters, split view, logs, file browser, booru lookup | [workspace.md](workspace.md) |

All long-running operations run in a background job queue and stream real-time progress to the UI — see [Background jobs](workspace.md#background-jobs--the-top-bar).
