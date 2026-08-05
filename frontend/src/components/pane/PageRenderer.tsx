import { Suspense } from "react";
import type { PaneView } from "../../contexts/PaneContext";
import {
  DatasetsPage,
  GalleryPage,
  ImageDetailPage,
  VideoDetailPage,
  CaptioningPage,
  QualityPage,
  StatsPage,
  ExportPage,
  BulkEditPage,
  TagConsolidatePage,
  FileBrowserPage,
  BooruPage,
  VersionsPage,
  ComfyPage,
} from "../../pages/lazyPages";

// A pane's dataset is part of a page's *identity* for the three pages below: each
// seeds per-dataset state in `useState` initializers, which only run at mount. Without
// the key, switching a pane's dataset leaves the previous dataset's state on screen and
// — because the persist effects *do* re-fire on `datasetId` — writes it under the new
// dataset's key, destroying what that dataset remembered. `GalleryPage` leaked its whole
// filter blob, `TagConsolidatePage` could apply dataset A's analyzed tag-merge mapping to
// B, and `VersionsPage` wrote A's branch id under B's key.
//
// Deliberately *not* the other pages. `CaptioningPage`, `QualityPage`, `StatsPage`,
// `ExportPage`, `BulkEditPage` and `ComfyPage` already re-seed via a `prevDatasetId`
// effect and would be harmed by a remount: each holds an `activeJobId`/`detectJobId` in
// local state with no re-adoption path, so a key would drop a running job's progress bar
// and its completion `invalidateQueries`. `ImageDetailPage`/`VideoDetailPage` seed
// nothing per-dataset and must not be keyed at all — their arrow navigation changes
// `imageId` under a constant `datasetId`.
//
// Accepted costs of the three keys: a gallery-started rescan/import loses its completion
// toast if the pane switches away mid-job (today those effects fire against the *wrong*
// dataset, so this is a strict improvement); `autoRescannedRef` resets, so auto-rescan
// can re-fire on return (already what routed mode does every visit); an in-flight
// `tag_consolidate` analyze is orphaned rather than applied to the wrong dataset.
function renderPage(view: PaneView) {
  switch (view.page) {
    case "datasets":     return <DatasetsPage />;
    case "gallery":      return <GalleryPage key={view.datasetId} />;
    case "image-detail": return <ImageDetailPage />;
    case "video-detail": return <VideoDetailPage />;
    case "captioning":   return <CaptioningPage />;
    case "quality":      return <QualityPage />;
    case "stats":        return <StatsPage />;
    case "export":       return <ExportPage />;
    case "bulk-edit":    return <BulkEditPage />;
    case "consolidate":  return <TagConsolidatePage key={view.datasetId} />;
    case "versions":     return <VersionsPage key={view.datasetId} />;
    case "comfy":        return <ComfyPage />;
    case "file-browser": return <FileBrowserPage />;
    case "booru":        return <BooruPage />;
    default:             return <DatasetsPage />;
  }
}

export default function PageRenderer({ view }: { view: PaneView }) {
  // A per-pane Suspense so a pane whose page chunk is still loading shows its own
  // fallback without blanking a sibling pane rendering a different page.
  return (
    <Suspense fallback={<div style={{ padding: 40, color: "var(--fg-mute)" }}>Loading…</div>}>
      {renderPage(view)}
    </Suspense>
  );
}
