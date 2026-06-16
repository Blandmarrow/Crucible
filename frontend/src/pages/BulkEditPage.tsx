import { useMemo, useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pencil } from "lucide-react";
import toast from "react-hot-toast";
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
import { BULK_EDIT_WORKFLOW_KEY, BULK_EDIT_FILTERS_PREFIX } from "../constants/storage";
import { loadPersisted, savePersisted, clearPersisted, datasetScopedKey } from "../utils/persistentState";

type Scope = "all" | "flags" | "selected";
type Tab = "captions" | "upscale" | "lut" | "rename" | "delete";

interface BulkEditWorkflow {
  tab: Tab;
  scope: Scope;
}

const BULK_EDIT_WORKFLOW_DEFAULTS: BulkEditWorkflow = {
  tab: "captions",
  scope: "all",
};

interface BulkEditFilters {
  selectedFlags: string[];
  activeSubfolder: string | null;
}

const BULK_EDIT_FILTERS_DEFAULTS: BulkEditFilters = {
  selectedFlags: [],
  activeSubfolder: null,
};

function getCountLabel(data: { count: number } | undefined, fetching: boolean): string {
  if (fetching) return "Counting…";
  if (!data) return "";
  if (data.count === 0) return "No matching images";
  return `${data.count.toLocaleString()} image${data.count !== 1 ? "s" : ""} will be affected`;
}

export default function BulkEditPage() {
  const datasetId = usePaneDatasetId();
  const { selectedIds, count: selectedCount } = useSelectionStore();

  // Remembered "workflow" config — global, shared across all datasets.
  const [workflow] = useState(() => loadPersisted(BULK_EDIT_WORKFLOW_KEY, BULK_EDIT_WORKFLOW_DEFAULTS));
  const [tab, setTab] = useState<Tab>(workflow.tab);
  const [scope, setScope] = useState<Scope>(() =>
    workflow.scope === "selected" && selectedCount === 0 ? "all" : workflow.scope
  );
  const [resetKey, setResetKey] = useState(0);

  // Remembered "filters" config — per-dataset.
  const [filters] = useState(() =>
    datasetId ? loadPersisted(datasetScopedKey(BULK_EDIT_FILTERS_PREFIX, datasetId), BULK_EDIT_FILTERS_DEFAULTS) : BULK_EDIT_FILTERS_DEFAULTS
  );
  const [selectedFlags, setSelectedFlags] = useState<Set<string>>(new Set(filters.selectedFlags));
  const [activeSubfolder, setActiveSubfolder] = useState<string | undefined>(filters.activeSubfolder ?? undefined);

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

  // Persist "workflow" config (tab/scope) — global, debounced.
  useEffect(() => {
    const t = setTimeout(() => {
      savePersisted(BULK_EDIT_WORKFLOW_KEY, { tab, scope });
    }, 350);
    return () => clearTimeout(t);
  }, [tab, scope]);

  // Persist "filters" config (selected flags/subfolder) — per-dataset, debounced.
  useEffect(() => {
    if (!datasetId) return;
    const t = setTimeout(() => {
      savePersisted(datasetScopedKey(BULK_EDIT_FILTERS_PREFIX, datasetId), {
        selectedFlags: [...selectedFlags],
        activeSubfolder: activeSubfolder ?? null,
      });
    }, 350);
    return () => clearTimeout(t);
  }, [datasetId, selectedFlags, activeSubfolder]);

  // Reload the "filters" blob when datasetId changes without a remount (pane mode).
  const prevDatasetId = useRef(datasetId);
  useEffect(() => {
    if (datasetId === prevDatasetId.current) return;
    prevDatasetId.current = datasetId;
    const next = datasetId
      ? loadPersisted(datasetScopedKey(BULK_EDIT_FILTERS_PREFIX, datasetId), BULK_EDIT_FILTERS_DEFAULTS)
      : BULK_EDIT_FILTERS_DEFAULTS;
    setSelectedFlags(new Set(next.selectedFlags));
    setActiveSubfolder(next.activeSubfolder ?? undefined);
  }, [datasetId]);

  function handleResetToDefaults() {
    clearPersisted(BULK_EDIT_WORKFLOW_KEY);
    if (datasetId) clearPersisted(datasetScopedKey(BULK_EDIT_FILTERS_PREFIX, datasetId));

    setTab(BULK_EDIT_WORKFLOW_DEFAULTS.tab);
    setScope(BULK_EDIT_WORKFLOW_DEFAULTS.scope);
    setSelectedFlags(new Set(BULK_EDIT_FILTERS_DEFAULTS.selectedFlags));
    setActiveSubfolder(BULK_EDIT_FILTERS_DEFAULTS.activeSubfolder ?? undefined);
    setResetKey(k => k + 1);

    toast.success("Configuration reset to defaults");
  }

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
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
        <div className="tabs" style={{ flex: 1 }}>
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
        <button className="btn ghost sm" onClick={handleResetToDefaults} title="Clear remembered configuration and revert to defaults">
          Reset to defaults
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
              key={`${scope}-${resetKey}`}
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
              key={`${scope}-${resetKey}`}
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
              key={`${scope}-${resetKey}`}
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
              key={`${scope}-${resetKey}`}
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
              key={`${scope}-${resetKey}`}
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
