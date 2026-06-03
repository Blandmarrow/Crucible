export const CONFIRM_DEFAULT_KEY = "confirm-danger-default-action";
export const BRANCH_SNAPSHOT_KEY = "branch-snapshot-behavior"; // "ask" | "auto"
export const VERSIONS_BRANCH_KEY = "versions-branch"; // prefix; append `-${datasetId}` for the full key
export const GALLERY_PAGE_SIZE_KEY = "gallery-page-size"; // 25 | 50 | 100 | 200
export const SUBFOLDER_RENAME_KEY = "subfolder-auto-rename"; // "on" | "off"
export const DECLARED_CATEGORIES_KEY = "crucible-declared-categories"; // string[]

import { SORT_OPTIONS } from "./galleryOptions";

// Gallery default filters (applied when no session state exists for a dataset)
export const GALLERY_DEFAULT_SORT_KEY    = "gallery-default-sort";    // number (index into SORT_OPTIONS)
export const GALLERY_DEFAULT_CAPTION_KEY = "gallery-default-caption"; // "all" | "captioned" | "uncaptioned"
export const GALLERY_DEFAULT_QUALITY_KEY = "gallery-default-quality"; // "" | "is_blurry" | "is_noisy" | "is_uniform" | "has_watermark" | "is_duplicate"

// Captioning defaults
export const CAPTION_DEFAULT_MODEL_KEY       = "caption-default-model";          // model ID string
export const CAPTION_DEFAULT_STYLE_KEY       = "caption-default-style";          // "detailed" | "short" | "tags" | "promptgen" | "booru"
export const CAPTION_DEFAULT_SCOPE_KEY       = "caption-default-scope";          // "all" | "uncaptioned"
export const CAPTION_DEFAULT_DELIMITER_KEY   = "caption-default-delimiter-mode"; // "overwrite" | "append" | "prepend"
export const CAPTION_DEFAULT_STRIP_REFS_KEY  = "caption-default-strip-refusals"; // "true" | "false"
export const CAPTION_DEFAULT_RENAME_KEY      = "caption-default-rename";         // "true" | "false"
export const CAPTION_DEFAULT_SAVE_BACKUP_KEY = "caption-default-save-backup";    // "true" | "false"

const GALLERY_PAGE_SIZE_DEFAULT = 100;

export function getGalleryPageSize(): number {
  const v = parseInt(localStorage.getItem(GALLERY_PAGE_SIZE_KEY) ?? "", 10);
  return Number.isNaN(v) ? GALLERY_PAGE_SIZE_DEFAULT : v;
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
