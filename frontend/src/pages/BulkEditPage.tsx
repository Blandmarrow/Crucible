import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pencil } from "lucide-react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useSelectionStore } from "../store/selectionStore";
import { FLAG_OPTIONS } from "../constants/flags";
import { datasetsApi } from "../api/datasets";
import { imagesApi } from "../api/images";
import BulkEditForm from "../components/caption/BulkEditForm";
import UpscaleForm from "../components/upscale/UpscaleForm";
import LutForm from "../components/lut/LutForm";
import BulkRenameForm from "../components/image/BulkRenameForm";
import BulkDeleteForm from "../components/image/BulkDeleteForm";

type Scope = "all" | "flags" | "selected";
type Tab = "captions" | "upscale" | "lut" | "rename" | "delete";

function getCountLabel(data: { count: number } | undefined, fetching: boolean): string {
  if (fetching) return "Counting…";
  if (!data) return "";
  if (data.count === 0) return "No matching images";
  return `${data.count.toLocaleString()} image${data.count !== 1 ? "s" : ""} will be affected`;
}

export default function BulkEditPage() {
  const datasetId = usePaneDatasetId();
  const { selectedIds, count: selectedCount } = useSelectionStore();

  const [tab, setTab] = useState<Tab>("captions");
  const [scope, setScope] = useState<Scope>("all");
  const [selectedFlags, setSelectedFlags] = useState<Set<string>>(new Set());
  const [activeSubfolder, setActiveSubfolder] = useState<string | undefined>();

  const imageIds = useMemo(() => scope === "selected" ? [...selectedIds] : undefined, [scope, selectedIds]);
  const qualityFlags = useMemo(() => scope === "flags" ? [...selectedFlags] : undefined, [scope, selectedFlags]);
  const subfolder = scope !== "selected" ? activeSubfolder : undefined;
  const formDisabled = scope === "flags" && selectedFlags.size === 0;
  const targetsFlaggedImages = tab === "delete";

  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  const { data: countData, isFetching: countFetching } = useQuery({
    queryKey: ["bulk-count", datasetId, imageIds ?? null, qualityFlags ?? null, subfolder ?? null, targetsFlaggedImages],
    queryFn: () => imagesApi.bulkCount(datasetId!, { imageIds, qualityFlags, subfolder, includeFlagged: targetsFlaggedImages }),
    enabled: !!datasetId && !formDisabled,
    staleTime: 10_000,
  });

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
        <button className={`tab${tab === "lut" ? " active" : ""}`} onClick={() => setTab("lut")}>
          Apply LUT
        </button>
        <button className={`tab${tab === "rename" ? " active" : ""}`} onClick={() => setTab("rename")}>
          Rename
        </button>
        <button className={`tab${tab === "delete" ? " active" : ""}`} onClick={() => setTab("delete")}>
          Delete
        </button>
      </div>

      {/* Scope — shared across all tabs */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-h">Scope</div>
        <div className="panel-b space-y-2">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="radio" name="scope" checked={scope === "all"} onChange={() => setScope("all")} />
            All images in dataset
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="radio" name="scope" checked={scope === "flags"} onChange={() => setScope("flags")} />
            {targetsFlaggedImages ? "Only images with quality flags" : "Exclude images with quality flags"}
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
          {!formDisabled && (
            <p className="text-xs pt-1" style={{ color: countData?.count === 0 ? "var(--warn)" : "var(--fg-mute)" }}>
              {getCountLabel(countData, countFetching)}
            </p>
          )}
        </div>
      </div>

      {/* Quality flag picker — only shown for "flags" scope */}
      {scope === "flags" && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-h">{targetsFlaggedImages ? "Only images flagged as (any)" : "Exclude images flagged as (any)"}</div>
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
              <p className="text-xs text-warn mt-2">{targetsFlaggedImages ? "Select at least one flag to target." : "Select at least one flag to enable exclusion."}</p>
            )}
          </div>
        </div>
      )}

      {/* Subfolder filter — shown when subfolders exist and scope is not "selected" */}
      {subfolders.length > 0 && scope !== "selected" && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-h">Subfolder</div>
          <div className="panel-b">
            <select
              className="select"
              value={activeSubfolder ?? ""}
              onChange={(e) => setActiveSubfolder(e.target.value || undefined)}
            >
              <option value="">All subfolders</option>
              {subfolders.map((sf) => (
                <option key={sf.path} value={sf.path}>
                  {sf.path === "" ? "(root)" : sf.path}
                </option>
              ))}
            </select>
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
              subfolder={subfolder}
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
              subfolder={subfolder}
              qualityFlags={qualityFlags}
            />
          </div>
        </div>
      )}

      {tab === "lut" && (
        <div className="panel">
          <div className="panel-h">Apply LUT</div>
          <div className="panel-b">
            <LutForm
              key={scope}
              datasetId={datasetId}
              imageIds={imageIds}
              subfolder={subfolder}
              qualityFlags={qualityFlags}
            />
          </div>
        </div>
      )}

      {tab === "rename" && (
        <div className="panel">
          <div className="panel-h">Rename Images</div>
          <div className="panel-b">
            <BulkRenameForm
              key={scope}
              datasetId={datasetId}
              imageIds={imageIds}
              qualityFlags={qualityFlags}
              subfolder={subfolder}
              disabled={formDisabled}
            />
          </div>
        </div>
      )}

      {tab === "delete" && (
        <div className="panel">
          <div className="panel-h">Delete Images</div>
          <div className="panel-b">
            <BulkDeleteForm
              key={scope}
              datasetId={datasetId}
              imageIds={imageIds}
              qualityFlags={qualityFlags}
              subfolder={subfolder}
              disabled={formDisabled}
            />
          </div>
        </div>
      )}
    </div>
  );
}
