import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../../utils/apiError";
import { comfyApi, type ComfyPlan, type WorkflowFile } from "../../api/comfy";
import { settingsApi } from "../../api/settings";
import { useModalBehavior } from "../../hooks/useModalBehavior";
import DirPickerModal from "../common/DirPickerModal";

interface Props {
  onLoad: (workflow: ComfyPlan["workflow_json"], sourceName: string) => void;
  onClose: () => void;
}

const FORMAT_BADGE: Record<WorkflowFile["format"], { cls: string; label: string; hint?: string }> = {
  api: { cls: "badge dot good", label: "API" },
  ui: { cls: "badge dot", label: "UI", hint: 'UI-format save — use "Workflow → Export (API)" in ComfyUI and save that file' },
  invalid: { cls: "badge dot bad", label: "not a workflow", hint: "Not a workflow JSON" },
};

/** Lists workflow .json files in a server-side folder; API-format ones are loadable. */
export default function WorkflowScanModal({ onLoad, onClose }: Props) {
  const { data: thresholds } = useQuery({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
  });
  // null = follow the settings default; a string = manual override for this scan.
  const [dirOverride, setDirOverride] = useState<string | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [loadingPath, setLoadingPath] = useState<string | null>(null);

  const dir = dirOverride ?? thresholds?.comfy_workflow_dir ?? "";
  const { data, isFetching, error } = useQuery({
    queryKey: ["comfy", "workflow-files", dir],
    queryFn: () => comfyApi.listWorkflowFiles(dir || undefined),
    // `dir` already folds in the settings fallback, so following the default stays
    // disabled (empty) until thresholds load, while a manual pick scans immediately
    // — don't block a chosen folder on the settings query.
    enabled: !!dir.trim(),
    retry: false,
  });

  async function loadFile(f: WorkflowFile) {
    setLoadingPath(f.path);
    try {
      const res = await comfyApi.loadWorkflowFile(f.path);
      onLoad(res.workflow, f.name);
    } catch (err: unknown) {
      toast.error(apiErrorDetail(err, "Failed to load workflow file"));
    } finally {
      setLoadingPath(null);
    }
  }

  const errorDetail = error ? apiErrorDetail(error, "") : "";
  const { overlayProps, panelProps } = useModalBehavior({ onClose, label: "Scan workflow folder", closeOnBackdrop: true });

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
      {...overlayProps}
    >
      <div className="panel" style={{ width: 640, maxWidth: "94vw" }} {...panelProps}>
        <div className="panel-h">
          <h3>Scan workflow folder</h3>
          <div style={{ flex: 1 }} />
          <button className="icon-btn" title="Close" onClick={onClose}>×</button>
        </div>
        <div className="panel-b">
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
            <span className="mono" style={{ fontSize: 12, color: "var(--fg-dim)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {dir.trim() || "No folder selected — set a default in Settings → ComfyUI, or browse"}
            </span>
            <button className="btn ghost sm" onClick={() => setShowPicker(true)}>Browse…</button>
          </div>

          {errorDetail && <p style={{ color: "var(--bad)", fontSize: 12 }}>{errorDetail}</p>}
          {isFetching && <p style={{ color: "var(--fg-mute)", fontSize: 12 }}>Scanning…</p>}
          {data && data.files.length === 0 && (
            <p style={{ color: "var(--fg-mute)", fontSize: 12 }}>No .json files found in this folder.</p>
          )}

          {data && data.files.length > 0 && (
            <>
              <div style={{ maxHeight: "50vh", overflowY: "auto", border: "1px solid var(--line)", borderRadius: "var(--r)" }}>
                {data.files.map((f) => {
                  const badge = FORMAT_BADGE[f.format];
                  return (
                    <div key={f.path} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 10px", borderBottom: "1px solid var(--line-2)", fontSize: 12 }}>
                      <span className="mono" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.path}>
                        {f.name}
                      </span>
                      <span style={{ color: "var(--fg-dim)", whiteSpace: "nowrap" }}>
                        {new Date(f.modified_at).toLocaleDateString()}
                      </span>
                      <span className={badge.cls} title={badge.hint}>{badge.label}</span>
                      <button
                        className="btn primary sm"
                        style={{ fontSize: 11, padding: "1px 10px" }}
                        disabled={loadingPath !== null || f.format !== "api"}
                        title={f.format === "api" ? undefined : badge.hint}
                        onClick={() => loadFile(f)}
                      >
                        {loadingPath === f.path ? "Loading…" : "Load"}
                      </button>
                    </div>
                  );
                })}
              </div>
              {!data.files.some((f) => f.format === "api") && (
                <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "8px 0 0" }}>
                  None of these files can be loaded: workflows saved normally by ComfyUI are UI-format
                  (editor layout) and can't be executed via its HTTP API. In ComfyUI, open the workflow
                  and use <b>Workflow → Export (API)</b> to save a loadable copy into this folder.
                </p>
              )}
            </>
          )}
        </div>
      </div>

      {/* Stacks over this modal. No click shield needed: the overlay above only
          closes on a click that lands on the overlay itself. */}
      {showPicker && (
        <DirPickerModal
          title="Select workflow folder"
          confirmLabel="Scan folder"
          initialPath={dir}
          onConfirm={(path) => { setDirOverride(path); setShowPicker(false); }}
          onCancel={() => setShowPicker(false)}
        />
      )}
    </div>
  );
}
