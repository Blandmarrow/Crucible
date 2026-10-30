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
  rejected: string[];
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

/** A rejected preset is often rejected for its name being too long, so shorten it. */
function presetLabel(preset: unknown): string {
  const name = (preset as { name?: unknown } | null)?.name;
  if (typeof name !== "string" || !name.trim()) return "(unnamed)";
  return name.length > 40 ? `${name.slice(0, 40)}…` : name;
}

/** Names what the server refused, because those entries are copied nowhere else. Returns
 *  the names, which the marker keeps so the later release that deletes the old keys can
 *  leave this browser's alone. */
function reportRejected(body: LegacyImportBody, report: LegacyImportReport): string[] {
  const names = (report.presets?.rejected ?? []).map((i) => presetLabel(body.presets?.[i]));
  if (names.length > 0) {
    toast.error(`These saved presets could not be copied to the app and stay in this browser only: ${names.join(", ")}`, { duration: 15_000 });
  }
  return names;
}

async function runImport(): Promise<void> {
  const marker = loadPersisted<ImportMarker>(LEGACY_IMPORT_MARKER_KEY, { done: [], rejected: [] });
  const done = new Set(marker.done);
  const body: LegacyImportBody = {};
  const finished: Section[] = [];
  const rejected = [...marker.rejected];

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
    rejected.push(...reportRejected(body, report));
  }

  if (finished.length > 0) {
    savePersisted(LEGACY_IMPORT_MARKER_KEY, { done: [...done, ...finished], rejected });
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
    // Said out loud, or the pages reading that data would make it look lost.
    toast.error("Couldn't copy your saved data from this browser to the app. It's still safe here, and will be tried again next time you load the page.", { duration: 15_000 });
  });
  return started;
}
