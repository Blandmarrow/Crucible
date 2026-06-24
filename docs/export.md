# Export

Three fully implemented export formats, all with identical filter and processing options:

| Format | Use case |
|---|---|
| **Kohya** | Kohya SS LoRA / full fine-tune training |
| **AI Toolkit** | AI Toolkit training |
| **Plain folder** | Any other framework (`images/` + `captions.jsonl`) |

## Per-export options

- Minimum aesthetic score filter
- Captioned-only filter
- Per-flag exclusions (blurry, noisy, uniform, watermarked, duplicate)
- Minimum style similarity filter
- Image format conversion (original / JPEG with quality setting)
- Resize longest side (downscale only)
- Caption sidecar format: `.txt`, `.caption`, or single `captions.jsonl`
- Subfolder scoping — export images from one or more selected subfolders; a checklist lets you pick any combination
- Export order follows the **Custom order** drag sequence when set, with `created_at` as tiebreak — numbered filenames (`0001.jpg`, `0002.jpg`, …) in Kohya and AI Toolkit formats reflect this order
- **Strip metadata** — forces a lossless PIL round-trip to discard embedded PNG text chunks (A1111 `parameters`, ComfyUI `workflow`/`prompt`, EXIF) even when no format conversion or resize is requested
- **Captions only** — skip image files entirely and export only caption sidecars / JSONL manifests; useful for updating captions in an existing dataset without re-copying images
- **Live export preview** — shows exact will-export and excluded counts (broken down by filter reason) before you run
