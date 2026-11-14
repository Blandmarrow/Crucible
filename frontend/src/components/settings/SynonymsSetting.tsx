import { useEffect, useRef } from "react";
import toast from "react-hot-toast";
import { dictionaryBusy, useDictionaryActions, useDictionaryStatus } from "../../hooks/useDictionaryStatus";
import { useUiPrefsStore } from "../../store/uiPrefsStore";
import { apiErrorDetail } from "../../utils/apiError";

const mb = (bytes: number | null) => `${((bytes ?? 0) / 1_048_576).toFixed(0)} MB`;

/**
 * Settings → UI Behavior → Synonym suggestions (#52). The toggle is a per-browser
 * preference (`SYNONYMS_ENABLED_KEY`); the dictionary it needs is installed once on
 * the server, so turning it on starts that install when it is missing.
 */
export default function SynonymsSetting() {
  const enabled = useUiPrefsStore((s) => s.synonymsEnabled);
  const setEnabled = useUiPrefsStore((s) => s.setSynonymsEnabled);
  const { data: status } = useDictionaryStatus();
  const { install, remove } = useDictionaryActions();
  const busy = dictionaryBusy(status);

  const startInstall = () =>
    install.mutate(undefined, {
      onError: (e) => toast.error(apiErrorDetail(e, "Could not start the download")),
    });

  // Say when an install this page watched has finished, either way.
  const prev = useRef(status?.state);
  useEffect(() => {
    const was = prev.current;
    prev.current = status?.state;
    if (was !== "downloading" && was !== "converting") return;
    if (status?.state === "ready") toast.success("Dictionary installed");
    if (status?.state === "failed") toast.error(status.error ?? "The dictionary download failed");
  }, [status?.state, status?.error]);

  return (
    <div data-testid="synonyms-setting">
      <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 10 }}>Caption editor</div>
      <label style={{ display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer" }}>
        <input
          type="checkbox"
          className="checkbox"
          style={{ marginTop: 2 }}
          checked={enabled}
          data-testid="synonyms-toggle"
          onChange={(e) => {
            const next = e.target.checked;
            setEnabled(next);
            toast.success("Preference saved");
            if (next && !status?.installed && !busy) startInstall();
          }}
        />
        <div>
          <div style={{ fontSize: 13 }}>Synonym suggestions</div>
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "2px 0 0" }}>
            Select a word in a caption to see other words for it, grouped by meaning, each with a
            short definition. Click one to use it. Off by default. Turning it on downloads an
            English dictionary once (about 10 MB); after that it works offline.
          </p>
        </div>
      </label>

      {(enabled || status?.installed || busy) && status && (
        <div style={{ margin: "8px 0 0 26px", fontSize: 12 }} data-testid="synonyms-status">
          {busy && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ color: "var(--fg-mute)" }}>{status.message || "Installing…"}</span>
              <div style={{ height: 4, background: "var(--surface-3)", borderRadius: 2, overflow: "hidden" }}>
                <div
                  style={{
                    height: "100%",
                    width: status.percent >= 0 ? `${status.percent}%` : "100%",
                    background: "var(--accent)",
                    opacity: status.percent >= 0 ? 1 : 0.4,
                  }}
                />
              </div>
            </div>
          )}
          {status.state === "failed" && (
            <span style={{ color: "var(--bad)" }} role="alert">
              {status.error}{" "}
              <button type="button" className="btn-ghost btn-sm" onClick={startInstall} disabled={install.isPending}>
                Retry
              </button>
            </span>
          )}
          {status.state === "missing" && (
            <span style={{ color: "var(--fg-mute)" }}>
              The dictionary is not installed.{" "}
              <button type="button" className="btn-ghost btn-sm" onClick={startInstall} disabled={install.isPending}>
                Download it
              </button>
            </span>
          )}
          {status.state === "ready" && (
            <span style={{ color: "var(--fg-mute)" }}>
              Installed ({mb(status.size_bytes)}).{" "}
              <button
                type="button"
                className="btn-ghost btn-sm"
                disabled={remove.isPending}
                onClick={() =>
                  remove.mutate(undefined, {
                    onSuccess: () => toast.success("Dictionary removed"),
                    onError: (e) => toast.error(apiErrorDetail(e, "Could not remove the dictionary")),
                  })
                }
              >
                Remove
              </button>
            </span>
          )}
          <p style={{ color: "var(--fg-dim)", margin: "6px 0 0" }}>
            Dictionary:{" "}
            <a href="https://en-word.net/" target="_blank" rel="noreferrer" style={{ color: "var(--accent-text)" }}>
              Open English WordNet 2025
            </a>{" "}
            (CC BY 4.0), derived from Princeton WordNet. The licence and notices are kept beside the
            installed file, and in <code>THIRD_PARTY_NOTICES.md</code>.
          </p>
        </div>
      )}
    </div>
  );
}
