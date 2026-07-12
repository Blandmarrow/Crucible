import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { comfyApi, type ComfyPlan, type ComfyRow, type PinnedParam } from "../../api/comfy";
import { usePaneNavigate } from "../../hooks/usePaneNavigate";

interface Props {
  plan: ComfyPlan;
  rows: ComfyRow[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onToggleAll: (ids: string[], select: boolean) => void;
  runningJob: boolean;
}

const STATUS_BADGE: Record<ComfyRow["status"], string> = {
  pending: "badge dot",
  running: "badge dot info",
  completed: "badge dot good",
  failed: "badge dot bad",
};

function templateValue(plan: ComfyPlan, pin: PinnedParam): unknown {
  return plan.workflow_json[pin.node_id]?.inputs?.[pin.input];
}

/** What a blank cell falls back to: run default, else template (or an int-mode roll). */
function fallbackLabel(plan: ComfyPlan, pin: PinnedParam): string {
  if (pin.int_mode === "random") return "🎲 random";
  if (pin.int_mode === "increment") return "auto +n";
  if (pin.value !== null && pin.value !== undefined && pin.value !== "") return String(pin.value);
  const tv = templateValue(plan, pin);
  return tv === undefined ? "" : String(tv);
}

/** Prompt-column cell: a textarea that can be dragged taller per cell, or auto-sized
 *  to its full content via the column-header expand toggle. Enter commits,
 *  Shift+Enter inserts a newline. The virtualizer's measureElement picks up the
 *  height changes (ResizeObserver on the row). */
function PromptCell({ row, alias, disabled, placeholder, expanded, onCommit }: {
  row: ComfyRow;
  alias: string;
  disabled: boolean;
  placeholder: string;
  expanded: boolean;
  onCommit: (alias: string, raw: string) => void;
}) {
  const stored = row.values[alias];
  const value = stored === undefined || stored === null ? "" : String(stored);
  const [draft, setDraft] = useState<string | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  const text = draft ?? value;
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (expanded) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight + 2}px`;
    } else {
      el.style.height = ""; // back to the rows=1 natural height (also clears manual drags)
    }
  }, [expanded, text]);
  return (
    <textarea
      ref={ref}
      className="input"
      rows={1}
      style={{
        width: "100%", fontSize: 12, minHeight: 26, padding: "4px 6px",
        resize: "vertical", lineHeight: 1.35, display: "block",
      }}
      value={text}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (draft !== null && draft !== value) onCommit(alias, draft);
        setDraft(null);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          (e.target as HTMLTextAreaElement).blur();
        }
      }}
    />
  );
}

function EditableCell({ row, alias, numeric, disabled, placeholder, onCommit }: {
  row: ComfyRow;
  alias: string;
  numeric: boolean;
  disabled: boolean;
  placeholder: string;
  onCommit: (alias: string, raw: string) => void;
}) {
  const stored = row.values[alias];
  const value = stored === undefined || stored === null ? "" : String(stored);
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <input
      className="input"
      style={{ width: "100%", fontSize: 12, height: 26, padding: "0 6px" }}
      type={numeric ? "number" : "text"}
      value={draft ?? value}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (draft !== null && draft !== value) onCommit(alias, draft);
        setDraft(null);
      }}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
    />
  );
}

export default function ComfyRowsTable({ plan, rows, selected, onToggle, onToggleAll, runningJob }: Props) {
  const qc = useQueryClient();
  const { go } = usePaneNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Only per-row pins get columns; run defaults live in the defaults strip above.
  const columns = useMemo(() => plan.pinned_params.filter((p) => p.per_row), [plan.pinned_params]);
  const numericAlias = useMemo(() => {
    const m = new Map<string, boolean>();
    for (const p of columns) m.set(p.alias, typeof templateValue(plan, p) === "number");
    return m;
  }, [plan, columns]);

  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 36,
    overscan: 10,
  });

  const updateMutation = useMutation({
    mutationFn: ({ rowId, values }: { rowId: string; values: Record<string, unknown> }) =>
      comfyApi.updateRow(rowId, { values }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["comfy", "rows", plan.id] }),
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? "Failed to save row");
    },
  });

  function commitCell(row: ComfyRow, alias: string, raw: string) {
    const values = { ...row.values };
    if (raw === "") {
      delete values[alias];
    } else {
      values[alias] = numericAlias.get(alias) && !Number.isNaN(Number(raw)) ? Number(raw) : raw;
    }
    updateMutation.mutate({ rowId: row.id, values });
  }

  // Expand every prompt cell to its full text (toggle in the prompt column header).
  const [expandPrompts, setExpandPrompts] = useState(false);

  // Bulk "set for all rows" per column.
  const [bulkPin, setBulkPin] = useState<PinnedParam | null>(null);
  const [bulkValue, setBulkValue] = useState("");

  const setAllMutation = useMutation({
    mutationFn: ({ alias, value }: { alias: string; value: string | number | boolean | null }) =>
      comfyApi.setValueAllRows(plan.id, alias, value),
    onSuccess: (data, vars) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", plan.id] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", plan.dataset_id] });
      setBulkPin(null);
      setBulkValue("");
      toast.success(
        vars.value === null
          ? `Cleared "${vars.alias}" on ${data.updated} row${data.updated !== 1 ? "s" : ""} (default applies)`
          : `Set "${vars.alias}" on ${data.updated} row${data.updated !== 1 ? "s" : ""}`
      );
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? "Failed to update rows");
    },
  });

  function applyBulk(clear: boolean) {
    if (!bulkPin) return;
    const raw = bulkValue.trim();
    if (!clear && raw === "") return;
    const value = clear
      ? null
      : numericAlias.get(bulkPin.alias) && !Number.isNaN(Number(raw)) ? Number(raw) : raw;
    setAllMutation.mutate({ alias: bulkPin.alias, value });
  }

  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));
  // Grid: checkbox | per-row columns (prompt wider) | status | image
  const gridTemplate = [
    "28px",
    ...columns.map((p) => (p.is_prompt ? "minmax(240px, 3fr)" : "minmax(110px, 1fr)")),
    "96px",
    "60px",
  ].join(" ");

  if (columns.length === 0) {
    return (
      <p style={{ fontSize: 12, color: "var(--fg-mute)" }}>
        No per-row columns yet — pin a parameter in Workflow &amp; Pins and switch <b>per row</b> on
        (the prompt parameter is per-row automatically).
      </p>
    );
  }

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: "var(--r)", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        display: "grid", gridTemplateColumns: gridTemplate, gap: 8, alignItems: "start",
        padding: "6px 10px", background: "var(--surface-2)", borderBottom: "1px solid var(--line)",
        fontSize: 11, color: "var(--fg-mute)",
      }}>
        <input
          type="checkbox" className="checkbox" checked={allSelected}
          onChange={(e) => onToggleAll(rows.map((r) => r.id), e.target.checked)}
        />
        {columns.map((p) => (
          <span key={p.alias} style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <span style={{ display: "flex", alignItems: "center", gap: 4, textTransform: "uppercase", letterSpacing: ".06em" }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {p.alias}{p.is_prompt ? " ★" : ""}
              </span>
              <button
                className="icon-btn"
                style={{ fontSize: 11, padding: 0, width: 16, height: 16, lineHeight: 1, flexShrink: 0 }}
                title={`Set "${p.alias}" on all rows at once`}
                disabled={runningJob || rows.length === 0}
                onClick={() => { setBulkPin(p); setBulkValue(""); }}
              >
                ✎
              </button>
              {p.is_prompt && (
                <button
                  className="icon-btn"
                  style={{
                    fontSize: 11, padding: 0, width: 16, height: 16, lineHeight: 1, flexShrink: 0,
                    color: expandPrompts ? "var(--accent)" : undefined,
                  }}
                  title={expandPrompts ? "Collapse prompts to one line" : "Expand all prompts to full text"}
                  onClick={() => setExpandPrompts((v) => !v)}
                >
                  {expandPrompts ? "⤒" : "⤢"}
                </button>
              )}
            </span>
            <span className="mono" style={{
              fontSize: 10, color: "var(--fg-dim)", textTransform: "none", letterSpacing: 0,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}
              title={`${plan.workflow_json[p.node_id]?.class_type ?? "?"} node #${p.node_id}, input "${p.input}"`}>
              #{p.node_id} {plan.workflow_json[p.node_id]?.class_type ?? "?"} · {p.input}
            </span>
          </span>
        ))}
        <span style={{ textTransform: "uppercase", letterSpacing: ".06em" }}>Status</span>
        <span style={{ textTransform: "uppercase", letterSpacing: ".06em" }}>Image</span>
      </div>

      {/* Virtualized body */}
      <div ref={scrollRef} style={{ height: "52vh", overflowY: "auto", background: "var(--surface-1)" }}>
        {rows.length === 0 ? (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", padding: 16 }}>
            No rows yet — add one, or paste a list of prompts.
          </p>
        ) : (
          <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
            {rowVirtualizer.getVirtualItems().map((vi) => {
              const row = rows[vi.index];
              const rowBusy = runningJob && row.status === "running";
              return (
                <div
                  key={row.id}
                  data-index={vi.index}
                  ref={rowVirtualizer.measureElement}
                  style={{
                    position: "absolute", top: 0, left: 0, width: "100%",
                    transform: `translateY(${vi.start}px)`,
                    display: "grid", gridTemplateColumns: gridTemplate, gap: 8, alignItems: "center",
                    padding: "4px 10px", borderBottom: "1px solid var(--line-2)",
                  }}
                >
                  <input
                    type="checkbox" className="checkbox" checked={selected.has(row.id)}
                    onChange={() => onToggle(row.id)}
                  />
                  {columns.map((p) => (
                    p.is_prompt ? (
                      <PromptCell
                        key={p.alias} row={row} alias={p.alias}
                        disabled={rowBusy}
                        placeholder={fallbackLabel(plan, p)}
                        expanded={expandPrompts}
                        onCommit={(alias, raw) => commitCell(row, alias, raw)}
                      />
                    ) : (
                      <EditableCell
                        key={p.alias} row={row} alias={p.alias}
                        numeric={numericAlias.get(p.alias) ?? false}
                        disabled={rowBusy}
                        placeholder={fallbackLabel(plan, p)}
                        onCommit={(alias, raw) => commitCell(row, alias, raw)}
                      />
                    )
                  ))}
                  <span
                    className={STATUS_BADGE[row.status]}
                    title={row.error_msg ?? undefined}
                    style={{ cursor: row.error_msg ? "help" : undefined }}
                  >
                    {row.status}
                  </span>
                  {row.image_id ? (
                    <button
                      className="btn ghost sm"
                      style={{ fontSize: 11, padding: "1px 8px" }}
                      title={row.image_ids.length > 1 ? `${row.image_ids.length} images — opens the first` : "Open image"}
                      onClick={() =>
                        go(`/datasets/${plan.dataset_id}/image/${row.image_id}`, {
                          page: "image-detail", datasetId: plan.dataset_id, imageId: row.image_id!,
                        })
                      }
                    >
                      View{row.image_ids.length > 1 ? ` (${row.image_ids.length})` : ""}
                    </button>
                  ) : (
                    <span style={{ color: "var(--fg-dim)", fontSize: 11 }}>—</span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Bulk column edit */}
      {bulkPin && (
        <div
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={() => setBulkPin(null)}
        >
          <div className="panel" style={{ width: 440, maxWidth: "92vw" }} onClick={(e) => e.stopPropagation()}>
            <div className="panel-h"><h3>Set “{bulkPin.alias}” on all rows</h3></div>
            <div className="panel-b">
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 8px" }}>
                Applies to all {rows.length} row{rows.length !== 1 ? "s" : ""} of{" "}
                <span className="mono">#{bulkPin.node_id} {plan.workflow_json[bulkPin.node_id]?.class_type ?? "?"} · {bulkPin.input}</span>.
                Completed and failed rows whose value changes reset to pending. Blank cells currently
                fall back to: <span className="mono">{fallbackLabel(plan, bulkPin) || "—"}</span>
              </p>
              <input
                className="input" autoFocus
                type={numericAlias.get(bulkPin.alias) ? "number" : "text"}
                style={{ width: "100%", fontSize: 12 }}
                placeholder={fallbackLabel(plan, bulkPin)}
                value={bulkValue}
                onChange={(e) => setBulkValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && bulkValue.trim() !== "") applyBulk(false);
                  if (e.key === "Escape") setBulkPin(null);
                }}
              />
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
                <button className="btn ghost" onClick={() => setBulkPin(null)}>Cancel</button>
                <button
                  className="btn ghost"
                  disabled={setAllMutation.isPending}
                  title="Remove the per-row override everywhere; the run default / template applies again"
                  onClick={() => applyBulk(true)}
                >
                  Clear all (use default)
                </button>
                <button
                  className="btn primary"
                  disabled={bulkValue.trim() === "" || setAllMutation.isPending}
                  onClick={() => applyBulk(false)}
                >
                  Apply to all
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
