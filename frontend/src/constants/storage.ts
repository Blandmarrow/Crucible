export const CONFIRM_DEFAULT_KEY = "confirm-danger-default-action";
export const BRANCH_SNAPSHOT_KEY = "branch-snapshot-behavior"; // "ask" | "auto"
export const VERSIONS_BRANCH_KEY = "versions-branch"; // prefix; append `-${datasetId}` for the full key
export const GALLERY_PAGE_SIZE_KEY = "gallery-page-size"; // 25 | 50 | 100 | 200
export const SUBFOLDER_RENAME_KEY = "subfolder-auto-rename"; // "on" | "off"
export const GALLERY_CHECKBOX_SIZE_KEY = "gallery-checkbox-size"; // px, 14..32
export const VIDEO_STRIP_COLLAPSED_KEY = "gallery-videos-collapsed"; // prefix; append `-${datasetId}` for the full key
export const DECLARED_CATEGORIES_KEY = "crucible-declared-categories"; // string[]

import { SORT_OPTIONS } from "./galleryOptions";

// Gallery default filters (applied when no session state exists for a dataset)
export const GALLERY_DEFAULT_SORT_KEY    = "gallery-default-sort";    // number (index into SORT_OPTIONS)
export const GALLERY_DEFAULT_CAPTION_KEY = "gallery-default-caption"; // "all" | "captioned" | "uncaptioned"
export const GALLERY_DEFAULT_QUALITY_KEY = "gallery-default-quality"; // "" | "is_blurry" | "is_noisy" | "is_uniform" | "has_watermark" | "is_duplicate"
export const GALLERY_LICENSE_BADGE_KEY   = "gallery-license-badge";   // "true" | "false" (off by default)

// Captioning defaults
export const CAPTION_DEFAULT_MODEL_KEY       = "caption-default-model";          // model ID string
export const CAPTION_DEFAULT_STYLE_KEY       = "caption-default-style";          // "detailed" | "short" | "tags" | "promptgen" | "booru"
export const CAPTION_DEFAULT_SCOPE_KEY       = "caption-default-scope";          // "all" | "uncaptioned"
export const CAPTION_DEFAULT_DELIMITER_KEY   = "caption-default-delimiter-mode"; // "overwrite" | "append" | "prepend"
export const CAPTION_DEFAULT_STRIP_REFS_KEY  = "caption-default-strip-refusals"; // "true" | "false"
export const CAPTION_DEFAULT_RENAME_KEY      = "caption-default-rename";         // "true" | "false"
export const CAPTION_DEFAULT_SAVE_BACKUP_KEY = "caption-default-save-backup";    // "true" | "false"

// Remembered per-page "workflow" config — global blobs shared across all datasets.
// Loaded/saved via loadPersisted/savePersisted/clearPersisted in utils/persistentState.ts
export const CAPTIONING_WORKFLOW_KEY = "captioning-workflow-config";
export const EXPORT_WORKFLOW_KEY     = "export-workflow-config";
export const QUALITY_WORKFLOW_KEY    = "quality-workflow-config";
export const BULK_EDIT_WORKFLOW_KEY  = "bulk-edit-workflow-config";
export const TAG_CONSOLIDATE_WORKFLOW_KEY = "tag-consolidate-workflow-config";
export const DATASETS_UI_KEY         = "datasets-ui-config"; // collapse / density / rail selection

// Remembered per-page "filters/scope" config — per-dataset blobs.
// Append `-${datasetId}` via datasetScopedKey() from utils/persistentState.ts
export const CAPTIONING_FILTERS_PREFIX = "captioning-filters";
export const EXPORT_FILTERS_PREFIX     = "export-filters";
export const QUALITY_FILTERS_PREFIX    = "quality-filters";
export const BULK_EDIT_FILTERS_PREFIX  = "bulk-edit-filters";
export const STATS_FILTERS_PREFIX      = "stats-filters";

const GALLERY_PAGE_SIZE_DEFAULT = 100;

export function getGalleryPageSize(): number {
  const v = parseInt(localStorage.getItem(GALLERY_PAGE_SIZE_KEY) ?? "", 10);
  return Number.isNaN(v) ? GALLERY_PAGE_SIZE_DEFAULT : v;
}

// Gallery selection checkbox size, in px. The stored value is clamped on read as
// well as on write so a hand-edited localStorage entry can't produce a checkbox
// that covers the thumbnail.
export const GALLERY_CHECKBOX_SIZE_DEFAULT = 18;
export const GALLERY_CHECKBOX_SIZE_MIN = 14;
export const GALLERY_CHECKBOX_SIZE_MAX = 32;

export function clampGalleryCheckboxSize(v: number): number {
  if (Number.isNaN(v)) return GALLERY_CHECKBOX_SIZE_DEFAULT;
  return Math.min(GALLERY_CHECKBOX_SIZE_MAX, Math.max(GALLERY_CHECKBOX_SIZE_MIN, Math.round(v)));
}

export function getGalleryCheckboxSize(): number {
  const raw = localStorage.getItem(GALLERY_CHECKBOX_SIZE_KEY);
  if (raw === null) return GALLERY_CHECKBOX_SIZE_DEFAULT;
  return clampGalleryCheckboxSize(parseInt(raw, 10));
}

export function getGalleryDefaultSort(): number {
  const v = parseInt(localStorage.getItem(GALLERY_DEFAULT_SORT_KEY) ?? "", 10);
  return Number.isNaN(v) || v < 0 || v >= SORT_OPTIONS.length ? 0 : v;
}

export function getGalleryDefaultCaptionFilter(): boolean | undefined {
  const v = localStorage.getItem(GALLERY_DEFAULT_CAPTION_KEY);
  if (v === "captioned")   return true;
  if (v === "uncaptioned") return false;
  return undefined;
}

export function getGalleryDefaultQualityFilter(): string {
  return localStorage.getItem(GALLERY_DEFAULT_QUALITY_KEY) ?? "";
}

/** Whether gallery cards show a license badge. Off by default — most datasets
 *  are single-source, where a badge on every card is pure noise. */
export function getGalleryLicenseBadge(): boolean {
  return localStorage.getItem(GALLERY_LICENSE_BADGE_KEY) === "true";
}
