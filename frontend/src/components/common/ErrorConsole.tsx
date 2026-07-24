import { useEffect } from "react";
import { useErrorConsoleStore, errorTypeLabel, formatErrorsForCopy } from "../../store/errorConsoleStore";

function formatTimestamp(d: Date): string {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function ErrorConsole() {
  const { errors, isOpen, addError, clearErrors, close } = useErrorConsoleStore();

  useEffect(() => {
    const handleError = (e: ErrorEvent) => {
      addError({
        type: "error",
        message: e.message || "Unknown error",
        source: e.filename,
        line: e.lineno,
        col: e.colno,
        stack: e.error?.stack,
      });
    };

    const handleRejection = (e: PromiseRejectionEvent) => {
      const reason = e.reason;
      addError({
        type: "unhandledrejection",
        message: reason?.message ?? String(reason),
        stack: reason?.stack,
      });
    };

    window.addEventListener("error", handleError);
    window.addEventListener("unhandledrejection", handleRejection);
    return () => {
      window.removeEventListener("error", handleError);
      window.removeEventListener("unhandledrejection", handleRejection);
    };
  }, [addError]);

  if (!isOpen || errors.length === 0) return null;

  return (
    <div
      style={{
        position: "fixed",
        bottom: 0,
        left: "var(--sidebar-w)",
        right: 0,
        height: 280,
        zIndex: 9000,
        background: "var(--surface-1)",
        borderTop: "1px solid var(--bad)",
        display: "flex",
        flexDirection: "column",
        fontFamily: "var(--font-mono, monospace)",
        fontSize: 12,
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 10px",
          borderBottom: "1px solid var(--line)",
          flexShrink: 0,
          background: "var(--bg)",
        }}
      >
        <span style={{ color: "var(--bad)", fontSize: 10 }}>●</span>
        <span style={{ fontWeight: 600, color: "var(--fg)", flex: 1 }}>
          Error Console ({errors.length})
        </span>
        <button
          className="btn sm"
          onClick={() => navigator.clipboard.writeText(formatErrorsForCopy(errors)).catch(() => {})}
          title="Copy all errors to clipboard"
        >
          Copy Errors
        </button>
        <button className="btn sm" onClick={clearErrors} title="Clear all errors">
          Clear
        </button>
        <button className="btn sm" onClick={close} title="Close panel">
          ✕
        </button>
      </div>

      {/* Error list */}
      <div style={{ overflowY: "auto", flex: 1, padding: "6px 0" }}>
        {errors.map((entry) => (
          <div
            key={entry.id}
            style={{
              padding: "6px 12px",
              borderBottom: "1px solid var(--line)",
            }}
          >
            {/* Meta row */}
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 3 }}>
              <span style={{ color: "var(--fg-dim)", fontSize: 11 }}>
                {formatTimestamp(entry.timestamp)}
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
              <details style={{ marginTop: 4 }}>
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
