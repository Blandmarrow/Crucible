import { lazy } from "react";

/**
 * Route-level code-split page modules. `React.lazy` splits each page into its
 * own chunk so the initial bundle no longer eagerly pulls in every page.
 *
 * Both `App.tsx` (the single-view router) and `components/pane/PageRenderer.tsx`
 * (the split-view pane system) import from **this one module** so a given page
 * resolves to the *same* lazy component identity in both — one chunk per page,
 * and switching a pane between single- and split-view never remounts the page
 * from a second copy. Every page is a default export, which `lazy` requires.
 */
export const DatasetsPage = lazy(() => import("./DatasetsPage"));
export const GalleryPage = lazy(() => import("./GalleryPage"));
export const ImageDetailPage = lazy(() => import("./ImageDetailPage"));
export const VideoDetailPage = lazy(() => import("./VideoDetailPage"));
export const CaptioningPage = lazy(() => import("./CaptioningPage"));
export const QualityPage = lazy(() => import("./QualityPage"));
export const StatsPage = lazy(() => import("./StatsPage"));
export const ExportPage = lazy(() => import("./ExportPage"));
export const BulkEditPage = lazy(() => import("./BulkEditPage"));
export const TagConsolidatePage = lazy(() => import("./TagConsolidatePage"));
export const BooruPage = lazy(() => import("./BooruPage"));
export const FileBrowserPage = lazy(() => import("./FileBrowserPage"));
export const SettingsPage = lazy(() => import("./SettingsPage"));
export const VersionsPage = lazy(() => import("./VersionsPage"));
export const ComfyPage = lazy(() => import("./ComfyPage"));
export const LogsPage = lazy(() => import("./LogsPage"));
