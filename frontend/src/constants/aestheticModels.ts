/** The producers of `Image.aesthetic_score`, shared by every screen that reads
 *  the `aesthetic_model` marker stored beside it.
 *
 *  Three screens name these models — the Score images picker, the duplicates
 *  *Keep best* refusal, and the Export page's mixed-model advisory — and a
 *  fourth (Stats) renders the per-model coverage breakdown. A copy per screen
 *  would drift, and the copies that drift are the ones in warnings nobody reads
 *  until something has already been deleted.
 *
 *  The set is **open**: a future learned head writes `head:{uuid}` into the same
 *  column, which is why every consumer falls back to the raw marker rather than
 *  treating an unknown one as absent.
 */
export type AestheticModel = "laion" | "v2_5";

export interface AestheticModelDef {
  value: AestheticModel;
  label: string;
  desc: string;
  vram: string;
}

/** In picker order. Their scales are **not** comparable — both land in 1–10, and
 *  that is all they share. */
export const AESTHETIC_MODELS: AestheticModelDef[] = [
  {
    value: "laion",
    label: "LAION (CLIP ViT-L/14)",
    desc: "sac+logos+ava1 MLP over CLIP. Shares its backbone with watermark detection and CLIP embeddings.",
    vram: "GPU · 2.1 GB",
  },
  {
    value: "v2_5",
    label: "Aesthetic Predictor V2.5 (SigLIP)",
    desc: "SigLIP-so400m backbone. Rates photographic and illustrated images more evenly than LAION.",
    vram: "GPU · 2.0 GB",
  },
];

/** How a stored marker reads on screen. An unknown marker renders verbatim
 *  rather than being swallowed, so a mixed-model warning can always name what it
 *  found. */
export function aestheticModelLabel(marker: string): string {
  return AESTHETIC_MODELS.find((m) => m.value === marker)?.label ?? marker;
}
