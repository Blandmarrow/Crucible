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
- Per-flag exclusions (blurry, noisy, near-uniform, watermarked, duplicate, NSFW, AI artifacts)
- Minimum style similarity filter
- Image format conversion (original / JPEG with quality setting)
- Resize longest side (downscale only)
- Caption sidecar format: `.txt`, `.caption`, or single `captions.jsonl`
- Subfolder scoping — export images from one or more selected subfolders; a checklist lets you pick any combination
- Export order follows the **Custom order** drag sequence when set, with `created_at` as tiebreak — numbered filenames (`0001.jpg`, `0002.jpg`, …) in Kohya and AI Toolkit formats reflect this order
- If two images would land on the same output filename (e.g. `same.png` and `same.jpg`, or when converting both to one format), the second is suffixed `_001`, `_002`, … so no image, caption, or mask overwrites another
- **Strip metadata** — forces a lossless PIL round-trip to discard embedded PNG text chunks (A1111 `parameters`, ComfyUI `workflow`/`prompt`, EXIF) even when no format conversion or resize is requested
- **Captions only** — skip image files entirely and export only caption sidecars / JSONL manifests; useful for updating captions in an existing dataset without re-copying images
- **Live export preview** — shows exact will-export and excluded counts (broken down by filter reason) before you run
- **Loss masks** — see below

## Loss masks (masked training loss)

When **Export masks** is enabled, every exported image gets a matching grayscale
mask PNG rasterized from its object detections (see [Object Detection](detection.md)),
sized to the exported image (including any resize). White areas train, black areas
are ignored. SAM2/SAM3 detections contribute their precise polygon masks;
bbox-only detections (Florence-2, NudeNet) contribute filled rectangles.

Options:

- **Detection labels** — pick which labels form the mask (chips show per-label
  image counts); selecting none uses all labels
- **Exclude from mask** — pick labels whose regions are always painted black,
  even inside a trained area and even with Invert on. Useful for punching a
  located watermark out of the loss (see [Locating watermarks](detection.md#locating-watermarks)).
  Exclusion overrides inclusion: a label in both lists is excluded. An image with
  only excluded regions (no included detection) counts as "without detections" and
  follows the setting below.
- **Invert** — flip the mask to train the *background* and mask out the detections
- **Images without detections** — write a full-white mask (image trains normally,
  the count is reported in the job result) or skip the image entirely; the live
  export preview's will-export count reflects the skip policy

Mask folder per format (filenames match the exported image stems):

| Format | Images | Masks |
|---|---|---|
| Kohya | `output_dir/{repeats}_{concept}/` | `output_dir/{repeats}_{concept}_mask/` |
| AI Toolkit | `output_dir/{concept}/` | `output_dir/{concept}_mask/` |
| Plain folder | `output_dir/images/` | `output_dir/masks/` |

### Wiring the masks into your trainer

**kohya (sd-scripts)** — masked loss requires a TOML dataset config pointing
`conditioning_data_dir` at the mask folder, plus the `--masked_loss` flag:

```toml
[[datasets.subsets]]
image_dir = "C:/training/my_export/10_concept"
caption_extension = ".txt"
conditioning_data_dir = "C:/training/my_export/10_concept_mask"
num_repeats = 10
```

**ai-toolkit** — set `mask_path` on the dataset in your job config (and optionally
`mask_min_value`, e.g. `0.1`, so masked regions keep a small residual loss):

```yaml
datasets:
  - folder_path: /training/my_export/concept
    mask_path: /training/my_export/concept_mask
    mask_min_value: 0.1
```
