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
import CropToDetectionForm from "../components/crop/CropToDetectionForm";
import DetectionBulkDeleteForm from "../components/detection/DetectionBulkDeleteForm";
import DetectionRunForm from "../components/detection/DetectionRunForm";
import LutForm from "../components/lut/LutForm";
import BulkRenameForm from "../components/image/BulkRenameForm";
import BulkDeleteForm from "../components/image/BulkDeleteForm";
import RegenerateThumbnailsForm from "../components/image/RegenerateThumbnailsForm";
import { BULK_EDIT_WORKFLOW_KEY, BULK_EDIT_FILTERS_PREFIX } from "../constants/storage";
import { loadPersisted, clearPersisted, datasetScopedKey } from "../utils/persistentState";
import { useDebouncedPersist } from "../hooks/useDebouncedPersist";

type Scope = "all" | "flags" | "selected";
type Tab = "captions" | "upscale" | "crop" | "detections" | "lut" | "rename" | "thumbnails" | "delete";

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

  const { data: countData, isFetching: countFetching, isError: countError } = useQuery({
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
  useDebouncedPersist(BULK_EDIT_WORKFLOW_KEY, { tab, scope });

  // Persist "filters" config (selected flags/subfolder) — per-dataset, debounced.
  useDebouncedPersist(
    datasetId ? datasetScopedKey(BULK_EDIT_FILTERS_PREFIX, datasetId) : null,
    {
      selectedFlags: [...selectedFlags],
      activeSubfolder: activeSubfolder ?? null,
    },
  );

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
    <div style={{ padding: "24px 32px", maxWidth: 900, flex: 1, overflowY: "auto" }}>
      {/* "Reset to defaults" sits in the header rather than beside the tabs:
          `.tabs` wraps now, and next to two rows of tabs the button floated
          against nothing. */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <Pencil size={18} style={{ color: "var(--accent)" }} />
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>Bulk Edit</h2>
        <div style={{ flex: 1 }} />
        <button className="btn ghost sm" onClick={handleResetToDefaults} title="Clear remembered configuration and revert to defaults">
          Reset to defaults
        </button>
      </div>

      {/* Tab bar */}
      <div className="tabs">
        <button className={`tab${tab === "captions" ? " active" : ""}`} onClick={() => setTab("captions")}>
          Edit Captions
        </button>
        <button className={`tab${tab === "upscale" ? " active" : ""}`} onClick={() => setTab("upscale")}>
          Upscale
        </button>
        <button className={`tab${tab === "crop" ? " active" : ""}`} onClick={() => setTab("crop")}>
          Crop to Subject
        </button>
        <button className={`tab${tab === "detections" ? " active" : ""}`} onClick={() => setTab("detections")}>
          Detections
        </button>
        <button className={`tab${tab === "lut" ? " active" : ""}`} onClick={() => setTab("lut")}>
          Apply LUT
        </button>
        <button className={`tab${tab === "rename" ? " active" : ""}`} onClick={() => setTab("rename")}>
          Rename
        </button>
        <button className={`tab${tab === "thumbnails" ? " active" : ""}`} onClick={() => setTab("thumbnails")}>
          Thumbnails
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
            countError ? (
              <p className="text-xs pt-1" style={{ color: "var(--bad)" }}>Count unavailable</p>
            ) : (
              <p className="text-xs pt-1" style={{ color: countData?.count === 0 ? "var(--warn)" : "var(--fg-mute)" }}>
                {getCountLabel(countData, countFetching)}
              </p>
            )
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
      {subfolders.some((sf) => sf.path) && scope !== "selected" && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-h">Subfolder</div>
          <div className="panel-b">
            <select
              className="select"
              value={activeSubfolder ?? ""}
              onChange={(e) => setActiveSubfolder(e.target.value || undefined)}
            >
              <option value="">All subfolders</option>
              {subfolders.filter((sf) => sf.path).map((sf) => (
                <option key={sf.path} value={sf.path}>
                  {sf.path} ({sf.image_count})
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

      {tab === "crop" && (
        <div className="panel">
          <div className="panel-h">Crop to Detected Subject</div>
          <div className="panel-b">
            <CropToDetectionForm
              key={`${scope}-${resetKey}`}
              datasetId={datasetId}
              imageIds={imageIds}
              subfolder={subfolder}
              qualityFlags={qualityFlags}
              disabled={formDisabled}
            />
          </div>
        </div>
      )}

      {tab === "detections" && (
        <>
          <div className="panel" style={{ marginBottom: 16 }}>
            <div className="panel-h">Run Detection</div>
            <div className="panel-b">
              <DetectionRunForm
                key={`run-${scope}-${resetKey}`}
                datasetId={datasetId}
                imageIds={imageIds}
                subfolder={subfolder}
                qualityFlags={qualityFlags}
                disabled={formDisabled}
              />
            </div>
          </div>
          <div className="panel">
            <div className="panel-h">Delete Detections</div>
            <div className="panel-b">
              <DetectionBulkDeleteForm
                key={`${scope}-${resetKey}`}
                datasetId={datasetId}
                imageIds={imageIds}
                subfolder={subfolder}
                qualityFlags={qualityFlags}
                disabled={formDisabled}
              />
            </div>
          </div>
        </>
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

      {/* The surface that matters for the repair: a failing thumbnails/ is
          deterministic, so the affected scope is "the whole dataset" — and
          SelectionToolbar only exists when there is a selection. */}
      {tab === "thumbnails" && (
        <div className="panel">
          <div className="panel-h">Rebuild Thumbnails</div>
          <div className="panel-b">
            <RegenerateThumbnailsForm
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
