import client from "./client";

/** Coerce a raw cell/pin input to the value stored in a row/pin.
 *
 * Non-numeric fields keep the raw string. Numeric fields become a real number
 * ONLY when it round-trips exactly and safely; large or high-precision integers
 * (e.g. ComfyUI seeds beyond 2^53) are kept as the raw string so no digits are
 * lost — the backend `_coerce` converts a numeric string to the template's type
 * losslessly (Python `int()`/`float()`).
 */
export function coerceCellValue(raw: string, numeric: boolean): string | number {
  if (!numeric) return raw;
  const trimmed = raw.trim();
  const n = Number(trimmed);
  if (trimmed !== "" && Number.isFinite(n) && String(n) === trimmed) return n;
  return raw;
}

export interface PinnedParam {
  node_id: string;
  input: string;
  alias: string;
  is_prompt: boolean;
  /** true → queue-table column; false → run default only (no column). */
  per_row: boolean;
  /** Run-default override applied to every row without a row value; null = template. */
  value: string | number | boolean | null;
  /** Integer params: applied when a row has no value. null behaves as "fixed". */
  int_mode: "fixed" | "random" | "increment" | null;
}

export interface ComfyPlanSummary {
  id: string;
  dataset_id: string;
  name: string;
  row_count: number;
  status_counts: Record<string, number>;
}

export interface ComfyPlan {
  id: string;
  dataset_id: string;
  name: string;
  workflow_json: Record<string, { class_type: string; inputs: Record<string, unknown>; _meta?: { title?: string } }>;
  pinned_params: PinnedParam[];
  /** Import images from these nodes' outputs (any type, incl. previews); [] = all SaveImage outputs. */
  output_node_ids: string[];
  /** True = stamp imported images as self-created (synthetic/ComfyUI); false = inherit the dataset's provenance defaults. */
  output_is_synthetic: boolean;
  created_at: string;
  updated_at: string;
}

export interface ComfyRow {
  id: string;
  plan_id: string;
  sort_order: number;
  values: Record<string, unknown>;
  status: "pending" | "running" | "completed" | "failed";
  error_msg: string | null;
  image_id: string | null;
  image_ids: string[];
  prompt_id: string | null;
}

export interface PlanPrompt {
  row_id: string;
  prompt: string;
  status: ComfyRow["status"];
}

export interface ComfyRunParams {
  plan_id: string;
  row_ids?: string[];
  subfolder?: string;
  set_caption?: boolean;
  label?: string;
}

export interface LibraryPrompt {
  id: string;
  category: string;
  text: string;
  created_at: string;
}

export interface WorkflowFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_at: string;
  format: "api" | "ui" | "invalid";
}

export interface CanvasWorkflowResponse {
  workflow: ComfyPlan["workflow_json"];
  /** "bridge" = live canvas via CrucibleBridge; "history" = last-queued prompt fallback. */
  source: "bridge" | "history";
  name: string | null;
  node_count: number;
  age_seconds: number | null;
}

export const comfyApi = {
  ping: (url?: string) =>
    client.get<{ ok: boolean; error?: string }>("/comfy/ping", { params: url ? { url } : {} }).then((r) => r.data),

  listPlans: (datasetId: string) =>
    client.get<ComfyPlanSummary[]>("/comfy/plans", { params: { dataset_id: datasetId } }).then((r) => r.data),
  createPlan: (data: { dataset_id: string; name: string; workflow_json?: object; pinned_params?: PinnedParam[] }) =>
    client.post<ComfyPlan>("/comfy/plans", data).then((r) => r.data),
  getPlan: (planId: string) =>
    client.get<ComfyPlan>(`/comfy/plans/${planId}`).then((r) => r.data),
  updatePlan: (planId: string, data: Partial<Pick<ComfyPlan, "name" | "workflow_json" | "pinned_params" | "output_node_ids" | "output_is_synthetic">>) =>
    client.patch<ComfyPlan>(`/comfy/plans/${planId}`, data).then((r) => r.data),
  deletePlan: (planId: string) => client.delete(`/comfy/plans/${planId}`),

  listRows: (planId: string) =>
    client.get<ComfyRow[]>(`/comfy/plans/${planId}/rows`).then((r) => r.data),
  /** Effective prompt text per row, for reusing prompts across plans/datasets. */
  listPlanPrompts: (planId: string) =>
    client.get<{ prompts: PlanPrompt[] }>(`/comfy/plans/${planId}/prompts`).then((r) => r.data.prompts),
  createRow: (planId: string, values: Record<string, unknown> = {}) =>
    client.post<ComfyRow>(`/comfy/plans/${planId}/rows`, { values }).then((r) => r.data),
  bulkAddRows: (planId: string, lines: string[]) =>
    client.post<{ created: number }>(`/comfy/plans/${planId}/rows/bulk`, { lines }).then((r) => r.data),
  updateRow: (rowId: string, data: { values?: Record<string, unknown>; sort_order?: number }) =>
    client.patch<ComfyRow>(`/comfy/rows/${rowId}`, data).then((r) => r.data),
  deleteRows: (planId: string, rowIds: string[]) =>
    client.post<{ deleted: number }>(`/comfy/plans/${planId}/rows/delete`, { row_ids: rowIds }).then((r) => r.data),
  resetRows: (planId: string, rowIds?: string[]) =>
    client.post<{ reset: number }>(`/comfy/plans/${planId}/rows/reset`, { row_ids: rowIds }).then((r) => r.data),
  setValueAllRows: (planId: string, alias: string, value: string | number | boolean | null) =>
    client.post<{ updated: number }>(`/comfy/plans/${planId}/rows/set-value`, { alias, value }).then((r) => r.data),

  /** One batch, synchronously. `signal` lets the caller abandon a call that can run
   *  for the provider's full timeout (120 s) — without it the UI has to sit on it. */
  generatePrompts: (body: {
    provider_id: string; model_name?: string; system_instructions?: string; instruction: string;
    batch_size?: number; existing?: string[]; temperature?: number;
  }, signal?: AbortSignal) =>
    client.post<{ prompts: string[]; model: string }>("/comfy/generate-prompts", body, { signal }).then((r) => r.data),
  /** Durable "generate until N": a background job that inserts rows per batch.
   *  `target_count` is absolute (until the plan holds N prompts), and the server
   *  derives the diverge-from context from the plan — hence no `existing`. */
  generatePromptsJob: (planId: string, body: {
    provider_id: string; model_name?: string; system_instructions?: string; instruction: string;
    batch_size?: number; temperature?: number; target_count: number; use_existing_context?: boolean;
  }) =>
    client.post<{ job_id: string; total: number }>(`/comfy/plans/${planId}/generate-prompts`, body).then((r) => r.data),
  bulkEditRows: (planId: string, body: {
    operation: "prepend" | "append" | "remove" | "find_replace";
    text: string; replacement?: string; use_regex?: boolean; row_ids?: string[];
  }) =>
    client.post<{ affected: number; skipped: number }>(`/comfy/plans/${planId}/rows/bulk-edit`, body).then((r) => r.data),

  /** Global prompt library (not dataset-scoped), grouped by free-text category. */
  libraryList: () =>
    client.get<{ prompts: LibraryPrompt[] }>("/comfy/library").then((r) => r.data.prompts),
  libraryAdd: (category: string, prompts: string[]) =>
    client.post<{ created: number; skipped: number }>("/comfy/library", { category, prompts }).then((r) => r.data),
  /** Prompts whose text already exists in the target category are deleted (merged). */
  libraryMove: (ids: string[], category: string) =>
    client.post<{ moved: number; merged: number }>("/comfy/library/move", { ids, category }).then((r) => r.data),
  libraryDelete: (ids: string[]) =>
    client.post<{ deleted: number }>("/comfy/library/delete", { ids }).then((r) => r.data),

  listWorkflowFiles: (dir?: string) =>
    client.get<{ dir: string; files: WorkflowFile[] }>("/comfy/workflow-files", { params: dir ? { dir } : {} }).then((r) => r.data),
  loadWorkflowFile: (path: string) =>
    client.get<{ workflow: ComfyPlan["workflow_json"] }>("/comfy/workflow-file", { params: { path } }).then((r) => r.data),
  /** Current ComfyUI workflow: CrucibleBridge canvas snapshot, or last-queued history entry. */
  canvasWorkflow: () =>
    client.get<CanvasWorkflowResponse>("/comfy/canvas-workflow").then((r) => r.data),

  run: (params: ComfyRunParams) =>
    client.post<{ job_id: string; total: number }>("/comfy/run", params).then((r) => r.data),
};
