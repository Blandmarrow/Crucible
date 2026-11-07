import { RUN_STAGE_LABELS, type RunPlanReport } from "../../api/runs";

/** Folders the report lists before "…and N more". */
const SHOWN_FOLDERS = 12;

function bytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * The dry run's answer under the start form (M6 §6): every check the run would fail or
 * warn on, then what its first pass would do. Start runs the same checks and refuses on
 * the first error, so a clean report here is a start that will not be refused.
 * `stale` says the form has changed since, so the report may no longer hold.
 */
export default function DryRunReport({ report, stale, onClose }: {
  report: RunPlanReport; stale: boolean; onClose: () => void;
}) {
  const errors = report.checks.filter((c) => c.level === "error");
  const warnings = report.checks.filter((c) => c.level === "warning");
  const loopNote = report.rows_planned < report.rows_before_clamp
    ? ` (the image cap leaves room for ${report.rows_planned} of ${report.rows_before_clamp})`
    : "";
  return (
    <div className="panel" data-testid="dry-run-report" style={{ margin: "0 0 14px", padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div className="row-flex" style={{ gap: 10 }}>
        <h4 style={{ margin: 0 }}>Dry run</h4>
        <span style={{ fontSize: 12, color: report.ok ? "var(--good)" : "var(--bad)" }}>
          {report.ok ? "Ready to start" : `${errors.length} problem${errors.length !== 1 ? "s" : ""} would stop the start`}
        </span>
        {stale && <span style={{ fontSize: 12, color: "var(--warn)" }}>The form has changed since. Run it again.</span>}
        <div style={{ flex: 1 }} />
        <button className="btn sm ghost" onClick={onClose}>Close</button>
      </div>

      {errors.map((c, i) => (
        <div key={`e${i}`} style={{ fontSize: 12.5, color: "var(--bad)" }}>✕ {c.message}</div>
      ))}
      {warnings.map((c, i) => (
        <div key={`w${i}`} style={{ fontSize: 12.5, color: "var(--warn)" }}>! {c.message}</div>
      ))}
      {report.checks.length === 0 && <div style={{ fontSize: 12.5, color: "var(--good)" }}>✓ Every check passed.</div>}

      <div style={{ fontSize: 12.5, color: "var(--fg-mute)" }}>
        Stages: {report.stages.map((s) => RUN_STAGE_LABELS[s.key] ?? s.key).join(" → ")}
      </div>
      <div style={{ fontSize: 12.5 }}>
        First pass: {report.rows_planned} row{report.rows_planned !== 1 ? "s" : ""} to generate{loopNote}, about{" "}
        {report.images_estimate} image{report.images_estimate !== 1 ? "s" : ""}
        {report.has_blueprint && ` · ${report.rows_to_add} new row${report.rows_to_add !== 1 ? "s" : ""} from the blueprint`}
        {report.disk_estimate_bytes != null && ` · about ${bytes(report.disk_estimate_bytes)} on disk`}
      </div>
      {(report.rewrite_calls != null || report.caption_calls != null) && (
        <div style={{ fontSize: 12.5 }}>
          {report.rewrite_calls != null && `Rewrite: about ${report.rewrite_calls} call${report.rewrite_calls !== 1 ? "s" : ""}${report.rewrite_billed ? " (billed)" : ""}`}
          {report.rewrite_calls != null && report.caption_calls != null && " · "}
          {report.caption_calls != null && `Caption: about ${report.caption_calls} call${report.caption_calls !== 1 ? "s" : ""}${report.caption_billed ? " (billed)" : ""}`}
        </div>
      )}

      {report.folders.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {report.folders.slice(0, SHOWN_FOLDERS).map((f) => (
            <div key={f.path} style={{ fontSize: 12, color: "var(--fg-mute)", paddingLeft: 12 }}>
              {f.path}: {f.kept} of {f.target} kept · {f.rows} row{f.rows !== 1 ? "s" : ""} in the first pass
            </div>
          ))}
          {report.folder_count > SHOWN_FOLDERS && (
            <div style={{ fontSize: 12, color: "var(--fg-mute)", paddingLeft: 12 }}>
              …and {report.folder_count - SHOWN_FOLDERS} more folders
            </div>
          )}
        </div>
      )}
    </div>
  );
}
