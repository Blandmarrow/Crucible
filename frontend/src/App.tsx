import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "react-hot-toast";

import Sidebar from "./components/layout/Sidebar";
import TopBar from "./components/layout/TopBar";
import DatasetsPage from "./pages/DatasetsPage";
import GalleryPage from "./pages/GalleryPage";
import ImageDetailPage from "./pages/ImageDetailPage";
import CaptioningPage from "./pages/CaptioningPage";
import QualityPage from "./pages/QualityPage";
import StatsPage from "./pages/StatsPage";
import ExportPage from "./pages/ExportPage";
import BulkEditPage from "./pages/BulkEditPage";
import TagConsolidatePage from "./pages/TagConsolidatePage";
import BooruPage from "./pages/BooruPage";
import FileBrowserPage from "./pages/FileBrowserPage";
import SettingsPage from "./pages/SettingsPage";
import VersionsPage from "./pages/VersionsPage";
import ComfyPage from "./pages/ComfyPage";
import PaneContainer from "./components/pane/PaneContainer";
import ErrorBoundary from "./components/common/ErrorBoundary";
import ErrorConsole from "./components/common/ErrorConsole";
import LogsPage from "./pages/LogsPage";
import { usePaneStore } from "./stores/paneStore";
import type { PaneView, PageType } from "./contexts/PaneContext";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000 },
  },
});

function routeToView(pathname: string): PaneView {
  const dsImageMatch = pathname.match(/^\/datasets\/([^/]+)\/image\/([^/]+)/);
  if (dsImageMatch) return { page: "image-detail", datasetId: dsImageMatch[1], imageId: dsImageMatch[2] };
  const dsPageMatch = pathname.match(/^\/datasets\/([^/]+)\/([^/]+)/);
  if (dsPageMatch) {
    const seg = dsPageMatch[2] as PageType;
    return { page: seg as PageType, datasetId: dsPageMatch[1] };
  }
  if (pathname.startsWith("/booru")) return { page: "booru" };
  if (pathname.startsWith("/file-browser")) return { page: "file-browser" };
  return { page: "datasets" };
}

function RouteSyncer() {
  const location = useLocation();
  const { enabled, syncFromRoute } = usePaneStore();
  useEffect(() => {
    if (enabled) syncFromRoute(routeToView(location.pathname));
  }, [location.pathname, enabled, syncFromRoute]);
  return null;
}

function MainContent() {
  const { enabled, layout } = usePaneStore();

  if (enabled) {
    return (
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <PaneContainer node={layout} isOnly={layout.type === "leaf"} />
      </div>
    );
  }

  return (
    <main style={{ flex: 1, overflowY: "auto" }}>
      <Routes>
        <Route path="/" element={<Navigate to="/datasets" replace />} />
        <Route path="/datasets" element={<ErrorBoundary><DatasetsPage /></ErrorBoundary>} />
        <Route path="/booru" element={<ErrorBoundary><BooruPage /></ErrorBoundary>} />
        <Route path="/datasets/:datasetId/gallery" element={<ErrorBoundary><GalleryPage /></ErrorBoundary>} />
        <Route path="/datasets/:datasetId/image/:imageId" element={<ErrorBoundary><ImageDetailPage /></ErrorBoundary>} />
        <Route path="/datasets/:datasetId/captioning" element={<ErrorBoundary><CaptioningPage /></ErrorBoundary>} />
        <Route path="/datasets/:datasetId/quality" element={<ErrorBoundary><QualityPage /></ErrorBoundary>} />
        <Route path="/datasets/:datasetId/stats" element={<ErrorBoundary><StatsPage /></ErrorBoundary>} />
        <Route path="/datasets/:datasetId/export" element={<ErrorBoundary><ExportPage /></ErrorBoundary>} />
        <Route path="/datasets/:datasetId/bulk-edit" element={<ErrorBoundary><BulkEditPage /></ErrorBoundary>} />
        <Route path="/datasets/:datasetId/consolidate" element={<ErrorBoundary><TagConsolidatePage /></ErrorBoundary>} />
        <Route path="/datasets/:datasetId/versions" element={<ErrorBoundary><VersionsPage /></ErrorBoundary>} />
        <Route path="/datasets/:datasetId/comfy" element={<ErrorBoundary><ComfyPage /></ErrorBoundary>} />
        <Route path="/file-browser" element={<ErrorBoundary><FileBrowserPage /></ErrorBoundary>} />
        <Route path="/settings" element={<ErrorBoundary><SettingsPage /></ErrorBoundary>} />
        <Route path="/logs" element={<ErrorBoundary><LogsPage /></ErrorBoundary>} />
      </Routes>
    </main>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <RouteSyncer />
        <div style={{ "--sidebar-w": "240px", display: "grid", gridTemplateColumns: "var(--sidebar-w) 1fr", height: "100vh", overflow: "hidden" } as any}>
          <Sidebar />
          <div style={{ display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0, overflow: "hidden" }}>
            <TopBar />
            <MainContent />
          </div>
        </div>
        <ErrorConsole />
        <Toaster
          position="bottom-right"
          toastOptions={{
            style: { background: "var(--surface-2)", color: "var(--fg)", border: "1px solid var(--line-2)" },
          }}
        />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
