import type { PaneView } from "../contexts/PaneContext";

/** The most images one comparison holds. It also bounds the URL: 100 uuids
 *  joined by commas is about 3,700 characters, under every browser's limit. */
export const COMPARE_MAX = 100;

/** Side by side shows at most this many panes; the rest wait their turn. */
export const COMPARE_SIDE_MAX = 4;

/** How A/B shows the second image: clipped beside a divider, swapped in while a
 *  key is held, or blended over the first as a difference. */
export type AbHow = "swipe" | "flip" | "difference";
export const AB_HOWS: { value: AbHow; label: string }[] = [
  { value: "swipe", label: "Swipe" },
  { value: "flip", label: "Flip" },
  { value: "difference", label: "Difference" },
];

/** The grid's "auto" columns: `ceil(sqrt(n × 1.4))`, roughly square and a little
 *  wider than tall — the server's `auto_columns` in `services/contact_sheet.py`,
 *  so the grid on screen and the sheet it exports agree — but never one column. */
export function autoGridColumns(n: number): number {
  return Math.min(n, Math.max(2, Math.ceil(Math.sqrt(n * 1.4))));
}

/** Where a comparison came from; see `PaneView.compareFrom`. */
export type CompareFrom = "dups";

export function compareUrl(ids: string[], from?: CompareFrom): string {
  return `/compare?ids=${ids.join(",")}${from ? `&from=${from}` : ""}`;
}

export function compareView(ids: string[], from?: CompareFrom): PaneView {
  return from ? { page: "compare", compareIds: ids, compareFrom: from } : { page: "compare", compareIds: ids };
}
