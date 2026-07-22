# Settings

Route: `/settings` — accessible from the sidebar. Settings are grouped into seven tabs.

## Gallery

Browser-local preferences, each taking effect immediately:

- Images per page: 25 / 50 / 100 / 200 — controls gallery pagination and detail-view prefetch; lower values reduce memory usage with large high-resolution datasets
- Selection checkbox size: 14–32 px (default 18) — the size of the selection checkbox on gallery thumbnails. A live preview sits next to the slider, and open galleries update as you drag
- Subfolder rename on move: *Rename to subfolder name* (default) or *Keep original filenames* — when disabled, moving images to a subfolder updates their subfolder metadata only, without renaming the files
- License badge on cards (off by default) — shows each image's effective license on its gallery card. Off by default because most datasets are single-source, where a badge on every card is noise → [details](provenance.md)
- **Gallery defaults** — applied on first visit to a dataset (session state takes precedence on subsequent visits): default sort order, default caption filter (All / Captioned only / Uncaptioned only), default quality flag filter

## Captioning

Browser-local preferences, each taking effect immediately (applied once when the Captioning page loads model data):

- Default model
- Default caption style
- Default scope (Uncaptioned only / All images)
- Default delimiter mode (Overwrite / Append / Prepend)
- Strip refusals (default on)
- Rename on caption (default off)
- Save backup (default off)

## UI Behavior

Preferences that take effect immediately (browser-local unless noted):

- Default-focused button in destructive confirmation dialogs: *Cancel* (safe default) or *Confirm* (faster workflows)
- Branch snapshot behavior: *Ask* (shows a prompt before checkout or branch creation, letting you choose whether to create a snapshot) or *Auto* (always creates snapshots without prompting)
- **Auto-rescan dataset on open** (off by default) — when enabled, opening a dataset gallery scans its folder on disk for new images and `.txt` captions added outside the app. Unlike the settings above, this is a **server-side** preference (persisted per-install, shared across browsers)

## Quality Thresholds

Configurable number inputs (require Save; changes apply to the next scoring or detection run only):

| Setting | Controls |
|---|---|
| Blur threshold | Laplacian variance cutoff for `is_blurry` (default 100) |
| Noise threshold | Smooth-region std dev cutoff for `is_noisy` (default 15) |
| Uniformity threshold | Grayscale std dev cutoff for `is_uniform` (default 12) |
| Watermark threshold | CLIP zero-shot score cutoff for `has_watermark` (default 0.6) |
| Duplicate threshold | pHash Hamming distance cutoff for `is_duplicate` (default 8) |
| NSFW threshold | Marqo classifier score cutoff for `is_nsfw` (default 0.5) |
| DINO box confidence | Grounding DINO minimum confidence before passing a box to SAM2 (default 0.35) |
| SAM 3 confidence | SAM 3 minimum instance confidence for a segmentation mask to be kept (default 0.5) |

## Versioning

Version control mode (Off / Manual / Auto; see [Dataset Versioning](versioning.md)) plus branch snapshot behavior. Requires Save for the version control mode.

## LLM Providers

Add, edit, and delete OpenAI-compatible API provider configurations for use as captioning backends:

- Name and Base URL are required; API key is optional (leave blank for local servers)
- Default model — selected from a hardcoded preset list for well-known cloud APIs (Gemini, Groq, OpenAI, Together.ai), or fetched live from local servers (LM Studio, llama.cpp) via a refresh button, or typed freely
- Max image resolution (128–4096 px) — images are JPEG-encoded at this size before being sent
- Max tokens — controls the length of generated captions (64–32768)

## ComfyUI

Server-side settings for the ComfyUI generation page, shared across all datasets (each requires Save; see [ComfyUI Generation](comfyui.md)):

- Server URL — base URL of your ComfyUI server (default port 8188). **Test connection** checks reachability before saving. The server is contacted by the Crucible backend, so it must be reachable from the machine running Crucible
- Workflow folder — default folder scanned by the **Scan folder…** button on the ComfyUI page, with a **Browse…** picker; a path on the machine running Crucible
