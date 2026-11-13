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

If a remembered configuration still names something Crucible cannot caption with
— a detector left in a saved pipeline step, say — the Run button is disabled and
the step that holds it says so, instead of the run failing part-way through and
leaving you to guess which step was at fault. A model that is merely *missing*
from the list is different and does not block anything: Ollama may simply not be
running, so you get a note rather than a refusal.

Captions can also be **imported** rather than generated: from `.txt` sidecars during a folder import, via the per-dataset "Import captions" folder dialog, or by dragging a `.txt` file onto an image — see [Getting images in](datasets.md#getting-images-in).

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

## Your setup is remembered

The page remembers your model, prompts, options and pipeline steps as you change them. They are saved in the app rather than your browser, so they are the same in every browser and survive clearing browser data. **Reset to defaults** clears them. The subfolder and quality filters are still remembered per dataset in each browser.

A setup saved before this change is copied over the first time each browser opens the updated app. If a second browser holds a different one, you are asked which to keep.

## Post-processing options

- **Strip refusal phrases & identity guesses** — removes the usual "I'm sorry, I can't…" boilerplate and speculative name-guessing
- **Strip thinking blocks** — removes `<think>…</think>` spans and reasoning preambles, which reasoning-capable models emit ahead of the caption proper
- **Normalise underscores in prose** — `word_word` → `word word`; applies to prose styles only, so booru tags keep their underscores
- **Strip hedging phrases** — "It appears to be a cat" → "a cat"
- **Merge redundant tags** — drops a tag that is contained in a longer one in the same caption (`tail` when `long tail` is present) and collapses exact duplicates; tag styles only → [details](tag-consolidation.md)
- Back up the original `.txt` sidecar before overwriting
- **Rename on caption** — after each caption is saved, rename the image file to `{subfolder_slug}_{NNN}.ext` (or `image_{NNN}.ext` for root images); useful for building consistently named datasets
- The backup and rename options work in single-model runs only. Add a second step and both are greyed out, because a pipeline does not support them yet
- **Target resolution preprocessing** — when a target width/height is set, each image is center-cropped to that aspect ratio and scaled to that resolution *in memory* before being sent to the model; no files are written to disk

### The AI-artifacts flag

Whenever a generated caption contains a thinking block or a hedging phrase, the image is
flagged `has_ai_artifacts` — **whether or not** the matching strip option is ticked. The
flag records that the model produced cruft, so it stays on even when the cruft was cleaned
up, letting you find and re-caption those images later. It clears if the caption is
emptied.

This is the one quality flag scoring never sets. It shows up beside the scoring flags in
the dataset flag counts, the gallery filters and the **Exclude quality flags** option below
→ [details](reading-scores.md#quality-flags)

## Delimiter mode

Controls how a new caption is merged with an existing one:

| Mode | Behaviour |
|---|---|
| **Overwrite** (default) | Replaces the existing caption entirely |
| **Append** | Adds new text after the existing caption: `existing + delimiter + new` |
| **Prepend** | Adds new text before the existing caption: `new + delimiter + existing` |

The **delimiter** is the separator string inserted between the two parts when appending or prepending (default `", "`). Merge only runs when the image already has a non-empty caption; images with no prior caption always receive just the new text regardless of mode.

Note: Overwrite mode with *Uncaptioned only* scope skips already-captioned images; Append and Prepend always process all images in scope. This holds for single-model runs and pipelines alike; in a pipeline, the first step's mode decides.

## Scope and filters

Available on the Captioning page. The gallery's **Caption** action has none of them: it
captions exactly the selected images, captioned or not, and its delimiter mode decides
whether a new caption replaces or joins the old one.

- **Scope** — All images, Uncaptioned only, or Selected images
- **Minimum aesthetic score** — skip images below this score (requires aesthetic scoring to have been run first)
- **Exclude quality flags** — skip images flagged as blurry, noisy, near-uniform, watermarked, duplicate, NSFW, or AI artifacts
- **Subfolder** — scope the run to a specific subfolder (shown when subfolders exist)

## Caption Pipeline

Chain two or more captioning steps that run in sequence as a single job:

- Each step has its own model, style, custom prompt, and delimiter settings
- Use `{previous_caption}` in a step's custom prompt to inject the prior step's output into the next model — enables iterative refinement (e.g. WD14 tags as input to an Ollama narrative prompt). In the first step it is the caption the image already has: see [Building on the current caption](#building-on-the-current-caption)
- Triggered via the **+ Add Step** button on the Captioning page (the gallery's Caption action runs one model only)
- Uses the same scope and filters as a single-model run
- Runs as a background job with the same queue and progress display as a single-model run

## Building on the current caption

A model that reads a prompt can be shown the caption an image already has, and asked to
improve it rather than start again. That works with Ollama, any OpenAI-compatible
provider and JoyCaption. Florence-2, PaliGemma-2 and WD14 ignore prompts, so the options
below do not appear for them.

- **In any custom prompt**, `{previous_caption}` is replaced with the image's current
  caption. An image with no caption gets an empty string.
- **On the Captioning page**, the first step's **Include current caption** box adds it to
  the prompt for you. With the prompt box empty, it asks the model to improve the
  caption. It is greyed out for *Uncaptioned only* with **Overwrite**, since every image
  in that scope has nothing to include. Later steps have **Include previous caption**,
  which passes on the step before's output instead.
- A job uses each caption as it is when the job **starts**. If you queue a job behind
  another one that rewrites the same images, it builds on that job's results.
- **On an image's detail page**, open **Generate with AI** and pick **Fix current**. Choose
  a model, say what is wrong ("the broken glass is their reflection in a cracked mirror")
  and press **Fix Caption**. The model sees the image, the caption and your note, and
  rewrites the caption, keeping what was right. Unsaved edits in the caption box are saved
  first, so the model works from what you see. When the fix lands, **Undo fix** puts the old
  caption back in the box; press **Save** to keep it. If the model fails or returns nothing,
  you are told so and the caption is left as it was.

## Job queuing

Multiple captioning or pipeline jobs can be submitted while one is already running; they execute serially and each can be cancelled independently. Within one job, an OpenAI-compatible provider or Ollama can be sent several images at once: see **Max concurrent requests** in [Settings](settings.md#llm-providers). A queue badge in the page header shows how many jobs are waiting. Each job shows a descriptive auto-generated name (model and image count) in the queue; enter a custom **label** in the optional field before starting to distinguish runs more clearly.

## When images come back uncaptioned

Some images can finish a run with no caption at all. Every one of them is now **counted and
named**: the Captioning page shows an amber "N images failed" badge with a sentence saying
why, and the same sentence appears on the run's row in the Logs page. Nothing is written
for those images — in particular, a caption they already had is left alone rather than
blanked.

Four reasons, each with its own fix:

- **"came back empty — the model returned no caption text."** The provider answered, but
  with nothing in it. The usual cause is a reasoning model spending its entire token budget
  thinking: raise **Max tokens** in Settings → LLM Providers. The terminal log names the
  model and the provider's `finish_reason`, which confirms it. For a WD14 run the reason
  reads *no tags above the confidence threshold* instead — lower **Tag threshold**.
- **"had their whole caption removed by post-processing."** The reply was entirely a
  thinking block or entirely a refusal, and **Strip thinking blocks** or **Strip refusal
  phrases** removed all of it. Turn the matching option off to see what the model actually
  said, or change the prompt.
- **"timed out."** The provider did not answer within its configured timeout — raise
  **Timeout** in Settings → LLM Providers. With a provider's **Max concurrent requests**
  above 1, a local server that queues the extra requests can time them out too; lower
  that instead. Ollama reports a timed-out request as *returned no caption text*, with a
  note to lower its Max concurrent requests when that is above 1.
- **"refused ... for too many requests (HTTP 429)."** A hosted provider, often on a free
  tier, turned away requests that arrived too fast. Lower **Max concurrent requests** in
  Settings → LLM Providers, or run again later.

These are per-image and usually intermittent, so the quickest recovery is to re-run over
the images that still have no caption: set **Scope** to *Uncaptioned only* and leave
**Existing captions** on *Overwrite*. Both are required — the run is narrowed to
uncaptioned images only when they agree, so switching **Existing captions** to *Append* or
*Prepend* re-captions the whole dataset instead.

That reaches an image that had no caption before the run. An image whose caption was left
intact — the post-processing and refusal cases above, on an image that was already
captioned — still has one, so an *Uncaptioned only* re-run skips it by design; retry those
with **Scope** set to *Selected* after picking them in the gallery.

## Prompt Preset Manager

Save and reload named combinations of model, style, and custom prompt text so you can reproduce captioning runs without re-entering settings.

Presets are saved in the app rather than your browser, so they are the same in every browser and survive clearing browser data. Presets saved before this change are copied over the first time each browser opens the updated app, and identical ones are merged.

## Model management

A **Model unload** button appears next to the model selector when a local model is currently loaded — click it to free VRAM immediately without restarting the app. It is disabled while a captioning run is going; a model a running job is using cannot be unloaded, and if you try anyway (from a second tab, say) you are told it is in use and the run carries on unharmed.
