/** What the DINOv2 layer pickers and `DinoLayerBreakdown` say about each layer.
 *
 *  **Every label must trace back to a measurement**, and that is a rule rather
 *  than a style. The strings this file used to hold ("Object parts", "Scene
 *  composition", "Abstract semantics") were a gloss on the generic
 *  low-level→semantic depth intuition, with nothing measured behind any of them.
 *  On screen beside a default that *was* measured, a reader cannot tell which is
 *  evidence — one review distrusted the layer-7 default because a label called
 *  layer 9 "Scene composition", and the crossed runs later showed the label
 *  pointed at the wrong end of the stack: layer 12 is the framing-locked one.
 *
 *  So each label is a **depth word plus an observed property**. The depth words
 *  (Low, Middle, Deep, Last) are positions in the stack, not claims about what a
 *  layer encodes. The properties, and where they were measured:
 *
 *  - **Low (1–4): scores bunch close together.** DINOv2's low layers put every
 *    image within a few hundredths of the rest (0.90–0.99, through layer 8), and
 *    1–4 rank worst of all. `backend/scripts/style_gate_report.md` § DINOv2
 *    per-layer sweep; `docs/dev/quality/style-evaluation.md`.
 *  - **The look/subject lean (5–12).** The crossed runs put the crossover at
 *    about layer 7: below it a shared style outranks a shared subject, above it
 *    the subject wins and keeps winning to the end of the stack, with layer 12
 *    furthest in. `docs/dev/quality/style-evaluation.md` § Which layer, and why
 *    it moved twice. Layer 8 was not measured on its own; its label follows from
 *    that monotone trend.
 *
 *  Nothing here says "default": that is `DEFAULT_DINO_LAYER` in `styleModes.ts`,
 *  and a label tied to it would go stale the day the default moves. Layer 7's
 *  label states the crossover, which is a measurement rather than a setting.
 */
export const DINO_LAYER_LABELS: Record<string, string> = {
  "1":  "Low: scores bunch close together",
  "2":  "Low: scores bunch close together",
  "3":  "Low: scores bunch close together",
  "4":  "Low: scores bunch close together",
  "5":  "Middle: leans toward the look",
  "6":  "Middle: leans toward the look",
  "7":  "Middle: balances look and subject",
  "8":  "Middle: leans toward subject",
  "9":  "Deep: leans toward subject",
  "10": "Deep: leans toward subject",
  "11": "Deep: leans toward subject",
  // Keeps "pre-layernorm": the layer picker offers a separate "Final embedding"
  // option for the post-layernorm `dino_embedding` column, and two options both
  // reading as the last layer would be exactly the confusion the three-valued
  // picker exists to remove. This is `hidden_states[12]`.
  "12": "Last block, pre-layernorm: leans most toward subject",
};
