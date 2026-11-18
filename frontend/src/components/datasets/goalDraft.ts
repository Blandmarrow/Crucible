/** The goal fields' draft and its parsing, shared by `GoalFields` and the two dataset dialogs. */

/** Bounds of `Dataset.target_resolution`, matching `backend/schemas/dataset.py`. */
export const TARGET_RESOLUTION_MIN = 64;
export const TARGET_RESOLUTION_MAX = 16384;
export const GOAL_MAX_LEN = 2000;

/** The dialogs' draft: the goal text and the target as typed ("" = no target). */
export interface GoalDraft {
  goal: string;
  target: string;
}

export function goalDraftOf(goal: string | undefined, target: number | null | undefined): GoalDraft {
  return { goal: goal ?? "", target: target == null ? "" : String(target) };
}

/** The draft's target as the API takes it: null for blank, a number when valid,
 *  undefined when what was typed is not a valid target (the dialog then refuses). */
export function parseTarget(target: string): number | null | undefined {
  const t = target.trim();
  if (t === "") return null;
  if (!/^\d+$/.test(t)) return undefined;
  const n = Number(t);
  return n >= TARGET_RESOLUTION_MIN && n <= TARGET_RESOLUTION_MAX ? n : undefined;
}
