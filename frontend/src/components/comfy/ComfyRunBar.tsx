import { useQuery } from "@tanstack/react-query";
import { datasetsApi } from "../../api/datasets";
import type { JobProgress } from "../../types";

interface Props {
  datasetId: string;
  pendingCount: number;
  selectedCount: number;
  totalCount: number;
  subfolder: string;
  onSubfolderChange: (v: string) => void;
  setCaption: boolean;
  onSetCaptionChange: (v: boolean) => void;
  onRunPending: () => void;
  onRunSelected: () => void;
  /** Only on the Rows tab: elsewhere the selection it would run is out of sight. */
  showRunSelected: boolean;
  onRunAll: () => void;
  isRunning: boolean;
  jobProgress: JobProgress | undefined;
}

export default function ComfyRunBar({
  datasetId, pendingCount, selectedCount, totalCount, subfolder, onSubfolderChange,
  setCaption, onSetCaptionChange, onRunPending, onRunSelected, showRunSelected, onRunAll, isRunning, jobProgress,
}: Props) {
  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId),
    enabled: !!datasetId,
  });

  // A strip, not a panel: it sits in the page's sticky header on every tab.
  return (
    <div data-testid="comfy-run-bar">
      <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>Run</h3>
        <div style={{ flex: 1 }} />
        <label
          style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", alignItems: "center", gap: 6 }}
          title="Per-row folders nest under this"
        >
          Base folder
          <select
            className="select" style={{ height: 30, fontSize: 12 }}
            value={subfolder} onChange={(e) => onSubfolderChange(e.target.value)} disabled={isRunning}
          >
            <option value="">(root)</option>
            {subfolders.filter((sf) => sf.path).map((sf) => (
              <option key={sf.path} value={sf.path}>{sf.path}</option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input
            type="checkbox" className="checkbox" checked={setCaption}
            onChange={(e) => onSetCaptionChange(e.target.checked)} disabled={isRunning}
          />
          Prompt as caption
        </label>
        {showRunSelected && (
          <button className="btn ghost" onClick={onRunSelected} disabled={isRunning || selectedCount === 0}>
            Run selected ({selectedCount})
          </button>
        )}
        <button
          className="btn ghost" onClick={onRunAll} disabled={isRunning || totalCount === 0}
          title="Run every row regardless of status — re-runs completed prompts and regenerates images"
        >
          Run all ({totalCount})
        </button>
        <button className="btn primary" onClick={onRunPending} disabled={isRunning || pendingCount === 0}>
          {isRunning ? "Running…" : `Run pending (${pendingCount})`}
        </button>
      </div>
      {jobProgress && (
        <div style={{ marginTop: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12, color: "var(--fg-mute)", marginBottom: 4 }}>
            <span>{jobProgress.message}{jobProgress.current_item ? ` — ${jobProgress.current_item}` : ""}</span>
            <span className="mono">{jobProgress.done}/{jobProgress.total}</span>
          </div>
          <div style={{ height: 4, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${jobProgress.percent ?? 0}%`, background: "linear-gradient(90deg, var(--accent-2), var(--accent))", transition: "width .4s" }} />
          </div>
        </div>
      )}
    </div>
  );
}
