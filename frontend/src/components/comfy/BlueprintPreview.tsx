import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { comfyApi, type BlueprintValue } from "../../api/comfy";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { apiErrorDetail } from "../../utils/apiError";

const PREVIEW_DEBOUNCE_MS = 350;

interface Props {
  planId: string;
  datasetId: string;
  /** The form as it is right now; the preview and Apply follow it after a short pause. */
  value: BlueprintValue;
  hasPromptPin: boolean;
}

const cell = { padding: "4px 8px", textAlign: "left" as const, fontSize: 12 };
const num = { ...cell, textAlign: "right" as const };

export default function BlueprintPreview({ planId, datasetId, value, hasPromptPin }: Props) {
  const qc = useQueryClient();
  const [allowOverCap, setAllowOverCap] = useState(false);
  const [deleteOrphans, setDeleteOrphans] = useState(false);

  const liveJSON = JSON.stringify(value);
  const debouncedJSON = useDebouncedValue(liveJSON, PREVIEW_DEBOUNCE_MS);
  const lagging = liveJSON !== debouncedJSON;

  const previewKey = ["comfy", "blueprint-preview", planId, debouncedJSON, allowOverCap] as const;
  const { data: preview, isFetching, isPlaceholderData, isError, error, refetch } = useQuery({
    queryKey: previewKey,
    queryFn: ({ signal }) =>
      comfyApi.previewBlueprint(planId, { ...(JSON.parse(debouncedJSON) as BlueprintValue), allow_over_cap: allowOverCap }, signal),
    placeholderData: keepPreviousData,
    staleTime: 0,
  });

  const applyMutation = useMutation({
    // Exactly what the displayed preview was computed from, not the live form.
    mutationFn: (vars: { body: BlueprintValue; total: number; deleteOrphans: boolean }) =>
      comfyApi.applyBlueprint(planId, {
        ...vars.body, confirm_total: vars.total, allow_over_cap: allowOverCap, delete_orphan_pending: vars.deleteOrphans,
      }),
    onSuccess: (data) => {
      const deleted = data.deleted_rows > 0 ? `, deleted ${data.deleted_rows} pending` : "";
      toast.success(`Added ${data.created} row${data.created !== 1 ? "s" : ""} across ${data.leaves} folder${data.leaves !== 1 ? "s" : ""}${deleted}`);
      setDeleteOrphans(false);
      qc.invalidateQueries({ queryKey: ["comfy", "rows", planId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      qc.invalidateQueries({ queryKey: ["comfy", "prompts", planId] });
      qc.invalidateQueries({ queryKey: ["comfy", "blueprint-preview", planId] });
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Failed to apply the blueprint"));
      if ((err as { response?: { status?: number } })?.response?.status === 409) {
        qc.invalidateQueries({ queryKey: ["comfy", "blueprint-preview", planId] });
      }
    },
  });

  if (isError && !preview) {
    return (
      <div className="panel" style={{ marginBottom: 12 }}>
        <div className="panel-h"><h3>Preview</h3></div>
        <div className="panel-b">
          <p style={{ color: "var(--bad)", fontSize: 12, margin: "0 0 8px" }}>
            {apiErrorDetail(error, "Could not compute the preview.")}
          </p>
          <button className="btn sm" onClick={() => refetch()}>Retry</button>
        </div>
      </div>
    );
  }

  const stale = lagging || isFetching || isPlaceholderData;
  const errors = preview?.errors ?? [];
  const orphans = preview?.orphans;
  const overCapBlocked = !!preview?.over_cap && !allowOverCap;
  const blockedReason = !hasPromptPin
    ? "Mark a pinned parameter as the prompt first (Workflow & Pins)"
    : errors.length > 0 ? "Fix the errors first"
    : overCapBlocked ? "Over the row cap: allow a larger expansion first"
    : stale ? "Waiting for the preview" : "";
  const canApply = !!preview && !blockedReason && !applyMutation.isPending;
  const orphanPending = orphans?.pending ?? 0;

  return (
    <div className="panel" style={{ marginBottom: 12 }} data-testid="blueprint-preview">
      <div className="panel-h" style={{ flexWrap: "wrap", gap: 8 }}>
        <h3>Preview</h3>
        <span style={{ fontSize: 12, color: "var(--fg-mute)", opacity: stale ? 0.6 : 1 }} data-testid="blueprint-totals">
          {preview
            ? `${preview.leaf_count} folder${preview.leaf_count !== 1 ? "s" : ""} · ${preview.rows_to_add} row${preview.rows_to_add !== 1 ? "s" : ""} to add`
            : "Working it out…"}
          {stale && preview ? " (updating…)" : ""}
        </span>
        <div style={{ flex: 1 }} />
        <button
          className="btn primary" disabled={!canApply} title={blockedReason || undefined}
          onClick={() => preview && applyMutation.mutate({
            body: JSON.parse(debouncedJSON) as BlueprintValue, total: preview.rows_to_add, deleteOrphans: deleteOrphans && orphanPending > 0,
          })}
        >
          {applyMutation.isPending ? "Applying…" : "Apply"}
        </button>
      </div>
      <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {!hasPromptPin && (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            Apply needs a prompt pin. Mark a pinned parameter as the prompt in Workflow &amp; Pins.
          </p>
        )}
        {errors.length > 0 && (
          <ul role="alert" style={{ margin: 0, paddingLeft: 18, color: "var(--bad)", fontSize: 12 }}>
            {errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        )}
        {(preview?.warnings.length ?? 0) > 0 && (
          <ul style={{ margin: 0, paddingLeft: 18, color: "var(--warn, var(--fg-mute))", fontSize: 12 }}>
            {preview!.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        )}
        {preview?.over_cap && (
          <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
            <input type="checkbox" className="checkbox" checked={allowOverCap}
              onChange={(e) => setAllowOverCap(e.target.checked)} />
            Allow a larger expansion (up to 20,000 rows)
          </label>
        )}
        {orphans && orphans.leaves > 0 && (
          <div style={{ fontSize: 12 }}>
            <div style={{ color: "var(--warn, var(--fg-mute))", marginBottom: 4 }}>
              {orphans.pending + orphans.running + orphans.failed + orphans.completed} rows ({orphans.images} images) belong to
              folders this blueprint no longer makes.
            </div>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: orphanPending > 0 ? "pointer" : "default" }}>
              <input type="checkbox" className="checkbox" checked={deleteOrphans && orphanPending > 0}
                disabled={orphanPending === 0}
                onChange={(e) => setDeleteOrphans(e.target.checked)} />
              Delete their {orphanPending} pending rows
            </label>
            <div style={{ color: "var(--fg-mute)", marginTop: 2 }}>Completed rows and images are never deleted.</div>
          </div>
        )}

        {preview && preview.leaves.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }} data-testid="blueprint-leaves">
              <thead>
                <tr style={{ color: "var(--fg-mute)" }}>
                  <th style={cell}>Folder</th>
                  <th style={num}>Target</th>
                  <th style={num}>Kept</th>
                  <th style={num}>Open</th>
                  <th style={num}>To add</th>
                  <th style={cell}>Sample prompts</th>
                </tr>
              </thead>
              <tbody>
                {preview.leaves.map((l) => (
                  <tr key={l.scenario + "/" + l.path} style={{ borderTop: "1px solid var(--border)", verticalAlign: "top" }}>
                    <td style={cell} className="mono">{l.path}</td>
                    <td style={num}>{l.target}</td>
                    <td style={num}>{l.kept}</td>
                    <td style={num}>{l.open}</td>
                    <td style={num}>{l.to_add}</td>
                    <td style={{ ...cell, color: "var(--fg-mute)" }}>
                      {l.samples.map((s, i) => <div key={i}>{s}</div>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {preview.leaf_count > preview.leaves.length && (
              <div style={{ fontSize: 12, color: "var(--fg-mute)", padding: "6px 8px" }}>
                +{preview.leaf_count - preview.leaves.length} more folders
              </div>
            )}
            {preview.leaves.length > 0 && (
              <div style={{ fontSize: 11, color: "var(--fg-mute)", padding: "0 8px" }}>
                Sample prompts show for the first 20 folders only.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
