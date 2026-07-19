export type DetectionModelFamily = "florence" | "sam" | "nudenet";

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
