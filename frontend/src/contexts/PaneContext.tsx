import { createContext, useContext } from "react";

export type PageType =
  | "datasets"
  | "gallery"
  | "captioning"
  | "quality"
  | "stats"
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
  /** Compare only: the images to compare, in order. Unlike the two deep-link
   *  fields above this is the page's identity — ComparePage keeps it current as
   *  panes are removed or reordered, so a pane always describes what it shows. */
  compareIds?: string[];
  /** Compare only: where the set came from. "dups" (a Quality page duplicate
   *  group) offers Keep this one while the comparison is still that whole group. */
  compareFrom?: "dups";
}

interface PaneContextValue {
  paneId: string;
  view: PaneView;
}

export const PaneContext = createContext<PaneContextValue | null>(null);

export function usePaneContext() {
  return useContext(PaneContext);
}
