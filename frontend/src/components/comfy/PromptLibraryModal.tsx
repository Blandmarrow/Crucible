import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { comfyApi } from "../../api/comfy";
import { datasetsApi } from "../../api/datasets";
import ConfirmDialog from "../common/ConfirmDialog";
import { useModalBehavior } from "../../hooks/useModalBehavior";
import { apiErrorDetail } from "../../utils/apiError";

interface Props {
  /** The plan prompts are added INTO. */
  targetPlanId: string;
  targetDatasetId: string;
  onClose: () => void;
  /** Called after prompts are added to the plan so the caller can refresh its rows. */
  onImported: () => void;
}

const STATUS_COLOR: Record<string, string> = {
  pending: "var(--fg-mute)",
  running: "var(--accent)",
  completed: "var(--good)",
  failed: "var(--bad)",
};

const NEW_CATEGORY = "__new__";

/** The global prompt library (categorized) plus cross-plan prompt reuse, in one modal. */
export default function PromptLibraryModal({ targetPlanId, targetDatasetId, onClose, onImported }: Props) {
  const [tab, setTab] = useState<"library" | "plans">("library");
  const { overlayProps, panelProps } = useModalBehavior({ onClose, label: "Prompt library", closeOnBackdrop: true });

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
      {...overlayProps}
    >
      <div className="panel" style={{ width: 720, maxWidth: "94vw", display: "flex", flexDirection: "column", maxHeight: "88vh" }} {...panelProps}>
        <div className="panel-h">
          <h3>Prompt library</h3>
          <div style={{ flex: 1 }} />
          <div className="tabs" style={{ marginBottom: 0 }}>
            <button className={`tab${tab === "library" ? " active" : ""}`} onClick={() => setTab("library")}>Library</button>
            <button className={`tab${tab === "plans" ? " active" : ""}`} onClick={() => setTab("plans")}>Other plans</button>
          </div>
        </div>
        <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            Only the prompt text is added — it lands in this plan's prompt column; other parameters use this
            plan's defaults/template.
          </p>
          {tab === "library" ? (
            <LibraryTab targetPlanId={targetPlanId} targetDatasetId={targetDatasetId} onClose={onClose} onImported={onImported} />
          ) : (
            <OtherPlansTab targetPlanId={targetPlanId} targetDatasetId={targetDatasetId} onClose={onClose} onImported={onImported} />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Library tab ───────────────────────────────────────────────────────────────

function LibraryTab({ targetPlanId, targetDatasetId, onClose, onImported }: Props) {
  const qc = useQueryClient();
  const [category, setCategory] = useState<string | null>(null); // null = All
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [moveTarget, setMoveTarget] = useState<string>("");
  const [newCategory, setNewCategory] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const { data: prompts = [], isLoading } = useQuery({
    queryKey: ["comfy", "library"],
    queryFn: comfyApi.libraryList,
  });

  const categories = useMemo(() => {
    const counts = new Map<string, number>();
    for (const p of prompts) counts.set(p.category, (counts.get(p.category) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [prompts]);

  const visible = useMemo(
    () => (category === null ? prompts : prompts.filter((p) => p.category === category)),
    [prompts, category],
  );
  const allSelected = visible.length > 0 && visible.every((p) => selected.has(p.id));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  const invalidateLibrary = () => qc.invalidateQueries({ queryKey: ["comfy", "library"] });

  const addToPlanMutation = useMutation({
    mutationFn: () => {
      const lines = prompts.filter((p) => selected.has(p.id)).map((p) => p.text.replace(/\s+/g, " ").trim()).filter(Boolean);
      return comfyApi.bulkAddRows(targetPlanId, lines);
    },
    onSuccess: ({ created }) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", targetPlanId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", targetDatasetId] });
      toast.success(`Added ${created} prompt${created !== 1 ? "s" : ""} to this plan`);
      onImported();
      onClose();
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to add prompts")),
  });

  const moveMutation = useMutation({
    mutationFn: (target: string) => comfyApi.libraryMove([...selected], target),
    onSuccess: ({ moved, merged }, target) => {
      invalidateLibrary();
      setSelected(new Set());
      setMoveTarget("");
      setNewCategory("");
      toast.success(
        `Moved ${moved} prompt${moved !== 1 ? "s" : ""} to "${target}"` +
        (merged > 0 ? ` (${merged} duplicate${merged !== 1 ? "s" : ""} merged)` : ""),
      );
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to move prompts")),
  });

  const deleteMutation = useMutation({
    mutationFn: () => comfyApi.libraryDelete([...selected]),
    onSuccess: ({ deleted }) => {
      invalidateLibrary();
      setSelected(new Set());
      setConfirmDelete(false);
      toast.success(`Deleted ${deleted} prompt${deleted !== 1 ? "s" : ""} from the library`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to delete prompts")),
  });

  const effectiveMoveCategory = moveTarget === NEW_CATEGORY ? newCategory.trim() : moveTarget;

  return (
    <>
      <div style={{ display: "flex", gap: 10, flex: 1, minHeight: 160 }}>
        {/* Category sidebar */}
        <div style={{ width: 180, flexShrink: 0, border: "1px solid var(--border)", borderRadius: 6, overflowY: "auto" }}>
          <CategoryButton label="All" count={prompts.length} active={category === null} onClick={() => setCategory(null)} />
          {categories.map(([cat, count]) => (
            <CategoryButton key={cat} label={cat} count={count} active={category === cat} onClick={() => setCategory(cat)} />
          ))}
        </div>

        {/* Prompt list */}
        <div style={{ flex: 1, border: "1px solid var(--border)", borderRadius: 6, overflowY: "auto" }}>
          {isLoading ? (
            <p style={{ padding: 12, fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>Loading…</p>
          ) : visible.length === 0 ? (
            <p style={{ padding: 12, fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
              {prompts.length === 0
                ? "The library is empty — select rows in a plan and use “Save to library” to fill it."
                : "No prompts in this category."}
            </p>
          ) : (
            visible.map((p) => (
              <label
                key={p.id}
                style={{ display: "flex", gap: 8, padding: "6px 10px", fontSize: 12, cursor: "pointer", borderBottom: "1px solid var(--border)", alignItems: "flex-start" }}
              >
                <input type="checkbox" className="checkbox" checked={selected.has(p.id)} onChange={() => toggle(p.id)} style={{ marginTop: 2 }} />
                <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{p.text}</span>
                {category === null && (
                  <span style={{ color: "var(--fg-dim)", fontSize: 10, flexShrink: 0 }}>{p.category}</span>
                )}
              </label>
            ))
          )}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12 }}>
        <button
          className="btn ghost sm" disabled={visible.length === 0}
          onClick={() => setSelected(allSelected
            ? new Set([...selected].filter((id) => !visible.some((p) => p.id === id)))
            : new Set([...selected, ...visible.map((p) => p.id)]))}
        >
          {allSelected ? "Select none" : "Select all"}
        </button>
        <span style={{ color: "var(--fg-mute)" }}>{selected.size} selected</span>
        <div style={{ flex: 1 }} />
        <select
          className="select" style={{ fontSize: 12, maxWidth: 160 }}
          value={moveTarget} onChange={(e) => setMoveTarget(e.target.value)}
          disabled={selected.size === 0}
        >
          <option value="">Move to…</option>
          {categories.map(([cat]) => <option key={cat} value={cat}>{cat}</option>)}
          <option value={NEW_CATEGORY}>New category…</option>
        </select>
        {moveTarget === NEW_CATEGORY && (
          <input
            className="input" autoFocus placeholder="Category name" maxLength={100}
            value={newCategory} onChange={(e) => setNewCategory(e.target.value)}
            style={{ width: 140, fontSize: 12 }}
          />
        )}
        {moveTarget !== "" && (
          <button
            className="btn ghost sm"
            disabled={selected.size === 0 || !effectiveMoveCategory || moveMutation.isPending}
            onClick={() => moveMutation.mutate(effectiveMoveCategory)}
          >
            Move
          </button>
        )}
        <button
          className="btn ghost sm" style={{ color: "var(--bad)" }}
          disabled={selected.size === 0}
          onClick={() => setConfirmDelete(true)}
        >
          Delete ({selected.size})
        </button>
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button
          className="btn primary"
          disabled={selected.size === 0 || addToPlanMutation.isPending}
          onClick={() => addToPlanMutation.mutate()}
        >
          {addToPlanMutation.isPending ? "Adding…" : `Add ${selected.size} to plan`}
        </button>
      </div>

      {confirmDelete && (
        <ConfirmDialog
          title="Delete library prompts"
          message={`Delete ${selected.size} prompt${selected.size !== 1 ? "s" : ""} from the library? Plans that already use them are unaffected.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </>
  );
}

function CategoryButton({ label, count, active, onClick }: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex", width: "100%", alignItems: "center", gap: 6, padding: "6px 10px",
        fontSize: 12, textAlign: "left", cursor: "pointer", border: "none",
        borderBottom: "1px solid var(--border)",
        background: active ? "var(--bg-3, rgba(127,127,127,.15))" : "transparent",
        color: active ? "var(--fg)" : "var(--fg-mute)",
      }}
    >
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      <span style={{ color: "var(--fg-dim)", fontSize: 10 }}>{count}</span>
    </button>
  );
}

// ── Other plans tab (cross-plan copy/move, unchanged behavior) ────────────────

function OtherPlansTab({ targetPlanId, targetDatasetId, onClose, onImported }: Props) {
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
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Import failed")),
  });

  return (
    <>
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
    </>
  );
}
