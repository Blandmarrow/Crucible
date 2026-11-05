import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../../utils/apiError";
import { ChevronDown, ChevronRight, Folder, GripVertical, Lock } from "lucide-react";
import { comfyApi, coerceCellValue, type ComfyPlan, type ComfyRow, type PinnedParam } from "../../api/comfy";
import { imagesApi } from "../../api/images";
import { usePaneNavigate } from "../../hooks/usePaneNavigate";
import { compareFolders } from "./folderTree";
import ContextMenu, { type ContextMenuAction } from "../common/ContextMenu";

interface Props {
  plan: ComfyPlan;
  /** The rows to show: the plan's rows after the folder, text and status filters. */
  rows: ComfyRow[];
  /** The plan's row count before filtering, to tell "no rows yet" from "none match". */
  totalRows: number;
  selected: Set<string>;
  onToggle: (id: string) => void;
  onToggleAll: (ids: string[], select: boolean) => void;
  runningJob: boolean;
  /** Folder groups collapsed under their header (persisted per plan). */
  closedGroups: Set<string>;
  onToggleGroup: (path: string) => void;
  /** Where a row sits in its folder's run order, for the row menu's Move entries. */
  rowBounds: Map<string, { first: boolean; last: boolean }>;
  onMoveRow: (row: ComfyRow, where: RowMove) => void;
}

/** The row menu's moves, all within the row's folder (M6 §8c). Across folders is Set
 *  folder, or a drag. */
export type RowMove = "top" | "up" | "down" | "bottom";

type Item =
  | { kind: "group"; path: string; rows: ComfyRow[] }
  | { kind: "row"; row: ComfyRow };

/** Group header counts: pending · running · failed · done, leaving out zeros. */
function groupSummary(rows: ComfyRow[]): string {
  const n = { pending: 0, running: 0, failed: 0, completed: 0 };
  for (const r of rows) n[r.status]++;
  const parts = [
    n.pending && `${n.pending} pending`,
    n.running && `${n.running} running`,
    n.failed && `${n.failed} failed`,
    n.completed && `${n.completed} done`,
  ].filter(Boolean);
  return parts.join(" · ");
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
  if (pin.int_mode === "random") return "random";
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

/** The row's destination folder, nested under the run bar's base folder.
 *
 *  Deliberately an `<input>`, never a `<textarea>`: a folder is one line, and
 *  `frontend/e2e/comfy.spec.ts` counts the textareas in the rows table to prove
 *  the prompt cells are the only multi-line ones. Modelled on `EditableCell`
 *  (commit on blur, Enter blurs) but writes `subfolder` rather than a `values`
 *  entry — a folder must never enter `values`, which is the pinned-parameter map. */
function FolderCell({ row, disabled, onCommit }: {
  row: ComfyRow;
  disabled: boolean;
  onCommit: (raw: string) => void;
}) {
  const value = row.subfolder ?? "";
  const [draft, setDraft] = useState<string | null>(null);
  // A row the blueprint still counts is locked here: moving it must unlink it, which
  // only Set folder asks about (docs/dev/comfyui/structured.md § Moving a blueprint row).
  if (row.blueprint_linked) {
    return (
      <span style={{ display: "flex", alignItems: "center", gap: 4, minWidth: 0 }}>
        <Lock size={12} style={{ flexShrink: 0, color: "var(--fg-mute)" }} role="img" aria-label="From the blueprint" />
        <input
          className="input"
          style={{ flex: 1, minWidth: 0, fontSize: 12, height: 26, padding: "0 6px" }}
          type="text" value={value} placeholder="(root)" readOnly
          title="Made by the blueprint, which counts this folder. Use Set folder to move it out."
        />
      </span>
    );
  }
  return (
    <input
      className="input"
      style={{ width: "100%", fontSize: 12, height: 26, padding: "0 6px" }}
      type="text"
      value={draft ?? value}
      placeholder="(root)"
      title="Folder for this row's images, nested under the run's base folder. Blank = the base folder itself."
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        // Trimmed like SetRowFolderModal: the server folds slashes but keeps spaces,
        // so "knight " would otherwise file beside "knight" as a second folder.
        const next = draft?.trim();
        if (next !== undefined && next !== value) onCommit(next);
        setDraft(null);
      }}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
    />
  );
}

export default function ComfyRowsTable({
  plan, rows, totalRows, selected, onToggle, onToggleAll, runningJob, closedGroups, onToggleGroup,
  rowBounds, onMoveRow,
}: Props) {
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

  // Rows grouped by folder, in tree order, with a header per group. A plan whose rows
  // all sit in (root) shows no header: one group adds nothing.
  const items = useMemo<Item[]>(() => {
    const groups = new Map<string, ComfyRow[]>();
    for (const r of rows) {
      const g = groups.get(r.subfolder);
      if (g) g.push(r); else groups.set(r.subfolder, [r]);
    }
    const paths = [...groups.keys()].sort(compareFolders);
    if (paths.length === 1 && paths[0] === "") return rows.map((row) => ({ kind: "row", row }));
    const out: Item[] = [];
    for (const path of paths) {
      const g = groups.get(path)!;
      out.push({ kind: "group", path, rows: g });
      if (!closedGroups.has(path)) for (const row of g) out.push({ kind: "row", row });
    }
    return out;
  }, [rows, closedGroups]);

  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (items[i]?.kind === "group" ? 30 : 36),
    getItemKey: (i) => {
      const it = items[i];
      return it.kind === "group" ? `g:${it.path}` : it.row.id;
    },
    overscan: 10,
  });

  // A generic patch, not a `values` dict: the folder cell and the parameter cells
  // write different fields of the same row.
  const updateMutation = useMutation({
    mutationFn: ({ rowId, patch }: { rowId: string; patch: Parameters<typeof comfyApi.updateRow>[1] }) =>
      comfyApi.updateRow(rowId, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["comfy", "rows", plan.id] }),
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Failed to save row"));
    },
  });

  function commitCell(row: ComfyRow, alias: string, raw: string) {
    const values = { ...row.values };
    if (raw === "") {
      delete values[alias];
    } else {
      values[alias] = coerceCellValue(raw, !!numericAlias.get(alias));
    }
    updateMutation.mutate({ rowId: row.id, patch: { values } });
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
      toast.error(apiErrorDetail(err, "Failed to update rows"));
    },
  });

  function applyBulk(clear: boolean) {
    if (!bulkPin) return;
    const raw = bulkValue.trim();
    if (!clear && raw === "") return;
    const value = clear ? null : coerceCellValue(raw, !!numericAlias.get(bulkPin.alias));
    setAllMutation.mutate({ alias: bulkPin.alias, value });
  }

  // The row menu, opened from the grip at the row's start (a drag handle from §8c's
  // stage 6). Buttons only: the e2e counts textareas and "(root)" inputs as row cells.
  const [rowMenu, setRowMenu] = useState<{ row: ComfyRow; x: number; y: number } | null>(null);
  function rowMenuActions(row: ComfyRow): ContextMenuAction[] {
    const b = rowBounds.get(row.id) ?? { first: true, last: true };
    const why = runningJob ? "Wait for the run to finish" : undefined;
    const move = (where: RowMove, label: string, blocked: boolean): ContextMenuAction => ({
      label, onClick: () => onMoveRow(row, where),
      disabled: runningJob || blocked,
      title: why ?? (blocked ? "Already there" : undefined),
    });
    return [
      move("top", "Move to top of folder", b.first),
      move("up", "Move up", b.first),
      move("down", "Move down", b.last),
      move("bottom", "Move to bottom of folder", b.last),
    ];
  }

  // Select-all covers the rows on show: those in a folded group are left out.
  const shownIds = items.flatMap((it) => (it.kind === "row" ? [it.row.id] : []));
  const allSelected = shownIds.length > 0 && shownIds.every((id) => selected.has(id));
  // Grid: checkbox | folder | per-row columns (prompt wider) | status | image.
  // The folder sits leftmost because it is row identity rather than a parameter: it
  // stays put as pins are added and removed. Built once and consumed by the header
  // and every body row, so header/body cells must go in matching positions.
  const gridTemplate = [
    "18px",
    "28px",
    "minmax(120px, 1fr)",
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
    <div
      data-testid="comfy-rows-table"
      style={{ border: "1px solid var(--line)", borderRadius: "var(--r)", overflow: "hidden" }}
    >
      {/* Header */}
      <div style={{
        display: "grid", gridTemplateColumns: gridTemplate, gap: 8, alignItems: "start",
        padding: "6px 10px", background: "var(--surface-2)", borderBottom: "1px solid var(--line)",
        fontSize: 11, color: "var(--fg-mute)",
      }}>
        <span />
        <input
          type="checkbox" className="checkbox" checked={allSelected}
          onChange={(e) => onToggleAll(shownIds, e.target.checked)}
        />
        <span
          style={{ textTransform: "uppercase", letterSpacing: ".06em" }}
          title="Per-row folder — nests under the base folder set on the Run bar"
        >
          Folder
        </span>
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
            {totalRows === 0
              ? "No rows yet — add one, or paste a list of prompts."
              : "No rows match. Clear the filter, or pick another folder."}
          </p>
        ) : (
          <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
            {rowVirtualizer.getVirtualItems().map((vi) => {
              const item = items[vi.index];
              if (item.kind === "group") {
                const open = !closedGroups.has(item.path);
                const label = item.path || "(root)";
                return (
                  // No checkbox, textarea or "(root)"-placeholder input in a header: the
                  // e2e counts those in this table as row cells.
                  <div
                    key={vi.key}
                    data-index={vi.index}
                    ref={rowVirtualizer.measureElement}
                    className="comfy-group-h"
                    data-testid="comfy-group"
                    data-folder={item.path}
                    style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${vi.start}px)` }}
                  >
                    <button
                      className="comfy-rail-twisty"
                      aria-label={`${open ? "Collapse" : "Expand"} folder ${label}`}
                      aria-expanded={open}
                      onClick={() => onToggleGroup(item.path)}
                    >
                      {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    </button>
                    <Folder size={13} style={{ flexShrink: 0, color: item.path ? "var(--accent)" : "var(--fg-mute)" }} />
                    <span className="mono" style={{ fontWeight: 500 }}>{label}</span>
                    <span style={{ color: "var(--fg-mute)", fontSize: 11.5 }}>{groupSummary(item.rows)}</span>
                    {item.rows.some((r) => r.blueprint_linked) && <span className="comfy-pill">from blueprint</span>}
                  </div>
                );
              }
              const row = item.row;
              const rowBusy = runningJob && row.status === "running";
              return (
                <div
                  key={vi.key}
                  data-index={vi.index}
                  ref={rowVirtualizer.measureElement}
                  style={{
                    position: "absolute", top: 0, left: 0, width: "100%",
                    transform: `translateY(${vi.start}px)`,
                    display: "grid", gridTemplateColumns: gridTemplate, gap: 8, alignItems: "center",
                    padding: "4px 10px", borderBottom: "1px solid var(--line-2)",
                  }}
                >
                  <button
                    className="comfy-rail-twisty"
                    aria-label="Row actions"
                    title="Row actions"
                    style={{ cursor: "grab" }}
                    onClick={(e) => {
                      const r = e.currentTarget.getBoundingClientRect();
                      setRowMenu({ row, x: r.left, y: r.bottom + 2 });
                    }}
                  >
                    <GripVertical size={13} />
                  </button>
                  <input
                    type="checkbox" className="checkbox" checked={selected.has(row.id)}
                    onChange={() => onToggle(row.id)}
                  />
                  <FolderCell
                    row={row}
                    // Locked for the whole run, not just the busy row: the run filed its
                    // folders from the rows it loaded at start, so a mid-run edit would
                    // show a folder the image never went to (Set folder is locked too).
                    disabled={runningJob}
                    onCommit={(raw) => updateMutation.mutate({ rowId: row.id, patch: { subfolder: raw } })}
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
                      aria-label={row.image_ids.length > 1 ? `View (${row.image_ids.length})` : "View"}
                      style={{ fontSize: 11, padding: "1px 4px", gap: 4, display: "inline-flex", alignItems: "center" }}
                      title={row.image_ids.length > 1 ? `${row.image_ids.length} images — opens the first` : "Open image"}
                      onClick={() =>
                        go(`/datasets/${plan.dataset_id}/image/${row.image_id}`, {
                          page: "image-detail", datasetId: plan.dataset_id, imageId: row.image_id!,
                        })
                      }
                    >
                      <img
                        src={imagesApi.thumbnailUrl(row.image_id)} alt="" loading="lazy"
                        style={{ width: 24, height: 24, objectFit: "cover", borderRadius: 3, flexShrink: 0 }}
                      />
                      {row.image_ids.length > 1 ? `+${row.image_ids.length - 1}` : ""}
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

      {rowMenu && (
        <ContextMenu
          x={rowMenu.x} y={rowMenu.y}
          actions={rowMenuActions(rowMenu.row)}
          onClose={() => setRowMenu(null)}
        />
      )}

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
                Applies to all {totalRows} row{totalRows !== 1 ? "s" : ""} of the plan, whatever the filter shows, for{" "}
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
