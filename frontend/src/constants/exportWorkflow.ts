/**
 * The Export page's remembered settings: their shapes and defaults. Kept out of
 * `ExportPage.tsx` so another page can read them (the Runs page's "Fill from the
 * Export page") without importing a page module, which would pull ExportPage into
 * that page's lazy chunk. `workflow` is stored under `EXPORT_WORKFLOW_KEY`, and the
 * filters per dataset under `datasetScopedKey(EXPORT_FILTERS_PREFIX, datasetId)`.
 */
export type Format = "kohya" | "aitoolkit" | "plain";
export type CaptionFmt = "txt" | "caption" | "jsonl";
export type ResizeTo = number | null;
export type MaskMissing = "white" | "skip";

export const FORMAT_LABELS: Record<Format, string> = {
  kohya: "kohya",
  aitoolkit: "ai-toolkit",
  plain: "plain folder",
};

export interface ExportWorkflow {
  format: Format;
  captionFmt: CaptionFmt;
  outputDir: string;
  nRepeats: number;
  conceptToken: string;
  outputImgFmt: string;
  resizeTo: ResizeTo;
  customResize: boolean;
  customResizeVal: string;
  stripMetadata: boolean;
  captionsOnly: boolean;
  exportMasks: boolean;
  maskInvert: boolean;
  maskMissing: MaskMissing;
}

export const EXPORT_WORKFLOW_DEFAULTS: ExportWorkflow = {
  format: "kohya",
  captionFmt: "txt",
  outputDir: "",
  nRepeats: 10,
  conceptToken: "concept",
  outputImgFmt: "original",
  resizeTo: null,
  customResize: false,
  customResizeVal: "",
  stripMetadata: false,
  captionsOnly: false,
  exportMasks: false,
  maskInvert: false,
  maskMissing: "white",
};

export interface ExportFilters {
  filterAesthetic: boolean;
  aestheticMin: number;
  filterCaptioned: boolean;
  excludeFlags: string[];
  filterStyleSim: boolean;
  styleSimMin: number;
  subfolderFilterActive: boolean;
  selectedSubfolders: string[];
  maskLabels: string[];
  maskExcludeLabels: string[];
  licenseFilter: string[];
  commercialOnly: boolean;
  excludeUnlicensed: boolean;
  excludeNoDerivatives: boolean;
  labelFilter: string[];
  labelMatch: "any" | "all";
  labelMissing: boolean;
}

export const EXPORT_FILTERS_DEFAULTS: ExportFilters = {
  filterAesthetic: false,
  aestheticMin: 5.0,
  filterCaptioned: true,
  excludeFlags: ["has_watermark", "gate_rejected"],
  filterStyleSim: false,
  styleSimMin: 0.5,
  subfolderFilterActive: false,
  selectedSubfolders: [],
  maskLabels: [],
  maskExcludeLabels: [],
  licenseFilter: [],
  commercialOnly: false,
  excludeUnlicensed: false,
  excludeNoDerivatives: false,
  labelFilter: [],
  labelMatch: "any",
  labelMissing: false,
};
