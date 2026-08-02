/** The embedding modes behind `Image.style_similarity_score`, shared by every
 *  screen that offers or names one.
 *
 *  `constants/aestheticModels.ts` is the precedent and the reason is the same
 *  verbatim: two screens present this picker — the Score page's Style similarity
 *  panel and the gallery `SelectionToolbar`'s — and the copy had **already
 *  drifted** between them before this file existed. A third screen (the image
 *  detail Style match block) now names a mode read back from a stored run
 *  descriptor. Consume by `.map()` so drift becomes structurally impossible.
 *
 *  The copy states what the Phase-0 gate measured (`backend/scripts/style_gate_report.md`,
 *  summarised in `docs/dev/image-similarity.md`) rather than what the modes sound
 *  like. The previous wording — "CLIP for general images; DINOv2 for object-shape
 *  similarity" — implied DINOv2 was the upgrade; on the gate's 118 images CLIP
 *  separated best (AUC 0.9733 vs 0.9417) and `combined` tracked DINOv2 at
 *  Spearman ρ 0.9844, so it is a tilt on DINOv2 rather than a third opinion.
 */
export type StyleMode = "clip" | "dino" | "combined";

export interface StyleModeDef {
  value: StyleMode;
  label: string;
  desc: string;
}

/** In picker order. */
export const STYLE_MODES: StyleModeDef[] = [
  {
    value: "clip",
    label: "CLIP",
    desc: "Separates best of the three (AUC 0.97). Matches on lighting and palette — the closest of the three to \"looks like these references\".",
  },
  {
    value: "dino",
    label: "DINOv2",
    desc: "Spends a wider range but separates less well (AUC 0.94), and drifts toward subject and framing rather than palette.",
  },
  {
    value: "combined",
    label: "CLIP + DINOv2",
    desc: "0.38 × CLIP + 0.62 × DINOv2. Tracks DINOv2 closely (ρ 0.98) rather than giving an independent third opinion.",
  },
];

/** Shown under the picker on both screens. The last clause is the one that
 *  matters most: it is why a stored score needs its run descriptor to be read. */
export const STYLE_MODE_NOTE =
  "All modes require embeddings computed first. Scores from different modes are not comparable — a run overwrites the previous one for every image it covers.";

/** Shown under the per-layer picker. The gate's per-layer sweep found layers 1–8
 *  compress every image into 0.90–0.99, which is an ordering with no cut point
 *  in it. */
export const DINO_LAYER_NOTE =
  "Each transformer block captures increasingly abstract features. Usable spread only appears at layers 10–12: on layers 1–8 every image scores 0.90–0.99, so there is no threshold to cut on. \"All layers\" scores each layer independently and stores the breakdown for the image detail view.";

/** How a stored `embedding_type` reads on screen.
 *
 *  Also maps the two all-layers values, which a descriptor can carry but the
 *  picker cannot select — they come from the layer dropdown's "All layers"
 *  option, not from the mode buttons. An unknown value renders verbatim rather
 *  than being swallowed, so a run made by a mode this build does not know about
 *  still names itself. */
export function styleModeLabel(mode: string): string {
  if (mode === "dino_all_layers") return "DINOv2 (all layers)";
  if (mode === "combined_all_layers") return "CLIP + DINOv2 (all layers)";
  return STYLE_MODES.find((m) => m.value === mode)?.label ?? mode;
}
