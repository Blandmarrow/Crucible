import type { PaneView } from "../contexts/PaneContext";

/** The most images one comparison holds. It also bounds the URL: 100 uuids
 *  joined by commas is about 3,700 characters, under every browser's limit. */
export const COMPARE_MAX = 100;

/** Side by side shows at most this many panes; the rest wait their turn. */
export const COMPARE_SIDE_MAX = 4;

export function compareUrl(ids: string[]): string {
  return `/compare?ids=${ids.join(",")}`;
}

export function compareView(ids: string[]): PaneView {
  return { page: "compare", compareIds: ids };
}
