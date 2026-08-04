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
 *  **No mode description quotes an AUC**, and that is a rule rather than an
 *  omission. The copy here twice said what one measurement found and twice had to
 *  be taken back: "CLIP for general images; DINOv2 for object-shape similarity"
 *  implied DINOv2 was the upgrade, and its replacement — "CLIP separates best of
 *  the three (AUC 0.97)" — was true of exactly one reference set. On a second
 *  reference cluster drawn from *the same 118 images* that mode scored 0.6144, and
 *  0.7399 on photographs. A single number presented as a property of a mode is a
 *  claim the feature cannot keep; what each mode *attends to* is stable, so that
 *  is what the copy says.
 *
 *  For the honest range: ~0.98 AUC is the ceiling for sorting across media
 *  (anime screencaps from painterly illustration), ~0.85 is realistic for style
 *  matching inside one medium. See `docs/dev/style-similarity.md`.
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
    desc: "Matches on lighting and palette — the closest of the three to \"looks like these references\". Strong on some reference sets and weak on others, more so than the DINOv2 modes.",
  },
  {
    value: "dino",
    label: "DINOv2",
    desc: "Spends a wider numeric range, and leans toward subject and framing rather than palette. Steadier than CLIP across different reference sets.",
  },
  {
    value: "combined",
    label: "CLIP + DINOv2",
    desc: "0.30 × CLIP + 0.70 × DINOv2, on layer 9 by default. The most reliable of the three across varied references — CLIP and DINOv2 are the pair that genuinely disagree, so blending them is worth more than either alone.",
  },
];

/** Shown under the picker on both screens. The last clause is the one that
 *  matters most: it is why a stored score needs its run descriptor to be read. */
export const STYLE_MODE_NOTE =
  "All modes require embeddings computed first. Scores from different modes, layers or blend weights are not comparable — a run overwrites the previous one for every image it covers, and the image detail page names what produced the score you are looking at.";

/** Shown under the per-layer picker — visible copy on both screens, never a
 *  `title=` (see SelectionToolbar).
 *
 *  This replaces a claim that turned out to be wrong in the direction that
 *  mattered: it said layers 1–8 were unusable because they compress every image
 *  into 0.90–0.99. The compression is real, but a narrow band with clean ordering
 *  thresholds perfectly well — the two are separate properties that happened to
 *  coincide on one dataset. A sweep across twelve reference configurations found
 *  the *middle* of the stack separates best and the last layer worst, on every
 *  model and every dataset tested. Steering users away from layer 9 would be
 *  steering them away from the default. */
export const DINO_LAYER_NOTE =
  "Each transformer block captures increasingly abstract features. The middle of the stack separates styles best — layer 9 is the default and the last layer is measurably the weakest, so raw score range is a poor guide to which layer to use. \"All layers\" scores each layer independently and stores the breakdown for the image detail view.";

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

/** The DINOv2 layer both pickers default to, and the layer the two `*_all_layers`
 *  modes report as the headline `style_similarity_score`.
 *
 *  Must equal `DEFAULT_DINO_LAYER` in `backend/ml/similarity_scorer.py`. Nothing at
 *  runtime couples them, so `backend/tests/test_similarity_scorer.py` reads this
 *  file and asserts the two agree — the same shape of guard as
 *  `test_scores_stale.py`'s AST check. */
export const DEFAULT_DINO_LAYER = 9;

/** What the layer dropdown can hold.
 *
 *  **Three values, not two.** `"final"` is the post-layernorm `dino_embedding`
 *  column; `12` is `dino_layer_embeddings`' layer 12, which is
 *  `hidden_states[12]` — **pre**-layernorm. They are different vectors and score
 *  differently (0.9417 vs 0.9611 on the gate's sweep), and until this type existed
 *  both pickers normalised "Layer 12" to `null`, which the backend reads as the
 *  final embedding — so the app could never score DINOv2's actual layer 12 at all.
 *  `"all"` selects the corresponding `*_all_layers` mode instead. */
export type DinoLayerChoice = number | "final" | "all";

/** Coerce a persisted or unknown value into a `DinoLayerChoice`.
 *
 *  Needed at every load boundary because `loadPersisted` shallow-merges onto the
 *  defaults: a blob written before this type existed carries `dinoLayer: null`,
 *  which survives the merge intact and would render an empty `<select>`. `null`
 *  becomes `"final"` rather than the new default — it is exactly what that stored
 *  value used to score, and silently moving a deliberate pick to another layer is
 *  worse than keeping it. Anything unrecognised falls back to the default. */
export function normalizeDinoLayer(value: unknown): DinoLayerChoice {
  if (value === "all" || value === "final") return value;
  if (value === null) return "final";
  if (typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 12) return value;
  return DEFAULT_DINO_LAYER;
}

/** What a mode+layer combination is missing, or null when it is fully covered. */
export interface EmbeddingGap {
  /** Images in scope carrying everything this mode reads. */
  covered: number;
  total: number;
  /** What is missing, phrased for a sentence: "…needs {needs}". */
  needs: string;
}

/** Does the current picker selection have the embeddings it reads? `null` when it does.
 *
 *  Style similarity is the one workflow whose prerequisite is another run, and
 *  every branch answers 400 when its column is empty — so without this the only
 *  way to find out is to press the button and read an error.
 *
 *  For the two-column `combined` modes this takes the **minimum** of the two
 *  counts, which is an upper bound on the number of rows carrying both rather
 *  than the exact figure. That is exact where it has to be — `covered === 0` iff
 *  the intersection is empty, which is the case that disables the button — and
 *  approximate only in the partial warning, which is advisory. Computing the true
 *  intersection would cost a second query for a number nothing acts on. */
export function describeEmbeddingGap(
  coverage: { total: number; clip: number; dino: number; dino_layers: number } | undefined,
  mode: StyleMode,
  layer: DinoLayerChoice,
): EmbeddingGap | null {
  if (!coverage || coverage.total === 0) return null;
  const perLayer = layer !== "final";
  const clip = { count: coverage.clip, name: "CLIP embeddings" };
  const dino = perLayer
    ? { count: coverage.dino_layers, name: "per-layer DINOv2 embeddings" }
    : { count: coverage.dino, name: "DINOv2 embeddings" };

  const parts =
    mode === "clip" ? [clip] : mode === "dino" ? [dino] : [clip, dino];
  const covered = Math.min(...parts.map((p) => p.count));
  if (covered >= coverage.total) return null;
  return {
    covered,
    total: coverage.total,
    needs: parts.map((p) => p.name).join(" and "),
  };
}
