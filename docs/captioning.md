# AI Captioning

Batch-caption any selection of images using one of several backends:

| Model | VRAM | Notes |
|---|---|---|
| **Ollama** | varies | Points to a local Ollama instance on `localhost:11434` |
| **Florence-2** | ~5.5 GB | `microsoft/Florence-2-large`; styles: short, detailed, tags |
| **Florence-2 PromptGen v2** | ~5.5 GB | `MiaoshouAI/Florence-2-large-PromptGen-v2.0`, finetuned for Stable Diffusion prompts; styles: short, detailed, promptgen |
| **PaliGemma-2 3B** | ~6 GB | `google/paligemma2-3b-pt-448` — **the only gated model**: needs a HuggingFace token and the licence accepted on the model page. Styles: short, detailed, tags, booru |
| **JoyCaption Alpha Two** | ~17 GB | `fancyfeast/llama-joycaption-alpha-two-hf-llava` — Llama 3.1 8B + SigLIP; 12 styles (see below); supports custom prompts |
| **JoyCaption Beta One** | ~17 GB | `fancyfeast/llama-joycaption-beta-one-hf-llava` — Llama 3.1 8B + SigLIP2; 12 styles (see below); supports custom prompts |
| **WD14 Tagger** | CPU only | Booru-style tag output (Eva02 Large, ViT Large, or SwinV2); downloads from SmilingWolf on HuggingFace; adjustable confidence threshold |
| **OpenAI-compatible** | — | Any provider with a `/v1/chat/completions` vision endpoint — Gemini, Groq, OpenAI, LM Studio, llama.cpp, etc.; configured in Settings → LLM Providers |

Every model in the picker carries a one-line description of what it is — who
publishes it, what it is built on, and how its styles behave. That text comes
from the backend, beside the code that loads the model, so it cannot drift from
what actually runs. Only PaliGemma-2 mentions a token, because it is the only
model that needs one.

The picker lists **captioning models only**. Crucible also loads scorers,
detectors and embedding models (see [Quality Scoring](scoring.md) and
[Detection](detection.md)), and those never appear here — the same list feeds
the Captioning page, the gallery's Caption action, the image-detail sidebar and
Settings → Captioning → Default model.

Captions can also be **imported** rather than generated: from `.txt` sidecars during a folder import, via the per-dataset "Import captions" folder dialog, or by dragging a `.txt` file onto an image — see [Datasets & Gallery](gallery.md).

### JoyCaption styles

| Style | Output |
|---|---|
| `descriptive` | Detailed prose description |
| `casual` | Casual-tone description |
| `straightforward` | Factual, no-speculation caption |
| `sd_prompt` | Stable Diffusion prompt |
| `midjourney` | MidJourney prompt |
| `danbooru` | Danbooru-style tag list (comma-separated) |
| `e621` | e621-style tag list (comma-separated) |
| `rule34` | Rule34-style tag list (comma-separated) |
| `booru_like` | Generic booru tag list (comma-separated) |
| `art_critic` | Art-critic analysis (composition, style, symbolism) |
| `product` | Product listing caption |
| `social_media` | Social media post caption |

Tag-producing styles (`danbooru`, `e621`, `rule34`, `booru_like`) are treated the same as WD14/booru output — they produce a comma-separated list of tags stored directly in the caption text. A custom prompt overrides the style prompt entirely.

## Post-processing options

- **Strip refusal phrases & identity guesses** — removes the usual "I'm sorry, I can't…" boilerplate and speculative name-guessing
- **Strip thinking blocks** — removes `<think>…</think>` spans and reasoning preambles, which reasoning-capable models emit ahead of the caption proper
- **Normalise underscores in prose** — `word_word` → `word word`; applies to prose styles only, so booru tags keep their underscores
- **Strip hedging phrases** — "It appears to be a cat" → "a cat"
- **Merge redundant tags** — drops a tag that is contained in a longer one in the same caption (`tail` when `long tail` is present) and collapses exact duplicates; tag styles only → [details](tag-consolidation.md)
- Back up the original `.txt` sidecar before overwriting
- **Rename on caption** — after each caption is saved, rename the image file to `{subfolder_slug}_{NNN}.ext` (or `image_{NNN}.ext` for root images); useful for building consistently named datasets
- **Target resolution preprocessing** — when a target width/height is set, each image is center-cropped to that aspect ratio and scaled to that resolution *in memory* before being sent to the model; no files are written to disk

### The AI-artifacts flag

Whenever a generated caption contains a thinking block or a hedging phrase, the image is
flagged `has_ai_artifacts` — **whether or not** the matching strip option is ticked. The
flag records that the model produced cruft, so it stays on even when the cruft was cleaned
up, letting you find and re-caption those images later. It clears if the caption is
emptied.

This is the one quality flag scoring never sets. It shows up beside the scoring flags in
the dataset flag counts, the gallery filters and the **Exclude quality flags** option below
→ [details](scoring.md#quality-flags)

## Delimiter mode

Controls how a new caption is merged with an existing one:

| Mode | Behaviour |
|---|---|
| **Overwrite** (default) | Replaces the existing caption entirely |
| **Append** | Adds new text after the existing caption: `existing + delimiter + new` |
| **Prepend** | Adds new text before the existing caption: `new + delimiter + existing` |

The **delimiter** is the separator string inserted between the two parts when appending or prepending (default `", "`). Merge only runs when the image already has a non-empty caption; images with no prior caption always receive just the new text regardless of mode.

Note: Overwrite mode with *Uncaptioned only* scope skips already-captioned images; Append and Prepend always process all images in scope.

## Scope and filters

Available on the Captioning page and in the selection toolbar caption modal:

- **Scope** — All images, Uncaptioned only, or Selected images
- **Minimum aesthetic score** — skip images below this score (requires aesthetic scoring to have been run first)
- **Exclude quality flags** — skip images flagged as blurry, noisy, near-uniform, watermarked, duplicate, NSFW, or AI artifacts
- **Subfolder** — scope the run to a specific subfolder (shown when subfolders exist)

## Caption Pipeline

Chain two or more captioning steps that run in sequence as a single job:

- Each step has its own model, style, custom prompt, and delimiter settings
- Use `{previous_caption}` in a step's custom prompt to inject the prior step's output into the next model — enables iterative refinement (e.g. WD14 tags as input to an Ollama narrative prompt)
- Triggered via the **+ Add Step** button on the Captioning page; also available from the selection toolbar caption modal
- Runs as a background job with the same queue and progress display as a single-model run

## Job queuing

Multiple captioning or pipeline jobs can be submitted while one is already running; they execute serially and each can be cancelled independently. A queue badge in the page header shows how many jobs are waiting. Each job shows a descriptive auto-generated name (model and image count) in the queue; enter a custom **label** in the optional field before starting to distinguish runs more clearly.

## Prompt Preset Manager

Save and reload named combinations of model, style, and custom prompt text so you can reproduce captioning runs without re-entering settings.

## Model management

A **Model unload** button appears next to the model selector when a local model is currently loaded — click it to free VRAM immediately without restarting the app.
