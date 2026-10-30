import client from "./client";

export interface PromptPreset {
  id: string;
  name: string;
  model: string;
  style: string;
  prompt: string;
}

export type PromptPresetCreate = Omit<PromptPreset, "id">;

export interface LegacyImportBody {
  presets?: unknown[];
}

export interface LegacyImportReport {
  presets?: { created: number; skipped: number; rejected: number[] };
}

/** The one-time carry-over waits at most this long. The shared axios client sets no
 *  timeout, and every query for this data waits on the import. */
const LEGACY_IMPORT_TIMEOUT_MS = 15_000;

export const appDataApi = {
  listPresets: () =>
    client.get<PromptPreset[]>("/app-data/presets").then((r) => {
      // A server older than the frontend answers with the SPA's 200 HTML, and every
      // surface maps over this.
      if (!Array.isArray(r.data)) throw new Error("Unexpected presets response");
      return r.data;
    }),
  createPreset: (body: PromptPresetCreate) =>
    client.post<PromptPreset>("/app-data/presets", body).then((r) => r.data),
  deletePreset: (id: string) =>
    client.delete(`/app-data/presets/${id}`).then((r) => r.data),
  legacyImport: (body: LegacyImportBody) =>
    client
      .post<LegacyImportReport>("/app-data/legacy-import", body, { timeout: LEGACY_IMPORT_TIMEOUT_MS })
      .then((r) => r.data),
};
