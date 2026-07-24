import { Suspense } from "react";
import type { PaneView } from "../../contexts/PaneContext";
import {
  DatasetsPage,
  GalleryPage,
  ImageDetailPage,
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

function renderPage(view: PaneView) {
  switch (view.page) {
    case "datasets":     return <DatasetsPage />;
    case "gallery":      return <GalleryPage />;
    case "image-detail": return <ImageDetailPage />;
    case "captioning":   return <CaptioningPage />;
    case "quality":      return <QualityPage />;
    case "stats":        return <StatsPage />;
    case "export":       return <ExportPage />;
    case "bulk-edit":    return <BulkEditPage />;
    case "consolidate":  return <TagConsolidatePage />;
    case "versions":     return <VersionsPage />;
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
