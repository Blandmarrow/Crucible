import { useQuery } from "@tanstack/react-query";
import { SplitSquareHorizontal, SplitSquareVertical, X, Layers } from "lucide-react";
import { datasetsApi } from "../../api/datasets";
import { usePaneStore } from "../../stores/paneStore";
import type { PageType, PaneView } from "../../contexts/PaneContext";

const PAGE_OPTIONS: { value: PageType; label: string }[] = [
  { value: "datasets",     label: "Datasets" },
  { value: "gallery",      label: "Gallery" },
  { value: "captioning",   label: "Captioning" },
  { value: "quality",      label: "Score Images" },
  { value: "stats",        label: "Stats" },
  { value: "export",       label: "Export" },
  { value: "bulk-edit",    label: "Bulk Edit" },
  { value: "file-browser", label: "File Browser" },
  { value: "booru",        label: "Booru Browser" },
];

const NEEDS_DATASET = new Set<PageType>(["gallery", "captioning", "quality", "stats", "export", "bulk-edit"]);

interface Props {
  paneId: string;
  view: PaneView;
  isOnly: boolean;
}

export default function PaneHeader({ paneId, view, isOnly }: Props) {
  const { splitPane, closePane, setView } = usePaneStore();

  const { data: datasets = [] } = useQuery({
    queryKey: ["datasets"],
    queryFn: datasetsApi.list,
    staleTime: 30_000,
  });

  const handlePageChange = (page: PageType) => {
    const needsDs = NEEDS_DATASET.has(page);
    setView(paneId, {
      page,
      datasetId: needsDs ? (view.datasetId ?? datasets[0]?.id) : undefined,
    });
  };

  const handleDatasetChange = (datasetId: string) => {
    setView(paneId, { ...view, datasetId });
  };

  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: 6, height: 32, padding: "0 8px",
        background: "var(--surface-2)", borderBottom: "1px solid var(--line)",
        flexShrink: 0, userSelect: "none",
      }}
    >
      <Layers size={12} style={{ color: "var(--accent)", flexShrink: 0 }} />

      {/* Page selector */}
      <select
        className="select"
        style={{ fontSize: 11, padding: "1px 4px", height: 22, flex: "0 0 auto" }}
        value={view.page}
        onChange={(e) => handlePageChange(e.target.value as PageType)}
      >
        {PAGE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {/* Dataset selector (only when page needs one) */}
      {NEEDS_DATASET.has(view.page) && datasets.length > 0 && (
        <select
          className="select"
          style={{ fontSize: 11, padding: "1px 4px", height: 22, flex: "1 1 0", minWidth: 0 }}
          value={view.datasetId ?? ""}
          onChange={(e) => handleDatasetChange(e.target.value)}
        >
          {!view.datasetId && <option value="">— select dataset —</option>}
          {datasets.map((ds) => (
            <option key={ds.id} value={ds.id}>{ds.name}</option>
          ))}
        </select>
      )}

      <div style={{ flex: 1 }} />

      {/* Split controls */}
      <button
        className="icon-btn"
        title="Split horizontal (side by side)"
        style={{ width: 22, height: 22 }}
        onClick={() => splitPane(paneId, "horizontal")}
      >
        <SplitSquareHorizontal size={12} />
      </button>
      <button
        className="icon-btn"
        title="Split vertical (top / bottom)"
        style={{ width: 22, height: 22 }}
        onClick={() => splitPane(paneId, "vertical")}
      >
        <SplitSquareVertical size={12} />
      </button>
      <button
        className="icon-btn"
        title="Close pane"
        style={{ width: 22, height: 22 }}
        disabled={isOnly}
        onClick={() => closePane(paneId)}
      >
        <X size={12} />
      </button>
    </div>
  );
}
