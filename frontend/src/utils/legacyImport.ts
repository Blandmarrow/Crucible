import toast from "react-hot-toast";
import { appDataApi, type LegacyImportBody, type LegacyImportReport } from "../api/appData";
import { LEGACY_IMPORT_MARKER_KEY } from "../constants/storage";
import { loadPersisted, savePersisted } from "./persistentState";

/** Written by the zustand `persist` store that held presets before they moved to the
 *  server, as `{ state: { presets }, version }`. */
const LEGACY_PRESETS_KEY = "caption-prompt-presets";

type Section = keyof LegacyImportBody;

interface ImportMarker {
  done: Section[];
}

function readLegacyPresets(): unknown[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(LEGACY_PRESETS_KEY) ?? "null");
    const presets = parsed?.state?.presets;
    if (!Array.isArray(presets)) return [];
    // The old client-side ids mean nothing to the server.
    return presets.map((p) => {
      if (typeof p !== "object" || p === null) return p;
      const copy = { ...p };
      delete copy.id;
      return copy;
    });
  } catch {
    return [];
  }
}

/** Names what the server refused, because those entries are copied nowhere else. */
function reportRejected(report: LegacyImportReport): void {
  const rejected = report.presets?.rejected.length ?? 0;
  if (rejected > 0) {
    toast.error(`${rejected} saved preset(s) could not be copied to the app and stay in this browser only.`);
  }
}

async function runImport(): Promise<void> {
  const marker = loadPersisted<ImportMarker>(LEGACY_IMPORT_MARKER_KEY, { done: [] });
  const done = new Set(marker.done);
  const body: LegacyImportBody = {};
  const finished: Section[] = [];

  if (!done.has("presets")) {
    const presets = readLegacyPresets();
    if (presets.length > 0) body.presets = presets;
    else finished.push("presets");
  }

  if (Object.keys(body).length > 0) {
    const report = await appDataApi.legacyImport(body);
    // The SPA catch-all can answer with 200 HTML, which must not read as success.
    if (typeof report !== "object" || report === null) throw new Error("Unexpected import response");
    if (body.presets && report.presets) finished.push("presets");
    reportRejected(report);
  }

  if (finished.length > 0) {
    savePersisted(LEGACY_IMPORT_MARKER_KEY, { ...marker, done: [...done, ...finished] });
  }
}

let started: Promise<void> | null = null;

/** Copies what this browser still holds to the server, once per load. Never rejects: a
 *  failure leaves its section unmarked, so the next load tries again. Legacy keys are
 *  left in place for one release, as a copy to roll back to. Every query for this data
 *  awaits this first, so a page can't read the server before the copy arrives. */
export function runLegacyImport(): Promise<void> {
  started ??= runImport().catch((err) => {
    console.warn("Copying saved data from this browser failed; will retry on next load.", err);
  });
  return started;
}
