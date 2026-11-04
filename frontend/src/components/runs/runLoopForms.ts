import type { RunGateSettings, RunLoopSettings, RunRewriteSettings } from "../../api/runs";

/**
 * What the Runs page's M6 §5 sections edit, in the page's own names, and the helpers
 * that turn each into what the run stores (`schemas/run.LoopSettings`,
 * `GateSettings`, `RewriteSettings`). Kept apart from the components, as
 * `runForms.ts` is, so the component file exports components only.
 */

export interface LoopForm {
  on: boolean;
  maxPasses: number;
  maxImages: number;
  maxMinutes: number;
  maxPaidCalls: number;
}

export const LOOP_FORM_DEFAULTS: LoopForm = { on: false, maxPasses: 5, maxImages: 500, maxMinutes: 240, maxPaidCalls: 1000 };

/** `paidCap`: whether a billed provider is in the recipe, so the cap is sent at all. */
export function toLoopSettings(f: LoopForm, paidCap: boolean): RunLoopSettings | null {
  if (!f.on) return null;
  return {
    max_passes: f.maxPasses, max_images: f.maxImages, max_minutes: f.maxMinutes,
    max_paid_calls: paidCap ? f.maxPaidCalls : null,
  };
}

export interface GateForm {
  flags: string[];
  aestheticOn: boolean;
  aestheticMin: number;
}

export const GATE_FORM_DEFAULTS: GateForm = {
  flags: ["is_blurry", "is_noisy", "is_uniform", "is_duplicate"], aestheticOn: false, aestheticMin: 5,
};

export function toGateSettings(f: GateForm, runAesthetic: boolean): RunGateSettings {
  return { exclude_flags: f.flags, aesthetic_min: f.aestheticOn && runAesthetic ? f.aestheticMin : null };
}

export interface RewriteForm {
  providerId: string;
  modelName: string;
  batchSize: number;
  temperature: number;
  scenarios: string[];
}

export const REWRITE_FORM_DEFAULTS: RewriteForm = { providerId: "", modelName: "", batchSize: 5, temperature: 0.7, scenarios: [] };

export function toRewriteSettings(f: RewriteForm, providerId: string): RunRewriteSettings {
  return {
    provider_id: providerId, model_name: f.modelName.trim(), batch_size: f.batchSize,
    temperature: f.temperature, scenarios: f.scenarios,
  };
}
