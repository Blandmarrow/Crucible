import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { comfyApi } from "../../api/comfy";
import { datasetsApi } from "../../api/datasets";

interface Props {
  /** The plan prompts are imported INTO. */
  targetPlanId: string;
  targetDatasetId: string;
  onClose: () => void;
  /** Called after a successful import so the caller can refresh its rows. */
  onImported: () => void;
}

const STATUS_COLOR: Record<string, string> = {
  pending: "var(--fg-mute)",
  running: "var(--accent)",
  completed: "var(--good)",
  failed: "var(--bad)",
};

/** Browse any dataset's plan and copy (or move) its prompts into the current plan. */
export default function ImportPromptsModal({ targetPlanId, targetDatasetId, onClose, onImported }: Props) {
  const qc = useQueryClient();
  const [sourceDatasetId, setSourceDatasetId] = useState<string>(targetDatasetId);
  const [sourcePlanId, setSourcePlanId] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"copy" | "move">("copy");

  function pickDataset(id: string) {
    setSourceDatasetId(id);
    setSourcePlanId("");
    setSelected(new Set());
  }
  function pickPlan(id: string) {
    setSourcePlanId(id);
    setSelected(new Set());
  }

  const { data: datasets = [] } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => datasetsApi.list(),
  });

  const { data: plans = [] } = useQuery({
    queryKey: ["comfy", "plans", sourceDatasetId],
    queryFn: () => comfyApi.listPlans(sourceDatasetId),
    enabled: !!sourceDatasetId,
  });

  // Can't import a plan into itself; fall back to the first eligible plan when the
  // current selection isn't in the (dataset-dependent) list — derived, no effect.
  const eligiblePlans = useMemo(() => plans.filter((p) => p.id !== targetPlanId), [plans, targetPlanId]);
  const effectiveSourcePlanId = eligiblePlans.some((p) => p.id === sourcePlanId)
    ? sourcePlanId
    : eligiblePlans[0]?.id ?? "";

  const { data: prompts = [], isLoading: promptsLoading } = useQuery({
    queryKey: ["comfy", "prompts", effectiveSourcePlanId],
    queryFn: () => comfyApi.listPlanPrompts(effectiveSourcePlanId),
    enabled: !!effectiveSourcePlanId,
  });

  const allSelected = prompts.length > 0 && selected.size === prompts.length;

  function toggle(rowId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(rowId)) next.delete(rowId); else next.add(rowId);
      return next;
    });
  }

  const importMutation = useMutation({
    mutationFn: async () => {
      const chosen = prompts.filter((p) => selected.has(p.row_id));
      const lines = chosen.map((p) => p.prompt.replace(/\s+/g, " ").trim()).filter(Boolean);
      const { created } = await comfyApi.bulkAddRows(targetPlanId, lines);
      if (mode === "move") {
        await comfyApi.deleteRows(effectiveSourcePlanId, chosen.map((p) => p.row_id));
      }
      return created;
    },
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", targetPlanId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", targetDatasetId] });
      if (mode === "move") {
        qc.invalidateQueries({ queryKey: ["comfy", "rows", effectiveSourcePlanId] });
        qc.invalidateQueries({ queryKey: ["comfy", "prompts", effectiveSourcePlanId] });
        qc.invalidateQueries({ queryKey: ["comfy", "plans", sourceDatasetId] });
      }
      toast.success(
        `${mode === "move" ? "Moved" : "Copied"} ${created} prompt${created !== 1 ? "s" : ""} into this plan`,
      );
      onImported();
      onClose();
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? "Import failed");
    },
  });

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={onClose}
    >
      <div className="panel" style={{ width: 640, maxWidth: "94vw", display: "flex", flexDirection: "column", maxHeight: "88vh" }} onClick={(e) => e.stopPropagation()}>
        <div className="panel-h"><h3>Import prompts</h3></div>
        <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            Reuse prompts from another plan. Only the prompt text is copied — it lands in this plan's prompt
            column; other parameters use this plan's defaults/template.
          </p>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", fontSize: 12 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--fg-mute)" }}>
              Dataset
              <select
                className="select" style={{ fontSize: 12, maxWidth: 220 }}
                value={sourceDatasetId} onChange={(e) => pickDataset(e.target.value)}
              >
                {datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}{d.id === targetDatasetId ? " (this dataset)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--fg-mute)" }}>
              Plan
              <select
                className="select" style={{ fontSize: 12, maxWidth: 240 }}
                value={effectiveSourcePlanId} onChange={(e) => pickPlan(e.target.value)}
                disabled={eligiblePlans.length === 0}
              >
                {eligiblePlans.length === 0 && <option value="">(no other plans)</option>}
                {eligiblePlans.map((p) => (
                  <option key={p.id} value={p.id}>{p.name} ({p.row_count})</option>
                ))}
              </select>
            </label>
          </div>

          {/* Prompt list */}
          <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflowY: "auto", flex: 1, minHeight: 120 }}>
            {promptsLoading ? (
              <p style={{ padding: 12, fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>Loading…</p>
            ) : prompts.length === 0 ? (
              <p style={{ padding: 12, fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
                {effectiveSourcePlanId ? "This plan has no prompts." : "Pick a plan to browse its prompts."}
              </p>
            ) : (
              prompts.map((p) => (
                <label
                  key={p.row_id}
                  style={{ display: "flex", gap: 8, padding: "6px 10px", fontSize: 12, cursor: "pointer", borderBottom: "1px solid var(--border)", alignItems: "flex-start" }}
                >
                  <input type="checkbox" className="checkbox" checked={selected.has(p.row_id)} onChange={() => toggle(p.row_id)} style={{ marginTop: 2 }} />
                  <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{p.prompt}</span>
                  <span style={{ color: STATUS_COLOR[p.status] ?? "var(--fg-mute)", fontSize: 10, textTransform: "uppercase", flexShrink: 0 }}>{p.status}</span>
                </label>
              ))
            )}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12 }}>
            <button
              className="btn ghost sm" disabled={prompts.length === 0}
              onClick={() => setSelected(allSelected ? new Set() : new Set(prompts.map((p) => p.row_id)))}
            >
              {allSelected ? "Select none" : "Select all"}
            </button>
            <span style={{ color: "var(--fg-mute)" }}>{selected.size} selected</span>
            <div style={{ flex: 1 }} />
            <label style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--fg-mute)", cursor: "pointer" }}>
              <input type="radio" name="import-mode" checked={mode === "copy"} onChange={() => setMode("copy")} />
              Copy
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--fg-mute)", cursor: "pointer" }}
              title="Also delete the selected prompts from the source plan">
              <input type="radio" name="import-mode" checked={mode === "move"} onChange={() => setMode("move")} />
              Move
            </label>
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button className="btn ghost" onClick={onClose}>Cancel</button>
            <button
              className="btn primary"
              disabled={selected.size === 0 || importMutation.isPending}
              onClick={() => importMutation.mutate()}
            >
              {importMutation.isPending
                ? "Importing…"
                : `${mode === "move" ? "Move" : "Copy"} ${selected.size} prompt${selected.size !== 1 ? "s" : ""}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
