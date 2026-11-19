import { createContext, useContext } from "react";

export type PageType =
  | "datasets"
  | "gallery"
  | "captioning"
  | "quality"
  | "stats"
  | "health"
  | "chat"
  | "export"
  | "file-browser"
  | "image-detail"
  | "video-detail"
  | "booru"
  | "bulk-edit"
  | "consolidate"
  | "versions"
  | "comfy"
  | "runs"
  | "compare";

/** Every page, for checking a pane layout read back from storage. A record rather than
 *  an array so a new `PageType` that is missing here fails the build. */
const PAGE_TYPE_SET: Record<PageType, true> = {
  datasets: true, gallery: true, captioning: true, quality: true, stats: true, health: true,
  chat: true, export: true, "file-browser": true, "image-detail": true, "video-detail": true,
  booru: true, "bulk-edit": true, consolidate: true, versions: true, comfy: true, runs: true,
  compare: true,
};
export function isPageType(v: unknown): v is PageType {
  return typeof v === "string" && Object.prototype.hasOwnProperty.call(PAGE_TYPE_SET, v);
}

export interface PaneView {
  page: PageType;
  datasetId?: string;
  imageId?: string;
  videoId?: string;
  /** Gallery only: which subfolder to open at. A deep-link target, not a record
   *  of what the user has since clicked — GalleryPage applies it once per change
   *  and otherwise leaves its own state alone. "" is the dataset root. */
  subfolder?: string;
  /** Gallery only: show just the frames extracted from this video. Like
   *  `subfolder`, a deep-link target rather than a record of what the user has
   *  since chosen — GalleryPage applies it once per change and then leaves its
   *  own state alone. Unlike `subfolder`, "" carries no meaning. */
  sourceVideoId?: string;
  /** Gallery only: a `filterLink` deep link, encoded as in `?filter=`. Applied once
   *  on arrival and then cleared from the view, so returning to the gallery later
   *  in this pane never re-applies it over what the user has since chosen. */
  filter?: string;
  /** Compare only: the images to compare, in order. Unlike the two deep-link
   *  fields above this is the page's identity — ComparePage keeps it current as
   *  panes are removed or reordered, so a pane always describes what it shows. */
  compareIds?: string[];
  /** Compare only: where the set came from. "dups" (a Quality page duplicate
   *  group) offers Keep this one while the comparison is still that whole group. */
  compareFrom?: "dups";
  /** Chat only: the chat this pane shows. Per pane, so two panes on one dataset can
   *  show two chats; persisted with the layout, so a pinned chat survives a reload. */
  chatId?: string;
}

interface PaneContextValue {
  paneId: string;
  view: PaneView;
}

export const PaneContext = createContext<PaneContextValue | null>(null);

export function usePaneContext() {
  return useContext(PaneContext);
}
