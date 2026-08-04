import client from "./client";

export interface DuplicateImage {
  id: string;
  filename: string;
  aesthetic_score: number | null;
  /** Which model produced `aesthetic_score`: "laion" | "v2_5" (or a future
   *  `head:{uuid}`). Non-null exactly when the score is. Two markers inside one
   *  group mean two non-comparable scales, and *Keep best* — which deletes —
   *  must refuse rather than rank across them. */
  aesthetic_model: string | null;
  updated_at: string;
  created_at: string;
  /** True for the one image in the group the scan kept — the group's first
   *  member, and the only one no default action deletes. */
  kept: boolean;
  /** Frame lineage, annotated by `get_duplicates` rather than by the scan.
   *  Frames from one video (held cels, recycled footage, a locked-off shot) land
   *  inside the pHash threshold legitimately, so a group where every member
   *  shares one non-null `source_video_id` deserves saying out loud before
   *  anything is deleted. Null on any image that did not come from a video, and
   *  `source_video_id` also goes null when the source video is deleted. */
  source_video_id: string | null;
  source_timestamp_ms: number | null;
  source_shot_index: number | null;
  /** Resolved filename of `source_video_id`; null when the id is null or the
   *  video row is gone. */
  source_video_name: string | null;
}

export type DuplicateGroup = DuplicateImage[];

/** What produced the style scores currently stored in a dataset.
 *
 *  One per dataset, overwritten by every successful run — so it describes the
 *  values in `style_similarity_score` right now, not a history. Null on a dataset
 *  scored before run tracking existed, and on a clone (`duplicate_dataset` does
 *  not carry it, because the reference ids would point at the source dataset's
 *  images). */
export interface StyleRunDescriptor {
  /** "clip" | "dino" | "combined" | "dino_all_layers" | "combined_all_layers" —
   *  read through `styleModeLabel`, which renders an unknown value verbatim. */
  embedding_type: string;
  dino_layer: number | null;
  /** The blend a `combined` / `combined_all_layers` run used. Null on a mode that
   *  does not blend — and also on a `combined` run predating the columns, which
   *  `embedding_type` is what distinguishes. Recorded per run because the shipped
   *  weights have moved (0.38/0.62 → 0.30/0.70) and two scores are only comparable
   *  when they were blended the same way. */
  clip_weight: number | null;
  dino_weight: number | null;
  /** Capped at 64 server-side; `reference_count` is the true number. Deliberately
   *  not kept in sync with `images`, so an id here may no longer resolve. */
  reference_image_ids: string[];
  reference_count: number;
  external_reference_count: number;
  scored_count: number;
  skipped_count: number;
  /** Non-null when the run covered a selection rather than the whole dataset, in
   *  which case the rest of the dataset still carries scores from an earlier run
   *  this descriptor does not describe. */
  scoped_image_count: number | null;
  updated_at: string | null;
}

/** The dataset's style-score distribution — what makes one raw cosine readable.
 *
 *  `quantiles` holds 21 breakpoints, every 5th percentile, ascending, with q0 and
 *  q100 exactly min and max. It is empty when nothing is scored, and may repeat
 *  values when fewer images are scored than there are breakpoints; `percentileOf`
 *  handles both. Dataset-wide and never subfolder-scoped, so one image reads the
 *  same in every pane. */
export interface StyleDistribution {
  scored: number;
  total: number;
  quantiles: number[];
  quantile_step: number;
  run: StyleRunDescriptor | null;
}

export const qualityApi = {
  score: (params: {
    dataset_id: string;
    subfolder?: string;
    image_ids?: string[];
    run_aesthetic: boolean;
    /** Which model writes `aesthetic_score`. Omitted = "laion", matching the
     *  server default; an unknown value is a 422. */
    aesthetic_model?: "laion" | "v2_5";
    /** Re-score only rows a *different* model already scored. Never-scored rows
     *  are excluded — plain scoring covers those. */
    only_mismatched?: boolean;
    run_technical: boolean;
    run_watermark?: boolean;
    run_embeddings?: boolean;
    run_dino?: boolean;
    run_dino_layers?: boolean;
    run_nsfw?: boolean;
    label?: string;
  }) =>
    // `job_id` is null with a `message` when the scope matched no images — the
    // ordinary answer for a re-score offer whose mismatch count raced to zero,
    // not an error.
    client
      .post<{ job_id: string | null; total?: number; message?: string }>("/quality/score", params)
      .then((r) => r.data),

  /** Per-model aesthetic coverage within one subfolder scope. `by_model` sums to
   *  `scored`: every stored score carries a marker. */
  aestheticCoverage: (dataset_id: string, subfolder?: string) =>
    client
      .get<{ scored: number; unscored: number; by_model: Record<string, number> }>(
        `/quality/aesthetic-coverage/${dataset_id}`,
        { params: subfolder ? { subfolder } : undefined },
      )
      .then((r) => r.data),

  /** Groups are led by the image the scan kept (`kept: true`); the rest are the
   *  removable copies, in `created_at` order. See `get_duplicates`. */
  duplicates: (dataset_id: string) =>
    client
      .get<{ groups: DuplicateGroup[] }>(`/quality/duplicates/${dataset_id}`)
      .then((r) => r.data),

  resolveDuplicates: (keep_ids: string[], delete_ids: string[]) =>
    client.post("/quality/duplicates/resolve", { keep_ids, delete_ids }),

  embedReferences: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return client
      .post<{ embeddings: string[] }>("/quality/embed-references", form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },

  styleSimilarity: (params: {
    dataset_id: string;
    image_ids?: string[];
    reference_image_ids: string[];
    reference_embeddings?: string[];
    embedding_type?: "clip" | "dino" | "combined" | "dino_all_layers" | "combined_all_layers";
    dino_layer?: number;
  }) =>
    client.post<{ updated: number; skipped: number }>("/quality/style-similarity", params).then((r) => r.data),

  /** The dataset's style-score distribution plus its run descriptor. An unknown
   *  dataset returns an empty payload rather than 404 — the caller is a gallery
   *  card, not a navigation. */
  styleDistribution: (dataset_id: string) =>
    client.get<StyleDistribution>(`/quality/style-similarity/${dataset_id}`).then((r) => r.data),
};
