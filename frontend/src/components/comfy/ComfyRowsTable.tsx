import { useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { comfyApi, type ComfyPlan, type ComfyRow } from "../../api/comfy";
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

/** Template value for a pinned param — shown as the cell placeholder. */
function templateValue(plan: ComfyPlan, alias: string): unknown {
  const pin = plan.pinned_params.find((p) => p.alias === alias);
  if (!pin) return "";
  return plan.workflow_json[pin.node_id]?.inputs?.[pin.input] ?? "";
}

function EditableCell({ row, alias, numeric, disabled, onCommit }: {
  row: ComfyRow;
  alias: string;
  numeric: boolean;
  disabled: boolean;
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

  const aliases = plan.pinned_params.map((p) => p.alias);
  const numericAlias = useMemo(() => {
    const m = new Map<string, boolean>();
    for (const a of aliases) m.set(a, typeof templateValue(plan, a) === "number");
    return m;
  }, [plan, aliases]);

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

  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));
  // Grid: checkbox | param columns (prompt column wider) | status | image
  const promptAlias = plan.pinned_params.find((p) => p.is_prompt)?.alias;
  const gridTemplate = [
    "28px",
    ...aliases.map((a) => (a === promptAlias ? "minmax(240px, 3fr)" : "minmax(90px, 1fr)")),
    "96px",
    "60px",
  ].join(" ");

  if (aliases.length === 0) {
    return (
      <p style={{ fontSize: 12, color: "var(--fg-mute)" }}>
        Pin at least one workflow parameter (in Workflow &amp; Pins) to start adding rows.
      </p>
    );
  }

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: "var(--r)", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        display: "grid", gridTemplateColumns: gridTemplate, gap: 8, alignItems: "center",
        padding: "6px 10px", background: "var(--surface-2)", borderBottom: "1px solid var(--line)",
        fontSize: 11, color: "var(--fg-mute)", textTransform: "uppercase", letterSpacing: ".06em",
      }}>
        <input
          type="checkbox" className="checkbox" checked={allSelected}
          onChange={(e) => onToggleAll(rows.map((r) => r.id), e.target.checked)}
        />
        {aliases.map((a) => <span key={a}>{a}{a === promptAlias ? " (prompt)" : ""}</span>)}
        <span>Status</span>
        <span>Image</span>
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
                  {aliases.map((a) => (
                    <EditableCell
                      key={a} row={row} alias={a}
                      numeric={numericAlias.get(a) ?? false}
                      disabled={rowBusy}
                      onCommit={(alias, raw) => commitCell(row, alias, raw)}
                    />
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
    </div>
  );
}
