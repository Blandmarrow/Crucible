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
  | "comfy";

export interface PaneView {
  page: PageType;
  datasetId?: string;
  imageId?: string;
  videoId?: string;
}

interface PaneContextValue {
  paneId: string;
  view: PaneView;
}

export const PaneContext = createContext<PaneContextValue | null>(null);

export function usePaneContext() {
  return useContext(PaneContext);
}
