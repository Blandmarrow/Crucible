import type { DelimiterMode } from "../api/captioning";

// Apart from CaptioningPage, a lazily loaded chunk, because the one-time browser import
// in the main bundle compares against these defaults.

export type Scope = "uncaptioned" | "selected" | "all";

export interface StepConfig {
  id: string;
  model: string;
  style: string;
  customPrompt: string;
  wd14Threshold: number;
  providerModelInput: string;
  delimiterMode: DelimiterMode;
  delimiterParts: string[];
  usePreviousCaption: boolean;
  stripRefusals: boolean;
  stripThinking: boolean;
  stripUnderscores: boolean;
  stripHedges: boolean;
  dedupeTags: boolean;
  targetWidth: number | null;
  targetHeight: number | null;
}

export interface CaptioningWorkflow {
  selectedModel: string;
  providerModelInput: string;
  style: string;
  customPrompt: string;
  wd14Threshold: number;
  targetWidth: number | null;
  targetHeight: number | null;
  scope: Scope;
  delimiterMode: DelimiterMode;
  delimiterParts: string[];
  stripRefusals: boolean;
  stripThinking: boolean;
  stripUnderscores: boolean;
  stripHedges: boolean;
  dedupeTags: boolean;
  saveBackup: boolean;
  renameOnCaption: boolean;
  detectTask: string;
  detectPrompt: string;
  detectUseCaptions: boolean;
  detectOverwrite: boolean;
  additionalSteps: StepConfig[];
}

export const CAPTIONING_WORKFLOW_DEFAULTS: CaptioningWorkflow = {
  selectedModel: "",
  providerModelInput: "",
  style: "detailed",
  customPrompt: "",
  wd14Threshold: 0.35,
  targetWidth: null,
  targetHeight: null,
  scope: "uncaptioned",
  delimiterMode: "overwrite",
  delimiterParts: [",", " "],
  stripRefusals: true,
  stripThinking: false,
  stripUnderscores: false,
  stripHedges: false,
  dedupeTags: false,
  saveBackup: false,
  renameOnCaption: false,
  detectTask: "<OD>",
  detectPrompt: "",
  detectUseCaptions: false,
  detectOverwrite: true,
  additionalSteps: [],
};
