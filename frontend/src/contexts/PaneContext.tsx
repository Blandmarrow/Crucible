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
  | "booru"
  | "bulk-edit"
  | "consolidate"
  | "versions";

export interface PaneView {
  page: PageType;
  datasetId?: string;
  imageId?: string;
}

interface PaneContextValue {
  paneId: string;
  view: PaneView;
}

export const PaneContext = createContext<PaneContextValue | null>(null);

export function usePaneContext() {
  return useContext(PaneContext);
}
