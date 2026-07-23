# Statistics Dashboard

Inspect the composition of a dataset — score distributions, caption lengths, tag frequencies, and detection coverage — before it feeds export or training.

Available from: the **Stats** sidebar item on any dataset page.

- 14+ interactive histograms: aesthetic, blur, noise, uniformity, color, saturation, watermark, megapixels, file size, aspect ratio, caption length, caption token distribution, style similarity, quality flags
  - **Caption token distribution** uses GPT-2 BPE tokenisation and highlights captions that exceed CLIP's 77-token truncation limit
- Editable histogram bucket edges — rebucketing runs entirely client-side against raw score arrays
- Top-500 tag frequency chart and tag co-occurrence matrix
- The **Summary** section includes a score guide table (metric, value range, flag threshold, detection method) and score coverage bars showing what percentage of images have been scored for each metric
- The **Licenses** panel (in the Summary section) breaks the dataset down by *effective* license — the image's own value, or the dataset default it inherits — as a table of License / Commercial use / Images / Share. A warning badge counts images with no license at either level. Click any row to open a filtered thumbnail grid of exactly those images, including the "No license" row. Only the largest license buckets are listed individually; a footnote counts the remainder (a scrape folder can produce a free-text license per image) and points you at the gallery's license filter to reach them → [details](provenance.md)
- The **Detections & Masks** section audits object-detection/mask data before it feeds export or training: overview cards (total detections, % of images with detections, distinct labels, bbox-only count, images with no detections), a label distribution, a per-model breakdown, and detection-score, mask-coverage, and detections-per-image histograms. Mask coverage is an approximate percentage of the image each mask covers, useful for spotting masks that are too small (<2%) or that swallowed the whole frame (>95%). Clicking a bar opens the same filtered thumbnail grid — e.g. jump straight to the suspicious masks. The section live-updates while a detection job runs
- Click any histogram bar or quality flag card to open a filtered thumbnail grid; clicking a thumbnail in that grid opens a full-resolution **lightbox** with prev/next navigation, a "View Details →" link to the image detail page, and a two-step delete button; a per-thumbnail × button on hover also provides inline delete
- A gear icon in the page header opens a settings drawer to toggle individual histogram panels on/off; visibility state is persisted per-browser
- All histograms and charts can be scoped to a specific subfolder via a dropdown in the page header
- **Export Stats CSV** — downloads a key-value CSV of all dataset statistics: summary fields, file-size percentiles, quality flag counts, score coverage, mean scores, and every histogram distribution; button is disabled while score data is still loading
- **Export Tags CSV** — downloads a tabular CSV (`tag,count`) of all tag frequencies (up to 500 tags), computed from caption text; disabled when no tags exist
