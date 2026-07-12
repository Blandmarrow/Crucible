import client from "./client";

export interface PinnedParam {
  node_id: string;
  input: string;
  alias: string;
  is_prompt: boolean;
}

export interface ComfyPlanSummary {
  id: string;
  dataset_id: string;
  name: string;
  seed_mode: "fixed" | "random" | "increment";
  row_count: number;
  status_counts: Record<string, number>;
}

export interface ComfyPlan {
  id: string;
  dataset_id: string;
  name: string;
  workflow_json: Record<string, { class_type: string; inputs: Record<string, unknown>; _meta?: { title?: string } }>;
  pinned_params: PinnedParam[];
  seed_mode: "fixed" | "random" | "increment";
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

export const comfyApi = {
  ping: (url?: string) =>
    client.get<{ ok: boolean; error?: string }>("/comfy/ping", { params: url ? { url } : {} }).then((r) => r.data),

  listPlans: (datasetId: string) =>
    client.get<ComfyPlanSummary[]>("/comfy/plans", { params: { dataset_id: datasetId } }).then((r) => r.data),
  createPlan: (data: { dataset_id: string; name: string; workflow_json?: object; pinned_params?: PinnedParam[]; seed_mode?: string }) =>
    client.post<ComfyPlan>("/comfy/plans", data).then((r) => r.data),
  getPlan: (planId: string) =>
    client.get<ComfyPlan>(`/comfy/plans/${planId}`).then((r) => r.data),
  updatePlan: (planId: string, data: Partial<Pick<ComfyPlan, "name" | "workflow_json" | "pinned_params" | "seed_mode">>) =>
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

  run: (params: ComfyRunParams) =>
    client.post<{ job_id: string; total: number }>("/comfy/run", params).then((r) => r.data),
};
