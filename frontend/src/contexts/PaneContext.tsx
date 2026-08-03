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
  // Dataset-free, like "booru" and "file-browser": it pools ratings across every
  // dataset, so `PaneView` needs no new id field for it.
  | "rating";

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
}

interface PaneContextValue {
  paneId: string;
  view: PaneView;
}

export const PaneContext = createContext<PaneContextValue | null>(null);

export function usePaneContext() {
  return useContext(PaneContext);
}
