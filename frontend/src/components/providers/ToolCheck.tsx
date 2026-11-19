import { useState } from "react";
import { useIsMutating, useMutation, useQueryClient } from "@tanstack/react-query";
import { providersApi, type ToolProbe } from "../../api/providers";
import { apiErrorDetail } from "../../utils/apiError";

/** Whether one model on one provider may call tools, and the button that checks it
 *  (`POST /providers/{id}/probe-tools`, docs/dev/analyst/tools.md § The probe). Used on
 *  Settings → Analyst for a provider's default model and in "What gets sent" for the
 *  chat's own model.
 *
 *  The check can take minutes, so its pending state is read from the mutation cache by
 *  key, not from this instance: leaving the page and coming back still shows
 *  "Checking…", and a second click cannot start a second check. A check that reaches no
 *  verdict says why, until a newer verdict replaces it. */
export default function ToolCheck({ providerId, model, verdict }: {
  providerId: string;
  model: string;
  verdict: ToolProbe | undefined;
}) {
  const qc = useQueryClient();
  const mutationKey = ["probe-tools", providerId, model];
  const running = useIsMutating({ mutationKey }) > 0;
  // The reason a check found no verdict, and the verdict it was made against: a newer
  // verdict (from another check) makes it stale.
  const [failure, setFailure] = useState<{ message: string; against: string | undefined } | null>(null);

  const check = useMutation({
    mutationKey,
    mutationFn: () => providersApi.probeTools(providerId, model),
    onMutate: () => setFailure(null),
    // A new verdict changes which tools every chat on this provider and model is offered.
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: ["providers"] }),
      qc.invalidateQueries({ queryKey: ["chat-preview"] }),
    ]),
    onError: (err) => setFailure({ message: apiErrorDetail(err, "The check could not finish."), against: verdict?.checked_at }),
  });
  const showFailure = failure && failure.against === verdict?.checked_at ? failure.message : null;

  return (
    <div style={{ fontSize: 12.5 }} data-testid="tool-check">
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ overflowWrap: "anywhere" }}>
          Tools for <span className="mono">{model}</span>
        </span>
        <span className={`badge ${verdict ? (verdict.ok ? "good" : "warn") : ""}`} data-testid="tool-check-verdict">
          {verdict ? (verdict.ok ? "supported" : "not supported") : "not checked"}
        </span>
        <button className="btn sm" disabled={running} onClick={() => check.mutate()}>
          {running ? "Checking…" : "Check tool support"}
        </button>
      </div>
      {verdict && (
        <div style={{ fontSize: 11.5, color: "var(--fg-dim)", marginTop: 4 }}>
          {verdict.detail} Checked {new Date(verdict.checked_at).toLocaleString()}.
        </div>
      )}
      {showFailure && (
        <div role="alert" style={{ fontSize: 11.5, color: "var(--warn)", marginTop: 4 }}>
          No verdict, so nothing changed: {showFailure}
        </div>
      )}
    </div>
  );
}
