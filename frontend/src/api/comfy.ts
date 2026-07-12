import client from "./client";

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

export interface ComfyRunParams {
  plan_id: string;
  row_ids?: string[];
  subfolder?: string;
  set_caption?: boolean;
  label?: string;
}

export interface WorkflowFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_at: string;
  format: "api" | "ui" | "invalid";
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
  updatePlan: (planId: string, data: Partial<Pick<ComfyPlan, "name" | "workflow_json" | "pinned_params" | "output_node_ids">>) =>
    client.patch<ComfyPlan>(`/comfy/plans/${planId}`, data).then((r) => r.data),
  deletePlan: (planId: string) => client.delete(`/comfy/plans/${planId}`),

  listRows: (planId: string) =>
    client.get<ComfyRow[]>(`/comfy/plans/${planId}/rows`).then((r) => r.data),
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

  generatePrompts: (body: {
    provider_id: string; model_name?: string; system_instructions?: string; instruction: string;
    batch_size?: number; existing?: string[]; temperature?: number;
  }) =>
    client.post<{ prompts: string[]; model: string }>("/comfy/generate-prompts", body).then((r) => r.data),
  bulkEditRows: (planId: string, body: {
    operation: "prepend" | "append" | "remove" | "find_replace";
    text: string; replacement?: string; use_regex?: boolean; row_ids?: string[];
  }) =>
    client.post<{ affected: number; skipped: number }>(`/comfy/plans/${planId}/rows/bulk-edit`, body).then((r) => r.data),

  listWorkflowFiles: (dir?: string) =>
    client.get<{ dir: string; files: WorkflowFile[] }>("/comfy/workflow-files", { params: dir ? { dir } : {} }).then((r) => r.data),
  loadWorkflowFile: (path: string) =>
    client.get<{ workflow: ComfyPlan["workflow_json"] }>("/comfy/workflow-file", { params: { path } }).then((r) => r.data),

  run: (params: ComfyRunParams) =>
    client.post<{ job_id: string; total: number }>("/comfy/run", params).then((r) => r.data),
};
