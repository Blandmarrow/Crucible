export const CONFIRM_DEFAULT_KEY = "confirm-danger-default-action";
export const BRANCH_SNAPSHOT_KEY = "branch-snapshot-behavior"; // "ask" | "auto"
export const VERSIONS_BRANCH_KEY = "versions-branch"; // prefix; append `-${datasetId}` for the full key
export const GALLERY_PAGE_SIZE_KEY = "gallery-page-size"; // 25 | 50 | 100 | 200
export const SUBFOLDER_RENAME_KEY = "subfolder-auto-rename"; // "on" | "off"
export const DECLARED_CATEGORIES_KEY = "crucible-declared-categories"; // string[]

const GALLERY_PAGE_SIZE_DEFAULT = 100;

export function getGalleryPageSize(): number {
  const v = parseInt(localStorage.getItem(GALLERY_PAGE_SIZE_KEY) ?? "", 10);
  return Number.isNaN(v) ? GALLERY_PAGE_SIZE_DEFAULT : v;
}
