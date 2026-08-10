export type DetectionModelFamily = "florence" | "sam" | "nudenet";

/**
 * The five models `POST /detection/run` accepts, in picker order.
 *
 * These were hardcoded `<option>` lists in three components, and had drifted in
 * both directions a copied list drifts: the same model carried two different
 * labels, and the gallery selection toolbar had lost NudeNet entirely, so it
 * could not be run on a selection at all. Following `aestheticModels.ts`.
 *
 * Frontend constants rather than an endpoint on purpose — unlike the captioning
 * models, there is no backend list to own. `_ALLOWED_MODELS` in
 * `routers/detection.py` is the authority on what is accepted; keep in step.
 */
export const DETECTION_MODELS: { value: string; label: string }[] = [
  { value: "florence2_large", label: "Florence-2 Large" },
  { value: "florence2_promptgen", label: "Florence-2 PromptGen" },
  { value: "nudenet", label: "NudeNet (body-part detection)" },
  { value: "sam2", label: "SAM 2.1 + Grounding DINO (segmentation)" },
  { value: "sam3", label: "SAM 3 (text-prompt segmentation)" },
];

/**
 * Group a detection model id into its behavioural family.
 *
 * The Detect modals reset the task / prompt / use-captions inputs only when the
 * *family* changes — switching Florence-2 Large ↔ PromptGen (same family) keeps
 * the user's task and prompt, while Florence → SAM or → NudeNet resets them.
 */
export function detectionModelFamily(model: string): DetectionModelFamily {
  if (model === "sam2" || model === "sam3") return "sam";
  if (model === "nudenet") return "nudenet";
  return "florence";
}
