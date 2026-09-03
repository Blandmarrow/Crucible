import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { jobsApi } from "../api/jobs";
import { useErrorConsoleStore, errorTypeLabel, formatErrorsForCopy } from "../store/errorConsoleStore";
import type { Job } from "../types";

// ── Helpers ──────────────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

function duration(job: Job): string | null {
  if (!job.started_at || !job.finished_at) return null;
  const ms = new Date(job.finished_at).getTime() - new Date(job.started_at).getTime();
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

/** A job that lost items but still returned normally is marked `completed` with
 *  `error_msg` unset, so the row would otherwise read as a clean success. Keyed on
 *  the generic `failed_count` name that import jobs already write, so they light up
 *  too. `result_data` is `Record<string, unknown>`, hence the narrowing here. */
function partialFailure(job: Job): string | null {
  const data = job.result_data;
  if (!data) return null;
  const failed = data.failed_count;
  if (typeof failed !== "number" || failed <= 0) return null;
  const summary = data.failure_summary;
  return typeof summary === "string" && summary ? summary : `${failed} item${failed !== 1 ? "s" : ""} failed`;
}

function PartialFailureLine({ job }: { job: Job }) {
  const text = partialFailure(job);
  if (!text) return null;
  return <div style={{ marginTop: 5, color: "var(--warn)", fontSize: 12 }}>{text}</div>;
}

function StatusBadge({ status }: { status: Job["status"] }) {
  const styles: Record<string, { color: string; label: string }> = {
    pending:   { color: "var(--fg-mute)",  label: "pending" },
    running:   { color: "var(--accent)",   label: "running" },
    completed: { color: "var(--good)",      label: "done" },
    failed:    { color: "var(--bad)",      label: "failed" },
    cancelled: { color: "var(--fg-dim)",   label: "cancelled" },
  };
  const s = styles[status] ?? styles.pending;
  return (
    <span
      style={{
        fontSize: 10,
        padding: "2px 6px",
        borderRadius: 3,
        border: "1px solid var(--line-2)",
        background: "var(--surface-2)",
        color: s.color,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        fontFamily: "var(--font-mono, monospace)",
        flexShrink: 0,
      }}
    >
      {s.label}
    </span>
  );
}

// ── History tab ───────────────────────────────────────────────────────────────

function HistoryTab() {
  const [filter, setFilter] = useState("");
  const { data: jobs = [], isFetching, refetch } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => jobsApi.list(200),
    staleTime: 10_000,
  });

  const visible = filter.trim()
    ? jobs.filter((j) => {
        const q = filter.toLowerCase();
        return (
          (j.label ?? j.job_type).toLowerCase().includes(q) ||
          j.job_type.toLowerCase().includes(q) ||
          (j.dataset_id ?? "").toLowerCase().includes(q)
        );
      })
    : jobs;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: "16px 20px" }}>
      {/* Toolbar */}
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input
          className="input"
          style={{ flex: 1, maxWidth: 320 }}
          placeholder="Filter by label, type, or dataset…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <button className="btn sm" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
        <span style={{ color: "var(--fg-dim)", fontSize: 12, marginLeft: 4 }}>
          {visible.length} job{visible.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Job list */}
      {visible.length === 0 ? (
        <div style={{ color: "var(--fg-mute)", fontSize: 13, padding: "24px 0" }}>
          No jobs found.
        </div>
      ) : (
        <div
          style={{
            border: "1px solid var(--line)",
            borderRadius: "var(--r)",
            overflow: "hidden",
          }}
        >
          {visible.map((job, i) => (
            <div
              key={job.id}
              style={{
                padding: "10px 14px",
                borderBottom: i < visible.length - 1 ? "1px solid var(--line)" : undefined,
                background: "var(--surface-1)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <StatusBadge status={job.status} />

                <span style={{ fontWeight: 500, color: "var(--fg)", fontSize: 13, flex: 1 }}>
                  {job.label ?? job.job_type}
                </span>

                {job.dataset_id && (
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--fg-dim)",
                      background: "var(--surface-3)",
                      padding: "1px 6px",
                      borderRadius: 3,
                      border: "1px solid var(--line)",
                      fontFamily: "var(--font-mono, monospace)",
                      flexShrink: 0,
                    }}
                  >
                    {job.dataset_id.slice(0, 8)}
                  </span>
                )}

                <span
                  style={{ color: "var(--fg-dim)", fontSize: 11, flexShrink: 0 }}
                  title={new Date(job.created_at).toLocaleString()}
                >
                  {relativeTime(job.created_at)}
                </span>

                {duration(job) && (
                  <span style={{ color: "var(--fg-dim)", fontSize: 11, flexShrink: 0 }}>
                    · {duration(job)}
                  </span>
                )}

                {job.total_items > 0 && (
                  <span style={{ color: "var(--fg-mute)", fontSize: 11, flexShrink: 0, fontFamily: "var(--font-mono, monospace)" }}>
                    {job.done_items}/{job.total_items}
                  </span>
                )}
              </div>

              {job.error_msg && (
                <div
                  style={{
                    marginTop: 5,
                    color: "var(--bad)",
                    fontSize: 12,
                    fontFamily: "var(--font-mono, monospace)",
                  }}
                >
                  {job.error_msg}
                </div>
              )}

              <PartialFailureLine job={job} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Errors tab ────────────────────────────────────────────────────────────────

function ErrorsTab() {
  const { errors, clearErrors } = useErrorConsoleStore();

  if (errors.length === 0) {
    return (
      <div style={{ padding: "40px 20px", color: "var(--fg-mute)", fontSize: 13 }}>
        No JS errors captured this session.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: "16px 20px" }}>
      {/* Toolbar */}
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <span style={{ color: "var(--fg-dim)", fontSize: 12, flex: 1 }}>
          {errors.length} error{errors.length !== 1 ? "s" : ""} captured this session
        </span>
        <button
          className="btn sm"
          onClick={() => navigator.clipboard.writeText(formatErrorsForCopy(errors)).catch(() => {})}
        >
          Copy Errors
        </button>
        <button className="btn sm" onClick={clearErrors}>
          Clear
        </button>
      </div>

      {/* Error list */}
      <div
        style={{
          border: "1px solid var(--line)",
          borderRadius: "var(--r)",
          overflow: "hidden",
          fontFamily: "var(--font-mono, monospace)",
          fontSize: 12,
        }}
      >
        {errors.map((entry, i) => (
          <div
            key={entry.id}
            style={{
              padding: "10px 14px",
              borderBottom: i < errors.length - 1 ? "1px solid var(--line)" : undefined,
              background: "var(--surface-1)",
            }}
          >
            {/* Meta */}
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
              <span style={{ color: "var(--fg-dim)", fontSize: 11 }}>
                {entry.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
              </span>
              <span
                style={{
                  fontSize: 10,
                  padding: "1px 5px",
                  borderRadius: 3,
                  border: "1px solid var(--line-2)",
                  background: "var(--surface-2)",
                  color: "var(--bad)",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                {errorTypeLabel(entry.type)}
              </span>
            </div>

            {/* Message */}
            <div style={{ color: "var(--fg)", fontWeight: 600, wordBreak: "break-word" }}>
              {entry.message}
            </div>

            {/* Source */}
            {entry.source && (
              <div style={{ color: "var(--fg-mute)", marginTop: 2, fontSize: 11 }}>
                {entry.source}
                {entry.line != null && `:${entry.line}`}
                {entry.col != null && `:${entry.col}`}
              </div>
            )}

            {/* Stack trace */}
            {entry.stack && (
              <details style={{ marginTop: 6 }}>
                <summary
                  style={{
                    cursor: "pointer",
                    color: "var(--fg-mute)",
                    fontSize: 11,
                    userSelect: "none",
                  }}
                >
                  Stack trace
                </summary>
                <pre
                  style={{
                    margin: "4px 0 0",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    color: "var(--fg-dim)",
                    fontSize: 11,
                    lineHeight: 1.5,
                  }}
                >
                  {entry.stack}
                </pre>
              </details>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

type Tab = "history" | "errors";

export default function LogsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("history");
  const errorCount = useErrorConsoleStore((s) => s.errors.length);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Page header */}
      <div
        style={{
          padding: "16px 20px 0",
          borderBottom: "1px solid var(--line)",
          background: "var(--bg)",
          flexShrink: 0,
        }}
      >
        <h1
          style={{
            fontSize: 16,
            fontWeight: 600,
            color: "var(--fg)",
            margin: "0 0 12px",
          }}
        >
          Logs
        </h1>

        <div className="tabs" style={{ gap: 0 }}>
          <button
            className={`tab${activeTab === "history" ? " active" : ""}`}
            onClick={() => setActiveTab("history")}
          >
            History
          </button>
          <button
            className={`tab${activeTab === "errors" ? " active" : ""}`}
            onClick={() => setActiveTab("errors")}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            Errors
            {errorCount > 0 && (
              <span
                style={{
                  fontSize: 10,
                  padding: "1px 5px",
                  borderRadius: 10,
                  background: "var(--bad)",
                  color: "#fff",
                  fontFamily: "var(--font-mono, monospace)",
                  lineHeight: 1.4,
                }}
              >
                {errorCount}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {activeTab === "history" ? <HistoryTab /> : <ErrorsTab />}
      </div>
    </div>
  );
}
