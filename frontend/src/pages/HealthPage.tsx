import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { MessageSquare, RefreshCw } from "lucide-react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { datasetsApi } from "../api/datasets";
import { filterSpecParams, healthApi, type Finding, type HealthReport, type Severity } from "../api/health";
import { datasetNotesKey, notesApi } from "../api/notes";
import { apiErrorDetail } from "../utils/apiError";
import FilteredImagesModal from "../components/common/FilteredImagesModal";
import ModalShell from "../components/common/ModalShell";
import FindingCard from "../components/health/FindingCard";
import NotesEditor from "../components/health/NotesEditor";
import { SEVERITY_CLASS, SEVERITY_LABEL } from "../components/health/severity";
import { useFindingActions } from "../components/health/useFindingActions";
import { useHealthReport } from "../components/health/useHealthReport";
import { useOpenChatBeside } from "../hooks/useOpenChatBeside";

/** The Health page (M12): what Crucible finds wrong with a dataset, with no AI.
 *  The body is keyed on the dataset so its drafts and open dialogs reset on a switch
 *  without the pane remounting the route. */
export default function HealthPage() {
  const datasetId = usePaneDatasetId();
  if (!datasetId) return null;
  return <HealthBody key={datasetId} datasetId={datasetId} />;
}

function HealthBody({ datasetId }: { datasetId: string }) {
  const qc = useQueryClient();
  const { openInGallery, openFix } = useFindingActions(datasetId);
  const openChatBeside = useOpenChatBeside();
  const [showing, setShowing] = useState<Finding | null>(null);
  const [dismissing, setDismissing] = useState<Finding | null>(null);

  const { data: dataset } = useQuery({ queryKey: ["dataset", datasetId], queryFn: () => datasetsApi.get(datasetId) });
  const { data: report, isLoading, isFetching, error } = useHealthReport(datasetId);

  const afterChange = () => {
    qc.invalidateQueries({ queryKey: ["dataset-health", datasetId] });
    qc.invalidateQueries({ queryKey: datasetNotesKey(datasetId) });
  };
  const undismiss = useMutation({
    mutationFn: (noteId: string) => notesApi.dataset(datasetId).remove(noteId),
    onSuccess: afterChange,
    onError: (err) => toast.error(apiErrorDetail(err, "Could not restore the finding")),
  });

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      <div className="page-h" style={{ marginBottom: 16 }}>
        <div>
          <h1>Health</h1>
          <p>What Crucible finds in this dataset by itself, with no AI. Dismiss what you mean to keep.</p>
        </div>
        <div className="phactions">
          {report && <SummaryBadges summary={report.summary} />}
          <button className="btn sm" onClick={() => openChatBeside(datasetId)} title="Open the analyst chat beside this page">
            <MessageSquare size={13} /> Ask the analyst
          </button>
          <button
            className="icon-btn"
            title="Check again"
            aria-label="Check again"
            disabled={isFetching}
            onClick={() => qc.invalidateQueries({ queryKey: ["dataset-health", datasetId] })}
          >
            <RefreshCw size={15} />
          </button>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12.5, color: "var(--fg-mute)", marginBottom: 18 }}>
        <div>
          <strong style={{ color: "var(--fg)" }}>Goal:</strong>{" "}
          {dataset?.goal ? dataset.goal : <em>none set. Add one with Edit on the Datasets page; it tells the analyst what the images are for.</em>}
          {dataset?.target_resolution ? <span> · target {dataset.target_resolution}px</span> : null}
        </div>
        {report && <BaselineLine report={report} />}
      </div>

      {error ? (
        <div className="card" style={{ padding: 16, color: "var(--bad)" }}>{apiErrorDetail(error, "Could not build the health report")}</div>
      ) : isLoading || !report ? (
        <div style={{ color: "var(--fg-mute)", fontSize: 13, padding: "40px 0" }}>Checking the dataset…</div>
      ) : (
        <>
          {report.findings.length === 0 ? (
            <div className="card" style={{ padding: 16, fontSize: 13 }}>No findings. Nothing here needs attention.</div>
          ) : (
            <ul aria-label="Findings" style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
              {report.findings.map((f) => (
                <FindingCard
                  key={f.id}
                  finding={f}
                  onShow={() => setShowing(f)}
                  onOpenGallery={() => openInGallery(f)}
                  onFix={() => openFix(f)}
                  onDismiss={() => setDismissing(f)}
                />
              ))}
            </ul>
          )}

          {report.resolved.length > 0 && (
            <p style={{ fontSize: 12.5, color: "var(--good)", marginTop: 12 }}>
              Resolved since {report.baseline?.version_name ?? "the snapshot"}:{" "}
              {report.resolved.map((r) => `${r.id.replace(/_/g, " ")} (was ${r.was})`).join(", ")}
            </p>
          )}

          {report.dismissed.length > 0 && (
            <details style={{ marginTop: 16 }} data-testid="dismissed">
              <summary style={{ cursor: "pointer", fontSize: 13, color: "var(--fg-mute)" }}>
                Dismissed ({report.dismissed.length})
              </summary>
              <ul style={{ listStyle: "none", margin: "8px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
                {report.dismissed.map((f) => (
                  <li key={f.id} className="card" style={{ padding: "8px 12px", display: "flex", gap: 10, alignItems: "center", fontSize: 12.5 }}>
                    <span style={{ flex: 1 }}>
                      <strong>{f.title}</strong> · {f.count.toLocaleString()}
                      <span style={{ color: "var(--fg-mute)" }}>
                        {" "}— “{f.dismissal?.reason}”{f.dismissal?.permanent ? " (for good)" : ""}
                      </span>
                    </span>
                    {f.dismissal && (
                      <button className="btn sm ghost" onClick={() => undismiss.mutate(f.dismissal!.note_id)}>Restore</button>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}

      <h2 style={{ fontSize: 15, fontWeight: 600, margin: "28px 0 4px" }}>Notes</h2>
      <p style={{ fontSize: 12.5, color: "var(--fg-mute)", margin: "0 0 12px" }}>
        What you want remembered about this dataset. The analyst will read these before it advises.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 24 }}>
        <NotesEditor scope="dataset" datasetId={datasetId} />
        <NotesEditor scope="app" />
      </div>

      {showing?.filter_spec && (
        <FilteredImagesModal
          title={showing.title}
          params={filterSpecParams(showing.filter_spec)}
          datasetId={datasetId}
          onClose={() => setShowing(null)}
        />
      )}
      {dismissing && (
        <DismissDialog
          finding={dismissing}
          onClose={() => setDismissing(null)}
          onDismiss={async (reason, permanent) => {
            try {
              await healthApi.dismiss(datasetId, dismissing.id, reason, permanent);
              setDismissing(null);
              afterChange();
            } catch (err) {
              toast.error(apiErrorDetail(err, "Could not dismiss the finding"));
            }
          }}
        />
      )}
    </div>
  );
}

function SummaryBadges({ summary }: { summary: Record<Severity, number> }) {
  return (
    <span role="group" style={{ display: "flex", gap: 6 }} aria-label="Findings by severity">
      {(["error", "warn", "info"] as const).map((s) => (
        <span key={s} className={`badge dot ${summary[s] ? SEVERITY_CLASS[s] : ""}`}>
          {summary[s]} {SEVERITY_LABEL[s].toLowerCase()}{summary[s] === 1 ? "" : "s"}
        </span>
      ))}
    </span>
  );
}

function BaselineLine({ report }: { report: HealthReport }) {
  const b = report.baseline;
  if (!b) return <div>No snapshot yet. Take one on the Versions page to see what changes.</div>;
  const name = b.version_name ?? "the last snapshot";
  if (b.missing) return <div>Compared with {name}: no baseline, since it was taken before findings were recorded.</div>;
  return <div>Compared with {name}, taken {new Date(b.created_at).toLocaleString()}.</div>;
}

function DismissDialog({ finding, onClose, onDismiss }: {
  finding: Finding;
  onClose: () => void;
  onDismiss: (reason: string, permanent: boolean) => Promise<void>;
}) {
  const uid = useId();
  const [reason, setReason] = useState("");
  const [permanent, setPermanent] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try { await onDismiss(reason, permanent); } finally { setBusy(false); }
  };
  return (
    <ModalShell label={`Dismiss ${finding.title}`} onClose={onClose}>
      <h3>Dismiss “{finding.title}”</h3>
      <p>The reason is saved as a preference in this dataset's notes, so the analyst knows why.</p>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 16 }}>
        <div>
          <label className="label" htmlFor={`${uid}-reason`}>Reason</label>
          <textarea
            id={`${uid}-reason`}
            className="input"
            rows={2}
            autoFocus
            maxLength={500}
            placeholder="e.g. zxc is the trigger word"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5 }}>
          <input type="checkbox" checked={permanent} onChange={(e) => setPermanent(e.target.checked)} />
          Keep it dismissed even if it gets worse
        </label>
        {!permanent && (
          <span style={{ fontSize: 11.5, color: "var(--fg-dim)" }}>
            Otherwise it comes back if its count more than doubles (now {finding.count.toLocaleString()}).
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={submit} disabled={busy}>Dismiss</button>
      </div>
    </ModalShell>
  );
}
