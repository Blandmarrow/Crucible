/**
 * The Runs page's start form as one object, and the converters between it and a run's
 * recipe (`schemas/run.RunRecipe`) in both directions (M6 §6). Start, Dry run and
 * saving a recipe all build the recipe through `formToRecipe`; loading a saved recipe,
 * a past run's settings or an imported file goes back through `recipeToForm`.
 *
 * The forward converters are lossy (a caption model id is two fields joined, the
 * delimiter is a list of parts joined), so each inverse is written beside its forward
 * one in `runForms.ts` and `runLoopForms.ts`. The e2e journey that saves, resets and
 * loads a recipe checks the round trip, comparing the request each form sends.
 */
import { DELIMITER_PRESETS } from "../../api/captioning";
import {
  RUN_STAGES, type RunCaptionSettings, type RunExportSettings, type RunGateSettings, type RunLoopSettings,
  type RunRecipe, type RunRewriteSettings, type RunStageKey,
} from "../../api/runs";
import {
  CAPTION_FORM_DEFAULTS, EXPORT_FORM_DEFAULTS, toCaptionSettings, toExportSettings,
  type CaptionForm, type ExportForm,
} from "./runForms";
import {
  GATE_FORM_DEFAULTS, LOOP_FORM_DEFAULTS, REWRITE_FORM_DEFAULTS, toGateSettings, toLoopSettings,
  toRewriteSettings, type GateForm, type LoopForm, type RewriteForm,
} from "./runLoopForms";

export const DEFAULT_STAGES: RunStageKey[] = [
  "prompts", "handoff_to_comfy", "generate", "handoff_to_local", "score", "free_caption_model",
];

/** A stage that only means something after another: sent only when that one is on too. */
export const NEEDS_STAGE: Partial<Record<RunStageKey, RunStageKey>> = {
  free_caption_model: "caption", free_rewrite_model: "rewrite", gate: "score", rewrite: "prompts",
};

/** Everything the start form edits. `stages` is a list, not a Set, so the whole form
 *  can be stored as it is (the remembered form, M6 §6). */
export interface RunFormState {
  planId: string | null;
  name: string;
  stages: RunStageKey[];
  subfolder: string;
  runAesthetic: boolean;
  aestheticModel: "laion" | "v2_5";
  caption: CaptionForm;
  exportForm: ExportForm;
  loop: LoopForm;
  gate: GateForm;
  rewrite: RewriteForm;
}

export const RUN_FORM_DEFAULTS: RunFormState = {
  planId: null,
  name: "",
  stages: DEFAULT_STAGES,
  subfolder: "",
  runAesthetic: true,
  aestheticModel: "laion",
  caption: CAPTION_FORM_DEFAULTS,
  exportForm: EXPORT_FORM_DEFAULTS,
  loop: LOOP_FORM_DEFAULTS,
  gate: GATE_FORM_DEFAULTS,
  rewrite: REWRITE_FORM_DEFAULTS,
};

// ── The remembered form (M6 §6) ─────────────────────────────────────────────

/** The plan and base folder are per dataset; everything else is one form for all. */
export type PerDataset = Record<string, { planId: string | null; subfolder: string }>;

/** What `/app-data/run-form` holds. The run name is never kept: a name is per run. */
export interface SavedRunForm {
  v: 1;
  form: Omit<RunFormState, "planId" | "name" | "subfolder">;
  byDataset: PerDataset;
}

/** The most datasets the remembered form keeps a plan and folder for. */
const MAX_REMEMBERED_DATASETS = 50;

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** A saved blob back into form state, section by section over the defaults, so a blob
 *  from an older build (a field it lacks, a stage it does not know) still loads. */
export function hydrateRunForm(saved: unknown): { form: RunFormState; byDataset: PerDataset } {
  if (!isObj(saved) || saved.v !== 1 || !isObj(saved.form)) return { form: RUN_FORM_DEFAULTS, byDataset: {} };
  const f = saved.form;
  const section = <T extends object>(defaults: T, v: unknown): T => (isObj(v) ? { ...defaults, ...v } : defaults);
  const known = new Set<string>(RUN_STAGES);
  const form: RunFormState = {
    ...RUN_FORM_DEFAULTS,
    stages: Array.isArray(f.stages) ? (f.stages.filter((s) => known.has(s as string)) as RunStageKey[]) : DEFAULT_STAGES,
    runAesthetic: typeof f.runAesthetic === "boolean" ? f.runAesthetic : RUN_FORM_DEFAULTS.runAesthetic,
    aestheticModel: f.aestheticModel === "v2_5" ? "v2_5" : "laion",
    caption: section(CAPTION_FORM_DEFAULTS, f.caption),
    exportForm: section(EXPORT_FORM_DEFAULTS, f.exportForm),
    loop: section(LOOP_FORM_DEFAULTS, f.loop),
    gate: section(GATE_FORM_DEFAULTS, f.gate),
    rewrite: section(REWRITE_FORM_DEFAULTS, f.rewrite),
  };
  const byDataset: PerDataset = {};
  if (isObj(saved.byDataset)) {
    for (const [id, e] of Object.entries(saved.byDataset)) {
      if (!isObj(e)) continue;
      byDataset[id] = {
        planId: typeof e.planId === "string" ? e.planId : null,
        subfolder: typeof e.subfolder === "string" ? e.subfolder : "",
      };
    }
  }
  return { form, byDataset };
}

/** The form as `/app-data/run-form` keeps it. The last-touched datasets are kept,
 *  in the order they were touched, when there are more than the cap. */
export function toSavedRunForm(form: RunFormState, byDataset: PerDataset): SavedRunForm {
  const entries = Object.entries(byDataset).slice(-MAX_REMEMBERED_DATASETS);
  return {
    v: 1,
    form: {
      stages: form.stages, runAesthetic: form.runAesthetic, aestheticModel: form.aestheticModel,
      caption: form.caption, exportForm: form.exportForm, loop: form.loop, gate: form.gate, rewrite: form.rewrite,
    },
    byDataset: Object.fromEntries(entries),
  };
}

/** What the page works out around the form and the recipe needs: the rewrite's
 *  provider (the form's, else the first one), the scenarios ticked for this plan, and
 *  whether a billed provider is in the recipe (so the paid-call cap is sent at all). */
export interface RecipeContext {
  rewriteProviderId: string;
  tickedScenarios: string[];
  paidCap: boolean;
}

export function formToRecipe(f: RunFormState, ctx: RecipeContext): RunRecipe {
  const on = new Set(f.stages);
  const rewriteOn = on.has("rewrite") && on.has("prompts");
  return {
    stages: RUN_STAGES.filter((s) => on.has(s) && (!NEEDS_STAGE[s] || on.has(NEEDS_STAGE[s]!))),
    generate: { subfolder: f.subfolder.trim(), set_caption: true },
    score: { run_aesthetic: f.runAesthetic, aesthetic_model: f.aestheticModel },
    caption: on.has("caption") ? toCaptionSettings(f.caption) : null,
    export: on.has("export") ? toExportSettings(f.exportForm) : null,
    gate: on.has("gate") && on.has("score") ? toGateSettings(f.gate, f.runAesthetic) : null,
    rewrite: rewriteOn ? toRewriteSettings({ ...f.rewrite, scenarios: ctx.tickedScenarios }, ctx.rewriteProviderId) : null,
    loop: toLoopSettings(f.loop, ctx.paidCap),
  };
}

// ── The inverses ─────────────────────────────────────────────────────────────

/** Split a delimiter string back into the parts `DelimiterControls` toggles. Preset
 *  characters become their own parts; anything else is kept as one custom part. */
export function delimiterParts(delimiter: string): string[] {
  const presets = new Set(DELIMITER_PRESETS.map((p) => p.value));
  const parts: string[] = [];
  let custom = "";
  for (const ch of delimiter) {
    if (presets.has(ch)) {
      if (custom) { parts.push(custom); custom = ""; }
      parts.push(ch);
    } else {
      custom += ch;
    }
  }
  if (custom) parts.push(custom);
  return parts;
}

export function fromCaptionSettings(s: RunCaptionSettings): CaptionForm {
  let base = s.model;
  let providerModel = "";
  if (s.model.startsWith("openai_compat:")) {
    const [, id, ...rest] = s.model.split(":");
    base = `openai_compat:${id ?? ""}`;
    // A provider's model name may itself hold colons.
    providerModel = rest.join(":");
  }
  return {
    ...CAPTION_FORM_DEFAULTS,
    base,
    providerModel,
    style: s.style,
    customPrompt: s.custom_prompt,
    wd14Threshold: s.wd14_threshold,
    targetWidth: s.target_width,
    targetHeight: s.target_height,
    overwrite: s.overwrite,
    delimiterMode: s.delimiter_mode,
    delimiterParts: delimiterParts(s.delimiter),
    stripRefusals: s.strip_refusals,
    stripThinking: s.strip_thinking,
    stripUnderscores: s.strip_underscores,
    stripHedges: s.strip_hedges,
    dedupeTags: s.dedupe_tags,
    saveBackup: s.save_backup,
    renameOnCaption: s.rename_on_caption,
    minAesthetic: s.min_aesthetic_score == null ? "" : String(s.min_aesthetic_score),
    excludeFlags: s.exclude_flags ?? [],
  };
}

/** The resize sizes the export fields offer as buttons; any other size is "Custom". */
export const EXPORT_RESIZE_PRESETS = [512, 768, 1024];

export function fromExportSettings(s: RunExportSettings): ExportForm {
  const preset = s.resize_to == null || EXPORT_RESIZE_PRESETS.includes(s.resize_to);
  return {
    ...EXPORT_FORM_DEFAULTS,
    format: s.kind,
    captionFmt: s.caption_format,
    outputDir: s.output_dir,
    nRepeats: s.n_repeats,
    conceptToken: s.concept_token,
    outputImgFmt: s.output_format,
    resizeTo: preset ? s.resize_to : null,
    customResize: !preset,
    customResizeVal: preset ? "" : String(s.resize_to),
    stripMetadata: s.strip_metadata,
    captionsOnly: s.captions_only,
    filterAesthetic: s.aesthetic_min != null,
    aestheticMin: s.aesthetic_min ?? EXPORT_FORM_DEFAULTS.aestheticMin,
    filterCaptioned: s.captioned_only,
    excludeFlags: s.exclude_flags,
    filterStyleSim: s.style_sim_min != null,
    styleSimMin: s.style_sim_min ?? EXPORT_FORM_DEFAULTS.styleSimMin,
  };
}

export function fromLoopSettings(s: RunLoopSettings | null | undefined): LoopForm {
  if (!s) return LOOP_FORM_DEFAULTS;
  return {
    on: true, maxPasses: s.max_passes, maxImages: s.max_images, maxMinutes: s.max_minutes,
    maxPaidCalls: s.max_paid_calls ?? LOOP_FORM_DEFAULTS.maxPaidCalls,
  };
}

export function fromGateSettings(s: RunGateSettings | null | undefined): GateForm {
  if (!s) return GATE_FORM_DEFAULTS;
  return {
    flags: s.exclude_flags, aestheticOn: s.aesthetic_min != null,
    aestheticMin: s.aesthetic_min ?? GATE_FORM_DEFAULTS.aestheticMin,
  };
}

export function fromRewriteSettings(s: RunRewriteSettings | null | undefined): RewriteForm {
  if (!s) return REWRITE_FORM_DEFAULTS;
  return {
    providerId: s.provider_id, modelName: s.model_name, batchSize: s.batch_size,
    temperature: s.temperature, scenarios: s.scenarios,
  };
}

/** What `recipeToForm` checks a recipe against: the providers set up here, and the
 *  current plan's rewrite-on scenarios (null while unknown). */
export interface LoadContext {
  providerIds: string[];
  rewriteScenarios: string[] | null;
}

/**
 * A stored recipe back into the form, keeping the form's plan and run name. What only
 * made sense elsewhere is cleared, and each clearing is a note for the user: a caption
 * or rewrite provider not set up here, and rewrite scenarios this plan does not have.
 */
export function recipeToForm(recipe: RunRecipe, keep: Pick<RunFormState, "planId" | "name">, ctx: LoadContext): {
  form: RunFormState; notes: string[];
} {
  const notes: string[] = [];
  const known = new Set(ctx.providerIds);
  let caption = recipe.caption ? fromCaptionSettings(recipe.caption) : CAPTION_FORM_DEFAULTS;
  if (caption.base.startsWith("openai_compat:") && !known.has(caption.base.slice("openai_compat:".length))) {
    caption = { ...caption, base: "", providerModel: "" };
    if (recipe.stages.includes("caption")) notes.push("Its caption provider is not set up here: choose a caption model.");
  }
  let rewrite = fromRewriteSettings(recipe.rewrite);
  if (rewrite.providerId && !known.has(rewrite.providerId)) {
    rewrite = { ...rewrite, providerId: "" };
    if (recipe.stages.includes("rewrite")) notes.push("Its rewrite provider is not set up here: choose one under Rewrite.");
  }
  if (ctx.rewriteScenarios && recipe.stages.includes("rewrite")) {
    const missing = rewrite.scenarios.filter((s) => !ctx.rewriteScenarios!.includes(s));
    if (missing.length) notes.push(`This plan has no rewrite-on scenario named ${missing.join(", ")}.`);
  }
  if (recipe.stages.includes("export") && !(recipe.export?.output_dir ?? "").trim()) {
    notes.push("Choose an export folder: recipes from another machine carry none.");
  }
  return {
    form: {
      ...keep,
      stages: [...recipe.stages],
      subfolder: recipe.generate?.subfolder ?? "",
      runAesthetic: recipe.score?.run_aesthetic ?? true,
      aestheticModel: recipe.score?.aesthetic_model ?? "laion",
      caption,
      exportForm: recipe.export ? fromExportSettings(recipe.export) : EXPORT_FORM_DEFAULTS,
      loop: fromLoopSettings(recipe.loop),
      gate: fromGateSettings(recipe.gate),
      rewrite,
    },
    notes,
  };
}
