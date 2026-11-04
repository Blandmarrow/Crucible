/**
 * The Runs page's caption and export forms: their shapes, defaults, and the converters
 * to what a run stores (`schemas/run.CaptionSettings` and `ExportSettings`). Kept apart
 * from the field components so those files export components only (fast refresh).
 */
import type { DelimiterMode } from "../../api/captioning";
import type { RunCaptionSettings, RunExportSettings } from "../../api/runs";
import { CAPTIONING_WORKFLOW_DEFAULTS } from "../../constants/captioningWorkflow";
import {
  EXPORT_FILTERS_DEFAULTS, EXPORT_WORKFLOW_DEFAULTS, type CaptionFmt, type Format,
} from "../../constants/exportWorkflow";

/** What the caption fields edit: the Captioning page's own shape, so "Fill from the
 *  Captioning page" is a field-for-field copy. `toCaptionSettings` turns it into
 *  what the run stores. */
export interface CaptionForm {
  base: string;
  providerModel: string;
  style: string;
  customPrompt: string;
  wd14Threshold: number;
  targetWidth: number | null;
  targetHeight: number | null;
  overwrite: boolean;
  delimiterMode: DelimiterMode;
  delimiterParts: string[];
  stripRefusals: boolean;
  stripThinking: boolean;
  stripUnderscores: boolean;
  stripHedges: boolean;
  dedupeTags: boolean;
  saveBackup: boolean;
  renameOnCaption: boolean;
  minAesthetic: string;
  excludeFlags: string[];
}

const D = CAPTIONING_WORKFLOW_DEFAULTS;
export const CAPTION_FORM_DEFAULTS: CaptionForm = {
  base: "",
  providerModel: "",
  style: D.style,
  customPrompt: D.customPrompt,
  wd14Threshold: D.wd14Threshold,
  targetWidth: D.targetWidth,
  targetHeight: D.targetHeight,
  // On, unlike the Captioning page's "uncaptioned only": a ComfyUI run has already
  // saved each prompt as its image's caption, so off would caption nothing.
  overwrite: true,
  delimiterMode: D.delimiterMode,
  delimiterParts: D.delimiterParts,
  stripRefusals: D.stripRefusals,
  stripThinking: D.stripThinking,
  stripUnderscores: D.stripUnderscores,
  stripHedges: D.stripHedges,
  dedupeTags: D.dedupeTags,
  saveBackup: D.saveBackup,
  renameOnCaption: D.renameOnCaption,
  minAesthetic: "",
  excludeFlags: [],
};

/** The model id the job takes: a provider's base id gets its chosen model appended. */
export function captionModelId(f: CaptionForm): string {
  return f.base.startsWith("openai_compat:") && f.providerModel.trim() ? `${f.base}:${f.providerModel.trim()}` : f.base;
}

export function toCaptionSettings(f: CaptionForm): RunCaptionSettings {
  const isWd14 = f.base.startsWith("wd14:");
  const min = parseFloat(f.minAesthetic);
  return {
    model: captionModelId(f),
    style: isWd14 ? "tags" : f.style,
    custom_prompt: f.customPrompt,
    overwrite: f.overwrite,
    // Both or neither, as the Captioning page sends them.
    target_width: f.targetWidth && f.targetHeight ? f.targetWidth : null,
    target_height: f.targetWidth && f.targetHeight ? f.targetHeight : null,
    strip_refusals: f.stripRefusals,
    strip_thinking: f.stripThinking,
    strip_underscores: f.stripUnderscores,
    strip_hedges: f.stripHedges,
    dedupe_tags: f.dedupeTags,
    save_backup: f.saveBackup,
    rename_on_caption: f.renameOnCaption,
    wd14_threshold: f.wd14Threshold,
    delimiter_mode: f.delimiterMode,
    delimiter: f.delimiterParts.join(""),
    min_aesthetic_score: Number.isFinite(min) ? min : null,
    exclude_flags: f.excludeFlags.length > 0 ? f.excludeFlags : null,
  };
}

/** What the export fields edit: the Export page's own names, so "Fill from the Export
 *  page" is a field-for-field copy. `toExportSettings` turns it into what the run stores. */
export interface ExportForm {
  format: Format;
  captionFmt: CaptionFmt;
  outputDir: string;
  nRepeats: number;
  conceptToken: string;
  outputImgFmt: string;
  resizeTo: number | null;
  customResize: boolean;
  customResizeVal: string;
  stripMetadata: boolean;
  captionsOnly: boolean;
  filterAesthetic: boolean;
  aestheticMin: number;
  filterCaptioned: boolean;
  excludeFlags: string[];
  filterStyleSim: boolean;
  styleSimMin: number;
}

const W = EXPORT_WORKFLOW_DEFAULTS;
const F = EXPORT_FILTERS_DEFAULTS;
export const EXPORT_FORM_DEFAULTS: ExportForm = {
  format: W.format,
  captionFmt: W.captionFmt,
  outputDir: "",
  nRepeats: W.nRepeats,
  conceptToken: W.conceptToken,
  outputImgFmt: W.outputImgFmt,
  resizeTo: W.resizeTo,
  customResize: W.customResize,
  customResizeVal: W.customResizeVal,
  stripMetadata: W.stripMetadata,
  captionsOnly: W.captionsOnly,
  filterAesthetic: F.filterAesthetic,
  aestheticMin: F.aestheticMin,
  filterCaptioned: F.filterCaptioned,
  // The Export page's default, plus duplicates: the run's score stage has just flagged them.
  excludeFlags: [...F.excludeFlags, "is_duplicate"],
  filterStyleSim: F.filterStyleSim,
  styleSimMin: F.styleSimMin,
};

/** The server's bounds on `resize_to` (RunExportSettings): outside them the start 422s. */
export const EXPORT_RESIZE_MIN = 1;
export const EXPORT_RESIZE_MAX = 16384;
export const clampExportResize = (n: number) => Math.min(EXPORT_RESIZE_MAX, Math.max(EXPORT_RESIZE_MIN, Math.round(n)));

/** The custom resize the field shows and the run sends: one parse for both, so the
 *  box never reads a value the start would not use. A blank, non-numeric or
 *  out-of-range stored value (a recipe or the Export page's form can carry one) reads
 *  as the 1024 default, never as a 422 or a dropped resize. */
export const DEFAULT_CUSTOM_RESIZE = 1024;
export function customResizeValue(f: ExportForm): number {
  const n = parseInt(f.customResizeVal, 10);
  return Number.isFinite(n) && n >= EXPORT_RESIZE_MIN && n <= EXPORT_RESIZE_MAX ? n : DEFAULT_CUSTOM_RESIZE;
}

export function exportResizeTo(f: ExportForm): number | null {
  return f.customResize ? customResizeValue(f) : f.resizeTo;
}

export function toExportSettings(f: ExportForm): RunExportSettings {
  return {
    kind: f.format,
    output_dir: f.outputDir.trim(),
    n_repeats: f.nRepeats,
    // The Export page sends its one concept field as both.
    concept_token: f.conceptToken,
    concept_name: f.conceptToken,
    caption_format: f.captionFmt,
    output_format: f.outputImgFmt,
    jpeg_quality: 95,
    resize_to: f.captionsOnly ? null : exportResizeTo(f),
    aesthetic_min: f.filterAesthetic ? f.aestheticMin : null,
    captioned_only: f.filterCaptioned,
    exclude_flags: f.excludeFlags,
    style_sim_min: f.filterStyleSim ? f.styleSimMin : null,
    strip_metadata: !f.captionsOnly && f.stripMetadata,
    captions_only: f.captionsOnly,
  };
}
