# AI Captioning

Batch-caption any selection of images using one of several backends:

| Model | VRAM | Notes |
|---|---|---|
| **Ollama** | varies | Points to a local Ollama instance on `localhost:11434` |
| **Florence-2** | ~5.5 GB | Styles: short, detailed, tags, dense, promptgen |
| **PaliGemma-2 3B** | ~6 GB | Requires HuggingFace token; styles: short, detailed, tags, booru |
| **WD14 Tagger** | CPU only | Booru-style tag output (Eva02 Large, ViT Large, or SwinV2); downloads from SmilingWolf on HuggingFace; adjustable confidence threshold |
| **OpenAI-compatible** | — | Any provider with a `/v1/chat/completions` vision endpoint — Gemini, Groq, OpenAI, LM Studio, llama.cpp, etc.; configured in Settings → LLM Providers |

## Post-processing options

- Strip common AI refusal phrases automatically
- Back up the original `.txt` sidecar before overwriting
- **Rename on caption** — after each caption is saved, rename the image file to `{subfolder_slug}_{NNN}.ext` (or `image_{NNN}.ext` for root images); useful for building consistently named datasets
- **Target resolution preprocessing** — when a target width/height is set, each image is center-cropped to that aspect ratio and scaled to that resolution *in memory* before being sent to the model; no files are written to disk

## Job queuing

Multiple captioning or pipeline jobs can be submitted while one is already running; they execute serially and each can be cancelled independently. A queue badge in the page header shows how many jobs are waiting. Each job shows a descriptive auto-generated name (model and image count) in the queue; enter a custom label in the optional field before starting if you need to distinguish runs more clearly.

## Prompt Preset Manager

Save and reload named combinations of model, style, and custom prompt text so you can reproduce captioning runs without re-entering settings.
