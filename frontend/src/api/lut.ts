import client from "./client";

export interface LutModelInfo {
  name: string;
  path: string;
  format: string;
}

export interface LutRunRequest {
  dataset_id: string;
  image_ids?: string[];
  lut_path: string;
  intensity: number;
  replace: boolean;
  subfolder?: string;
}

export const lutApi = {
  models: () =>
    client.get<LutModelInfo[]>("/lut/models").then((r) => r.data),

  run: (req: LutRunRequest) =>
    client
      .post<{ job_id: string; total: number }>("/lut/run", req)
      .then((r) => r.data),
};
