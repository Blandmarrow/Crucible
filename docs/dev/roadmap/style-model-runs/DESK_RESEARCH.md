# Style-model desk research — 2026-11-03

**Status: transient.** What the web says about each candidate, gathered before any download.
It feeds the shortlist in `PREREGISTRATION.md`, next to this file. Numbers are as published;
none was reproduced here.

## Shortlist (chosen by the owner, 2026-11-03)

| Model | Trained for | Weights | Licence | Size, input, dim | Published style numbers |
|---|---|---|---|---|---|
| **Kaloscope 3.0 preview** ([card](https://huggingface.co/heathcliff01/Kaloscope3.0-preview)) | Danbooru artist retrieval: 6.57M images, 44,129 artists. Positives are the same artist within 365 upload days, in a different "work family". | `v1-artist-classifier/model.safetensors`, 625 MB, self-contained (no separate DINOv3 download). Pinned revision `5e4bfa2`, 2026-10-06. | Weights: Meta DINOv3 License (no commercial clause; bans ITAR/military uses; redistribution keeps the terms). Code (`Chenkin-x/kaloscope-dinov3`): GPL-3.0. | DINOv3 ViT-B/16, 512 px (short side 512, centre 512), CLS + mean pooled, 256-d projector output. | Its own dev split: 95.2% top-1, 83.3% cross-content. No CLIP or DINO baseline. |
| **CSD ViT-L** ([card](https://huggingface.co/tomg-group-umd/CSD-ViT-L), [paper](https://arxiv.org/abs/2404.01292)) | Style vs content disentangling, contrastive, on LAION-Styles. | HF, not gated; file format to confirm. The README warns that the uploaded weights differ from the paper's numbers. | Code MIT; weights CC-BY-4.0 (HF card). | CLIP ViT-L/14 backbone, 224 px, 768-d style head. | WikiArt mAP@1: CSD 64.6, CLIP-L 59.4, DINO-B/16 44.0. DomainNet mAP@1: 78.3 / 74.0 / 69.4. |
| **PE-Core L14-336** ([card](https://huggingface.co/facebook/PE-Core-L14-336)) | Image–text contrastive (Meta Perception Encoder, 2025). | HF; a timm/open_clip port exists (`timm/PE-Core-L-14-336`). | Apache-2.0. | ViT-L/14 at 336 px. | None found. A hypothesis: a stronger image–text model in place of CLIP's share of the blend. |

## Considered and not tested this round

- **IntroStyle** ([paper](https://arxiv.org/abs/2412.14432)): channel mean and variance of the
  SD 2.1 UNet's up-block 1 at t = 25, 2-Wasserstein distance. WikiArt mAP@1 0.887, against
  CSD 0.646 and CLIP-L 0.595 — the strongest published number, from one group. Dropped by
  the owner: it runs every image through a diffusion UNet, the code has no licence file, and
  `stabilityai/stable-diffusion-2-1` returned 401 on 2026-11-03.
- **Gram matrices of VGG features**: WikiArt mAP@1 25.9 in the CSD paper, below DINO. Not
  worth a slot.
- **Gram of ViT patch tokens**: no published comparison found.
- **SigLIP 2, OpenCLIP H/bigG, DFN, MetaCLIP 2, AIMv2, EVA-CLIP**: no published style numbers.
  PE-Core was kept as the one image–text candidate. MetaCLIP 2 is CC BY-NC per a mirror; DFN
  and AIMv2 use Apple's own licences.
- **Kaloscope v1 (LSNet)**: Apache-2.0, 768-d, but its upstream code needs Triton.
- **AugustLabs/Author_ID**: ConvNeXt-tiny ONNX, Apache-2.0, Danbooru artists, no published
  quality. A cheap fallback if Kaloscope cannot be loaded.
- **Anima Style Embedding v2**: needs Anima VAE latents plus DINOv3 features, not raw pixels;
  non-commercial weights.
- **ALADIN / ALADIN-ViT**: no public weights found.
- **Fgdfgfthgr Anime Style Embedder, dudcjs2779 anime-style-art-embedding**: too little
  documented to test.

## The leakage problem, and how the test set avoids it

Kaloscope was trained on Danbooru, and the data cutoff is not stated. `evaluation.json` names
the release checkpoint `sigmoid_year_v1_20261002`, so training ended about 2026-10-02. A
Danbooru-built test set may therefore hold its training images. "Posts after the cutoff"
leaves only a month of uploads, too few for 30 per artist. The way out is the artist list it
ships (`class_mapping.csv`, 44,129 names): artists **missing** from it are ones it never
learned. That is also the app's real case, where a reference style can be anyone's. See
`PREREGISTRATION.md` for the rule.
