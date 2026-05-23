import type { PaneView } from "../../contexts/PaneContext";
import DatasetsPage from "../../pages/DatasetsPage";
import GalleryPage from "../../pages/GalleryPage";
import ImageDetailPage from "../../pages/ImageDetailPage";
import CaptioningPage from "../../pages/CaptioningPage";
import QualityPage from "../../pages/QualityPage";
import StatsPage from "../../pages/StatsPage";
import ExportPage from "../../pages/ExportPage";
import BulkEditPage from "../../pages/BulkEditPage";
import FileBrowserPage from "../../pages/FileBrowserPage";
import BooruPage from "../../pages/BooruPage";
import VersionsPage from "../../pages/VersionsPage";

export default function PageRenderer({ view }: { view: PaneView }) {
  switch (view.page) {
    case "datasets":     return <DatasetsPage />;
    case "gallery":      return <GalleryPage />;
    case "image-detail": return <ImageDetailPage />;
    case "captioning":   return <CaptioningPage />;
    case "quality":      return <QualityPage />;
    case "stats":        return <StatsPage />;
    case "export":       return <ExportPage />;
    case "bulk-edit":    return <BulkEditPage />;
    case "versions":     return <VersionsPage />;
    case "file-browser": return <FileBrowserPage />;
    case "booru":        return <BooruPage />;
    default:             return <DatasetsPage />;
  }
}
