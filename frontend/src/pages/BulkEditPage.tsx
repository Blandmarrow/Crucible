import { useState } from "react";
import { Pencil } from "lucide-react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useSelectionStore } from "../store/selectionStore";
import { FLAG_OPTIONS } from "../constants/flags";
import BulkEditForm from "../components/caption/BulkEditForm";
import UpscaleForm from "../components/upscale/UpscaleForm";

type Scope = "all" | "flags" | "selected";
type Tab = "captions" | "upscale";

export default function BulkEditPage() {
  const datasetId = usePaneDatasetId();
  const { selectedIds, count: selectedCount } = useSelectionStore();

  const [tab, setTab] = useState<Tab>("captions");
  const [scope, setScope] = useState<Scope>("all");
  const [selectedFlags, setSelectedFlags] = useState<Set<string>>(new Set());

  const toggleFlag = (key: string) => {
    setSelectedFlags(prev => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  if (!datasetId) {
    return <div style={{ padding: 32, color: "var(--fg-mute)" }}>No dataset selected.</div>;
  }

  const imageIds = scope === "selected" ? [...selectedIds] : undefined;
  const qualityFlags = scope === "flags" ? [...selectedFlags] : undefined;
  const formDisabled = scope === "flags" && selectedFlags.size === 0;

  return (
    <div style={{ padding: "24px 32px", maxWidth: 680, flex: 1, overflowY: "auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <Pencil size={18} style={{ color: "var(--accent)" }} />
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>Bulk Edit</h2>
      </div>

      {/* Tab bar */}
      <div className="tabs" style={{ marginBottom: 20 }}>
        <button className={`tab${tab === "captions" ? " active" : ""}`} onClick={() => setTab("captions")}>
          Edit Captions
        </button>
        <button className={`tab${tab === "upscale" ? " active" : ""}`} onClick={() => setTab("upscale")}>
          Upscale
        </button>
      </div>

      {/* Scope — shared between both tabs */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-h">Scope</div>
        <div className="panel-b space-y-2">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="radio" name="scope" checked={scope === "all"} onChange={() => setScope("all")} />
            All images in dataset
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="radio" name="scope" checked={scope === "flags"} onChange={() => setScope("flags")} />
            Exclude images with quality flags
          </label>
          <label className={`flex items-center gap-2 cursor-pointer text-sm ${selectedCount === 0 ? "opacity-40" : ""}`}>
            <input
              type="radio"
              name="scope"
              checked={scope === "selected"}
              onChange={() => setScope("selected")}
              disabled={selectedCount === 0}
            />
            Currently selected ({selectedCount} image{selectedCount !== 1 ? "s" : ""})
          </label>
        </div>
      </div>

      {/* Quality flag picker — only shown for "flags" scope */}
      {scope === "flags" && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-h">Exclude images flagged as (any)</div>
          <div className="panel-b">
            <div className="flex flex-wrap gap-1.5">
              {FLAG_OPTIONS.map(({ key, label }) => (
                <button
                  key={key}
                  className={`btn btn-sm ${selectedFlags.has(key) ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => toggleFlag(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            {selectedFlags.size === 0 && (
              <p className="text-xs text-warn mt-2">Select at least one flag to enable exclusion.</p>
            )}
          </div>
        </div>
      )}

      {/* Tab content */}
      {tab === "captions" && (
        <div className="panel">
          <div className="panel-h">Operation</div>
          <div className="panel-b">
            <BulkEditForm
              key={scope}
              datasetId={datasetId}
              imageIds={imageIds}
              qualityFlags={qualityFlags}
              disabled={formDisabled}
            />
          </div>
        </div>
      )}

      {tab === "upscale" && (
        <div className="panel">
          <div className="panel-h">Upscale</div>
          <div className="panel-b">
            <UpscaleForm
              key={scope}
              datasetId={datasetId}
              imageIds={imageIds}
            />
          </div>
        </div>
      )}
    </div>
  );
}
