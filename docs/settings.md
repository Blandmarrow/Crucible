# Settings

Route: `/settings` — accessible from the sidebar. Settings are grouped into eight tabs.

## Gallery

Browser-local preferences, each taking effect immediately:

- Images per page: 25 / 50 / 100 / 200 — controls gallery pagination and detail-view prefetch; lower values reduce memory usage with large high-resolution datasets
- Selection checkbox size: 14–32 px (default 18) — the size of the selection checkbox on gallery thumbnails. A live preview sits next to the slider, and open galleries update as you drag
- Subfolder rename on move: *Rename to subfolder name* (default) or *Keep original filenames* — when disabled, moving images to a subfolder updates their subfolder metadata only, without renaming the files
- License badge on cards (off by default) — shows each image's effective license on its gallery card, including a muted **No license** badge for images with none at either the image or dataset level. Applies immediately to every open gallery, and is not affected by the gallery toolbar's reset button (which is why it sits outside **Gallery defaults**). Off by default because most datasets are single-source, where a badge on every card is noise → [details](provenance.md)
- Style match meter on cards (**on** by default) — a thin bar under each thumbnail showing where that image's style-similarity score falls within the dataset's own scores. A percentile rather than the raw number, because the raw score is a cosine whose scale depends on which embedding model produced it → [details](scoring.md#reading-the-score). Images with no style score show nothing, and switching this off stops the gallery asking the server for the distribution at all. Like the license badge, it applies immediately to every open gallery and is outside **Gallery defaults**
- **Gallery defaults** — applied on first visit to a dataset, after which the filters you last used there take precedence (they are remembered per dataset and survive a browser restart; the gallery toolbar's **Reset filters** button clears them): default sort order, default caption filter (All / Captioned only / Uncaptioned only), default quality flag filter

## Captioning

Browser-local preferences, each taking effect immediately (applied once when the Captioning page loads model data):

- Default model
- Default caption style
- Default scope (Uncaptioned only / All images)
- Default delimiter mode (Overwrite / Append / Prepend)
- Strip refusals (default on)
- Rename on caption (default off)
- Save backup (default off)

If the default model is not among the ones currently on offer — Ollama not running, an LLM
provider still loading — the field shows blank, but your saved choice is kept and reappears
as soon as that service is back.
- **Reset remembered Captioning configuration** — forgets the Captioning page's remembered setup so the defaults above apply again on the next visit. It clears the global setup *and* the remembered filters for **every** dataset, not just the one you last used

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

## API Keys

Three credentials that used to be editable only by hand-editing `.env` and restarting:

| Key | What it unlocks |
|---|---|
| HuggingFace token | Downloading gated models — PaliGemma-2 is the one Crucible ships. Create one at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) and accept the model's license there first |
| Gelbooru API key | A higher rate limit for Gelbooru tag lookups on the [Booru page](workspace.md). Safebooru needs no key |
| Gelbooru user ID | The numeric ID that goes with the API key — both are required, and with either missing lookups stay anonymous |

Each row shows where its current value comes from: **Saved here** (this page), **Inherited
from `.env`**, or **Not set**. The last four characters are shown so you can tell which key
is in use; the rest is never sent back to the browser.

- **A key saved here overrides `.env`.** If both are set, the one on this page wins — the
  status line tells you which is in effect, so a value you typed is never silently ignored.
- **Clearing goes back to `.env`.** The **Clear** button drops the override rather than
  blanking the key: if `.env` still has a value, that one takes over again and the row
  switches to *Inherited*. Leaving the field blank changes nothing at all, so you can save
  one key without disturbing the others.
- **Changes apply immediately** — no restart. A HuggingFace token takes effect for the next
  model download; a download already in progress keeps the token it started with.
- **Keys are stored unencrypted** in the local database, the same as LLM provider keys.
  Crucible has no login and its file browser can already read `.env`, so treat the machine
  running it, not the database file, as the thing to protect.

Setting them in `.env` still works and needs no change — see the API keys section of the
[README](../README.md).
