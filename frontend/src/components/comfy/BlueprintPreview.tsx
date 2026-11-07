import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { comfyApi, type BlueprintValue } from "../../api/comfy";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { apiErrorDetail } from "../../utils/apiError";
import { LIVE_RUN_REASON } from "../../api/runs";
import RewriteModal from "./RewriteModal";

const PREVIEW_DEBOUNCE_MS = 350;

interface Props {
  planId: string;
  datasetId: string;
  /** The form as it is right now; the preview and Apply follow it after a short pause. */
  value: BlueprintValue;
  hasPromptPin: boolean;
  /** A run is live on this plan. The preview sits outside the editors' read-only
   *  fieldsets, so it disables its own buttons and options. */
  running: boolean;
  /** An automated Run on this plan is running or paused: Apply and Top up are refused. */
  liveRun?: boolean;
  /** The run bar's settings, which Top up runs with (read-only here). */
  subfolder: string;
  setCaption: boolean;
  /** Top up queued a run: the page tracks it like one started from the run bar. */
  onRunStarted: (jobId: string, planId: string) => void;
}

const cell = { padding: "4px 8px", textAlign: "left" as const, fontSize: 12 };
const num = { ...cell, textAlign: "right" as const };

export default function BlueprintPreview({ planId, datasetId, value, hasPromptPin, running, liveRun, subfolder, setCaption, onRunStarted }: Props) {
  const qc = useQueryClient();
  // Folders whose sample prompts are shown, by scenario + "/" + path.
  const [openLeaves, setOpenLeaves] = useState<Set<string>>(() => new Set());
  const [allowOverCap, setAllowOverCap] = useState(false);
  const [deleteOrphans, setDeleteOrphans] = useState(false);
  const [showRewrite, setShowRewrite] = useState(false);
  // Top up applied but left rows waiting for the rewrite: it ran nothing, so say why.
  const [awaitingRewrite, setAwaitingRewrite] = useState(0);

  const liveJSON = JSON.stringify(value);
  const debouncedJSON = useDebouncedValue(liveJSON, PREVIEW_DEBOUNCE_MS);
  const lagging = liveJSON !== debouncedJSON;

  const previewKey = ["comfy", "blueprint-preview", planId, debouncedJSON, allowOverCap] as const;
  const { data: preview, isPlaceholderData, isError, error, refetch } = useQuery({
    queryKey: previewKey,
    queryFn: ({ signal }) =>
      comfyApi.previewBlueprint(planId, { ...(JSON.parse(debouncedJSON) as BlueprintValue), allow_over_cap: allowOverCap }, signal),
    placeholderData: keepPreviousData,
    staleTime: 0,
    // Kept and short change outside this tab: an image deleted in the gallery (another
    // pane, say) makes its folder short. Without a refetch the panel would sit on
    // "0 short" with Top up disabled, hiding the very shortfall Top up exists for. A
    // stale preview cannot do harm (confirm_total would 409), but it misleads.
    refetchOnWindowFocus: true,
    refetchInterval: 20_000,
  });

  // The rewrite button counts rows that exist now (the preview's count also includes rows
  // Apply would add), at the batch size the modal will default to.
  const { data: gen } = useQuery({
    queryKey: ["comfy", "gen-settings-defaults", planId],
    queryFn: () => comfyApi.getGenSettings(planId),
    staleTime: 0,
  });
  const estimateBatch = Math.min(10, Math.max(1, gen?.batch_size ?? 5));
  const rewriteWanted = (preview?.rewrite_rows ?? 0) > 0;
  const { data: estimate } = useQuery({
    queryKey: ["comfy", "blueprint-rewrite", planId, estimateBatch],
    queryFn: ({ signal }) => comfyApi.rewriteEstimate(planId, estimateBatch, signal),
    enabled: rewriteWanted,
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
      setAwaitingRewrite(0);
      qc.invalidateQueries({ queryKey: ["comfy", "blueprint-rewrite", planId] });
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

  const topUpMutation = useMutation({
    // planId is captured at call time, so a plan switch mid-request cannot retag the run.
    mutationFn: (vars: { planId: string; body: BlueprintValue; total: number; deleteOrphans: boolean }) =>
      comfyApi.topUpBlueprint(vars.planId, {
        ...vars.body, confirm_total: vars.total, allow_over_cap: allowOverCap,
        delete_orphan_pending: vars.deleteOrphans, subfolder, set_caption: setCaption,
      }),
    onSuccess: (data, vars) => {
      setAwaitingRewrite(data.awaiting_rewrite ?? 0);
      if (data.awaiting_rewrite > 0) {
        // Applied, but nothing was reset or run: the notice below says what to do.
        toast.success(`Added ${data.created} row${data.created !== 1 ? "s" : ""}`);
      } else if (data.job_id) {
        onRunStarted(data.job_id, vars.planId);
        toast.success(`Top up: added ${data.created} row${data.created !== 1 ? "s" : ""}, reset ${data.reset} failed — running ${data.total} row${data.total !== 1 ? "s" : ""}`);
      } else {
        toast.success("Every folder is full");
      }
      setDeleteOrphans(false);
      qc.invalidateQueries({ queryKey: ["comfy", "blueprint-rewrite", vars.planId] });
      qc.invalidateQueries({ queryKey: ["comfy", "rows", vars.planId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      qc.invalidateQueries({ queryKey: ["comfy", "prompts", vars.planId] });
      qc.invalidateQueries({ queryKey: ["comfy", "blueprint-preview", vars.planId] });
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Failed to top up the blueprint"));
      if ((err as { response?: { status?: number } })?.response?.status === 409) {
        qc.invalidateQueries({ queryKey: ["comfy", "blueprint-preview", planId] });
      }
    },
  });

  if (isError && !preview) {
    return (
      <aside className="panel bp-side" aria-label="Preview">
        <div className="panel-h bp-actions"><h3>Preview</h3></div>
        <div className="panel-b bp-body">
          <p style={{ color: "var(--bad)", fontSize: 12, margin: "0 0 8px" }}>
            {apiErrorDetail(error, "Could not compute the preview.")}
          </p>
          <button className="btn sm" onClick={() => refetch()}>Retry</button>
        </div>
      </aside>
    );
  }

  // A background refetch of the same spec (the 20 s interval, window focus) is not
  // stale: counting it would disable both buttons every 20 s and drop a click that
  // lands mid-refetch. The preview shown still belongs to this spec, and confirm_total
  // 409s if the rows moved meanwhile. Only a spec the shown preview was not computed
  // from is stale: one still debouncing, or placeholder data for a new key.
  const stale = lagging || isPlaceholderData;
  const errors = preview?.errors ?? [];
  const orphans = preview?.orphans;
  const overCapBlocked = !!preview?.over_cap && !allowOverCap;
  const blockedReason = running
    ? "A run is in progress on this plan"
    : liveRun ? LIVE_RUN_REASON
    : !hasPromptPin
    ? "Mark a pinned parameter as the prompt first (Workflow & Pins)"
    : errors.length > 0 ? "Fix the errors first"
    : overCapBlocked ? "Over the row cap: allow a larger expansion first"
    : stale ? "Waiting for the preview" : "";
  const canApply = !!preview && !blockedReason && !applyMutation.isPending && !topUpMutation.isPending;
  const orphanPending = orphans?.pending ?? 0;
  const allFull = !!preview && preview.images_short === 0 && preview.rows_to_add === 0
    && preview.rows_pending + preview.rows_failed === 0;
  const topUpBlocked = blockedReason || (allFull ? "Every folder is full" : "");
  const canTopUp = !!preview && !topUpBlocked && !topUpMutation.isPending && !applyMutation.isPending;

  const rewriteRows = estimate?.rows ?? 0;
  const rewriteReason = rewriteRows === 0 ? "Apply first: there are no pending or failed template prompts to rewrite yet" : "";

  // Two blocks: the actions on top, the body below. The side column is sticky with the
  // body scrolling inside it; in a narrow pane the root becomes `display: contents` and the
  // actions pin to the bottom of the tab instead (index.css, .bp-side).
  return (
    <>
    <aside className="panel bp-side" aria-label="Preview" data-testid="blueprint-preview">
      <div className="bp-actions" style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 8 }}>
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>Preview</h3>
        <span style={{ fontSize: 12, color: "var(--fg-mute)", opacity: stale ? 0.6 : 1 }} data-testid="blueprint-totals">
          {preview
            ? `${preview.leaf_count} folder${preview.leaf_count !== 1 ? "s" : ""} · ${preview.rows_to_add} row${preview.rows_to_add !== 1 ? "s" : ""} to add · ${preview.images_short} image${preview.images_short !== 1 ? "s" : ""} short`
              + (preview.rows_pending + preview.rows_failed > 0 ? ` · ${preview.rows_pending} pending · ${preview.rows_failed} failed` : "")
              + (preview.rewrite_rows > 0 ? ` · ${preview.rewrite_rows} to rewrite · ≤ ${preview.rewrite_call_budget} LLM calls` : "")
            : "Working it out…"}
          {stale && preview ? " (updating…)" : ""}
        </span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        <button
          className="btn primary" disabled={!canApply} title={blockedReason || undefined}
          onClick={() => preview && applyMutation.mutate({
            body: JSON.parse(debouncedJSON) as BlueprintValue, total: preview.rows_to_add, deleteOrphans: deleteOrphans && orphanPending > 0,
          })}
        >
          {applyMutation.isPending ? "Applying…" : "Apply"}
        </button>
        <button
          className="btn" disabled={!canTopUp} title={topUpBlocked || "Add the missing rows, reset failed ones and run them"}
          data-testid="blueprint-topup"
          onClick={() => preview && topUpMutation.mutate({
            planId, body: JSON.parse(debouncedJSON) as BlueprintValue, total: preview.rows_to_add,
            deleteOrphans: deleteOrphans && orphanPending > 0,
          })}
        >
          {topUpMutation.isPending ? "Topping up…" : "Top up"}
        </button>
        {rewriteWanted && (
          <button
            className="btn" data-testid="blueprint-rewrite" disabled={running || rewriteRows === 0}
            title={running ? "A run is in progress on this plan"
              : rewriteReason || "Rewrite the template prompts into prose with an LLM"}
            onClick={() => setShowRewrite(true)}
          >
            Rewrite {rewriteRows} prompt{rewriteRows !== 1 ? "s" : ""}
          </button>
        )}
      </div>
      <span style={{ fontSize: 12, color: "var(--fg-mute)" }} data-testid="topup-settings">
        Top up runs into Base folder: {subfolder ? <span className="mono">{subfolder}</span> : "(dataset root)"}
        {setCaption ? ", prompt as caption" : ""}. Change both in the run bar at the top.
      </span>
      </div>
      <div className="bp-body" style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
        {awaitingRewrite > 0 && (
          <p style={{ fontSize: 12, color: "var(--warn, var(--fg-mute))", margin: 0 }} data-testid="blueprint-awaiting-rewrite">
            Applied. {awaitingRewrite} row{awaitingRewrite !== 1 ? "s" : ""} wait{awaitingRewrite === 1 ? "s" : ""} for the rewrite — press Rewrite, then Top up again.
          </p>
        )}
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
            <input type="checkbox" className="checkbox" checked={allowOverCap} disabled={running}
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
                disabled={running || orphanPending === 0}
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
                  <th style={num}>Short</th>
                  <th style={num}>Open</th>
                  <th style={num}>To add</th>
                </tr>
              </thead>
              <tbody>
                {/* The sample prompts open under their folder on demand: a column of them
                    made every row tall in the side column (the user's call, 2026-11-05). */}
                {preview.leaves.map((l) => {
                  const key = l.scenario + "/" + l.path;
                  const open = openLeaves.has(key);
                  const toggle = () => setOpenLeaves((prev) => {
                    const next = new Set(prev);
                    if (next.has(key)) next.delete(key); else next.add(key);
                    return next;
                  });
                  return (
                    <Fragment key={key}>
                      <tr style={{ borderTop: "1px solid var(--border)", verticalAlign: "top" }}>
                        <td style={{ ...cell, wordBreak: "break-all" }} className="mono">
                          {l.samples.length > 0 ? (
                            <button type="button" className="bp-section-toggle" aria-expanded={open}
                              title={open ? "Hide the sample prompts" : "Show the sample prompts"} onClick={toggle}>
                              {open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
                              {l.path}
                            </button>
                          ) : l.path}
                        </td>
                        <td style={num}>{l.target}</td>
                        <td style={num}>{l.kept}</td>
                        <td style={num}>{l.short}</td>
                        <td style={num}>{l.open}</td>
                        <td style={num}>{l.to_add}</td>
                      </tr>
                      {open && (
                        <tr data-testid="blueprint-leaf-samples">
                          <td colSpan={6} style={{ ...cell, color: "var(--fg-mute)", paddingLeft: 26 }}>
                            {l.samples.map((s, i) => <div key={i}>{s}</div>)}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
            {preview.leaf_count > preview.leaves.length && (
              <div style={{ fontSize: 12, color: "var(--fg-mute)", padding: "6px 8px" }}>
                +{preview.leaf_count - preview.leaves.length} more folders
              </div>
            )}
            {preview.leaves.length > 0 && (
              <div style={{ fontSize: 11, color: "var(--fg-mute)", padding: "0 8px" }}>
                Click a folder to see its sample prompts. They show for the first 20 folders only.
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
    {/* Outside the aside: a sticky element is its own stacking context, so a dialog inside
        it would sit under the page's sticky header, z-index 60 or not. */}
    {showRewrite && <RewriteModal planId={planId} onClose={() => setShowRewrite(false)} />}
    </>
  );
}
