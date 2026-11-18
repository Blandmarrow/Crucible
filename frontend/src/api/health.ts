import client from "./client";
import type { ImageListParams } from "./images";

export type Severity = "error" | "warn" | "info";

/** The images a finding is about: a closed subset of `ImageListParams`, sent to
 *  `GET /images/` as it is. Mirrors `backend/schemas/health.py::FilterSpec`. */
export type FilterSpec = Partial<
  Pick<
    ImageListParams,
    | "captioned"
    | "search"
    | "caption_tokens_min"
    | "caption_tokens_max"
    | "short_side_max"
    | "quality_flag"
    | "score_field"
    | "score_is_null"
    | "max_score_lt"
    | "scores_stale"
    | "license_missing"
  >
>;

export interface Dismissal {
  note_id: string;
  reason: string;
  permanent: boolean;
  dismissed_at: string;
  count_then: number | null;
}

export interface Finding {
  id: string;
  severity: Severity;
  title: string;
  /** What `count` and `total` count. */
  unit: "images" | "tags" | "groups";
  count: number;
  total: number;
  numbers: Record<string, unknown>;
  /** null when no single filter selects the affected images. */
  filter_spec: FilterSpec | null;
  /** A dataset page (pane PageType, or "datasets") and the button text. */
  fix: { page: string; label: string } | null;
  fingerprint: Record<string, unknown>;
  /** The count at the branch head's snapshot; null when there is no baseline. */
  was: number | null;
  dismissal: Dismissal | null;
  resurfaced: boolean;
}

export interface HealthReport {
  dataset_id: string;
  findings: Finding[];
  dismissed: Finding[];
  resolved: { id: string; severity: Severity; was: number }[];
  summary: Record<Severity, number>;
  baseline: { version_id: string; version_name: string | null; created_at: string; missing: boolean } | null;
  computed_at: string;
}

export const healthApi = {
  get: (datasetId: string) =>
    client.get<HealthReport>(`/datasets/${datasetId}/health`).then((r) => r.data),
  dismiss: (datasetId: string, findingId: string, reason: string, permanent: boolean) =>
    client
      .post(`/datasets/${datasetId}/health/dismiss`, { finding_id: findingId, reason, permanent })
      .then((r) => r.data),
};

/** `FilterSpec` with its unset keys dropped, ready to spread into `ImageListParams`. */
export function filterSpecParams(spec: FilterSpec): FilterSpec {
  return Object.fromEntries(
    Object.entries(spec).filter(([, v]) => v !== null && v !== undefined),
  ) as FilterSpec;
}
