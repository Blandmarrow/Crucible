import toast from "react-hot-toast";
import { appDataApi, type LegacyImportBody, type LegacyImportReport } from "../api/appData";
import { COMFY_GEN_DEFAULTS, comfyApi, type ComfyGenSettings } from "../api/comfy";
import { LEGACY_IMPORT_MARKER_KEY } from "../constants/storage";
import { loadPersisted, savePersisted } from "./persistentState";

/** Written by the zustand `persist` store that held presets before they moved to the
 *  server, as `{ state: { presets }, version }`. */
const LEGACY_PRESETS_KEY = "caption-prompt-presets";

/** The modal's old per-plan settings. `comfy-genprompts-job-*` shares the prefix but
 *  holds a job id and stays in the browser, hence the lookahead. */
const LEGACY_COMFY_KEY = /^comfy-genprompts-(?!job-)(.+)$/;

type Section = keyof LegacyImportBody;

/** A browser's copy that lost to one already on the server, kept until the user chooses. */
export interface LegacyConflict {
  section: "comfy_gen_settings";
  id: string;
  value: ComfyGenSettings;
}

interface ImportMarker {
  done: Section[];
  rejected: string[];
  conflicts: LegacyConflict[];
}

const EMPTY_MARKER: ImportMarker = { done: [], rejected: [], conflicts: [] };

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

function readLegacyComfySettings(): Record<string, ComfyGenSettings> {
  const entries: Record<string, ComfyGenSettings> = {};
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    const planId = key?.match(LEGACY_COMFY_KEY)?.[1];
    if (!key || !planId) continue;
    try {
      const old = JSON.parse(localStorage.getItem(key) ?? "null");
      if (typeof old !== "object" || old === null) continue;
      const d = COMFY_GEN_DEFAULTS;
      const value: ComfyGenSettings = {
        instructions: old.instructions ?? d.instructions,
        provider_id: old.providerId ?? d.provider_id,
        model: old.model ?? d.model,
        batch_size: old.batchSize ?? d.batch_size,
        temperature: old.temperature ?? d.temperature,
        target_count: old.targetCount ?? d.target_count,
      };
      // The old modal wrote this on every open, so an untouched copy says nothing, and
      // uploading it could beat another browser's real instructions to the server.
      if (JSON.stringify(value) !== JSON.stringify(d)) entries[planId] = value;
    } catch {
      /* unreadable entry: nothing to carry over */
    }
  }
  return entries;
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
  const presets = (report.presets?.rejected ?? []).map((i) => presetLabel(body.presets?.[i]));
  if (presets.length > 0) {
    toast.error(`These saved presets could not be copied to the app and stay in this browser only: ${presets.join(", ")}`, { duration: 15_000 });
  }
  const plans = Object.entries(report.comfy_gen_settings ?? {})
    .filter(([, status]) => status === "rejected")
    .map(([planId]) => `Generate-prompts settings for plan ${planId}`);
  if (plans.length > 0) {
    toast.error(`Generate-prompts settings for ${plans.length} plan(s) could not be copied to the app and stay in this browser only.`, { duration: 15_000 });
  }
  return [...presets, ...plans];
}

async function runImport(): Promise<void> {
  const marker = loadPersisted(LEGACY_IMPORT_MARKER_KEY, EMPTY_MARKER);
  const done = new Set(marker.done);
  const body: LegacyImportBody = {};
  const finished: Section[] = [];
  const rejected = [...marker.rejected];
  const conflicts = [...marker.conflicts];

  if (!done.has("presets")) {
    const presets = readLegacyPresets();
    if (presets.length > 0) body.presets = presets;
    else finished.push("presets");
  }
  if (!done.has("comfy_gen_settings")) {
    const settings = readLegacyComfySettings();
    if (Object.keys(settings).length > 0) body.comfy_gen_settings = settings;
    else finished.push("comfy_gen_settings");
  }

  if (Object.keys(body).length > 0) {
    const report = await appDataApi.legacyImport(body);
    // The SPA catch-all can answer with 200 HTML, which must not read as success.
    if (typeof report !== "object" || report === null) throw new Error("Unexpected import response");
    for (const section of Object.keys(body) as Section[]) {
      if (report[section]) finished.push(section);
    }
    for (const [id, status] of Object.entries(report.comfy_gen_settings ?? {})) {
      if (status === "conflict" && body.comfy_gen_settings) {
        conflicts.push({ section: "comfy_gen_settings", id, value: body.comfy_gen_settings[id] });
      }
    }
    rejected.push(...reportRejected(body, report));
  }

  if (finished.length > 0) {
    savePersisted(LEGACY_IMPORT_MARKER_KEY, { done: [...done, ...finished], rejected, conflicts });
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

export function readLegacyConflicts(): LegacyConflict[] {
  return loadPersisted(LEGACY_IMPORT_MARKER_KEY, EMPTY_MARKER).conflicts;
}

/** Forget a conflict the user has decided, or one that no longer means anything. */
export function dismissLegacyConflict(conflict: LegacyConflict): void {
  const marker = loadPersisted(LEGACY_IMPORT_MARKER_KEY, EMPTY_MARKER);
  savePersisted(LEGACY_IMPORT_MARKER_KEY, {
    ...marker,
    conflicts: marker.conflicts.filter((c) => !(c.section === conflict.section && c.id === conflict.id)),
  });
}

/** Replace the server's copy with this browser's. */
export async function applyLegacyConflict(conflict: LegacyConflict): Promise<void> {
  await comfyApi.putGenSettings(conflict.id, conflict.value);
}
