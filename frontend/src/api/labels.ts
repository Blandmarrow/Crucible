import client from "./client";

export interface Label {
  id: string;
  name: string;
  color: string;
  hotkey: string | null;
  sort_order: number;
  created_at: string;
  usage_count: number;
}

export interface LabelCreate {
  name: string;
  color?: string;
  hotkey?: string | null;
}

export interface LabelUpdate {
  name?: string;
  color?: string;
  /** Explicit `null` clears the hotkey — the PATCH uses `exclude_unset`, not
   * `exclude_none`, precisely so this works. */
  hotkey?: string | null;
}

export interface LabelAssignBody {
  image_ids: string[];
  /** Label ids to attach. */
  add?: string[];
  /** Label ids to detach. */
  remove?: string[];
}

export interface LabelAssignResult {
  images: number;
  added: number;
  removed: number;
}

export const labelsApi = {
  list: () => client.get<Label[]>("/labels/").then((r) => r.data),
  counts: (datasetId: string) =>
    client
      .get<{ counts: Record<string, number> }>("/labels/counts", {
        params: { dataset_id: datasetId },
      })
      .then((r) => r.data.counts),
  create: (body: LabelCreate) => client.post<Label>("/labels/", body).then((r) => r.data),
  update: (id: string, body: LabelUpdate) =>
    client.patch<Label>(`/labels/${id}`, body).then((r) => r.data),
  remove: (id: string) => client.delete(`/labels/${id}`),
  reorder: (orderedIds: string[]) =>
    client.post("/labels/reorder", { ordered_ids: orderedIds }).then((r) => r.data),
  assign: (body: LabelAssignBody) =>
    client.post<LabelAssignResult>("/labels/assign", body).then((r) => r.data),
};
