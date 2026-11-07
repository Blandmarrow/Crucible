import { useState } from "react";
import { MoreHorizontal, Plus } from "lucide-react";
import ContextMenu from "../common/ContextMenu";
import type { ComfyPlanSummary } from "../../api/comfy";
import { LIVE_RUN_REASON } from "../../api/runs";
import DndRow from "./DndRow";
import { DROP_RING, dropId } from "./dnd";

interface Props {
  plans: ComfyPlanSummary[];
  activePlanId: string | null;
  /** Plans with a run this page launched and is still tracking, or an automated Run that is running. */
  runningPlanIds: Set<string>;
  /** Plans whose automated Run is paused: it holds no job, so only this shows it. */
  pausedPlanIds: Set<string>;
  /** Plans whose automated Run is running or paused: the server refuses a delete (409). */
  liveRunPlanIds: Set<string>;
  /** Plans that refuse a delete now: a live run or a generate/prompts/rewrite job. */
  busyPlanIds: Set<string>;
  onSelect: (planId: string) => void;
  onCreate: (name: string) => void;
  creating: boolean;
  onRename: (planId: string, name: string) => void;
  renaming: boolean;
  onDuplicate: (plan: ComfyPlanSummary) => void;
  onDelete: (plan: ComfyPlanSummary) => void;
}

/** The dot beside a plan: running wins over paused, and paused over failed, since a run
 *  may yet retry (a paused one waits on the user). */
function planDot(p: ComfyPlanSummary, running: boolean, paused: boolean): { color: string; label: string } | null {
  if (running || (p.status_counts.running ?? 0) > 0) return { color: "var(--info)", label: "Running" };
  if (paused) return { color: "var(--warn)", label: "An automated run is paused" };
  if ((p.status_counts.failed ?? 0) > 0) return { color: "var(--bad)", label: `${p.status_counts.failed} failed` };
  return null;
}

/** The Rows tab's plan list (M6 §8b): one entry per plan with its row count, a status
 *  dot, and a ⋯ menu for Rename, Duplicate and Delete. *New plan* sits under it. */
export default function ComfyPlanRail({
  plans, activePlanId, runningPlanIds, pausedPlanIds, liveRunPlanIds, busyPlanIds, onSelect, onCreate, creating, onRename, renaming, onDuplicate, onDelete,
}: Props) {
  const [menu, setMenu] = useState<{ plan: ComfyPlanSummary; x: number; y: number } | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  function submitCreate() {
    const name = newName.trim();
    if (!name) return;
    onCreate(name);
    setShowCreate(false);
    setNewName("");
  }

  function submitRename() {
    const name = renameValue.trim();
    if (renameId && name) onRename(renameId, name);
    setRenameId(null);
  }

  return (
    <section className="comfy-rail-panel" data-testid="comfy-plan-rail" aria-label="Plans">
      <div className="comfy-rail-h">Plans</div>
      {plans.map((p) => {
        const dot = planDot(p, runningPlanIds.has(p.id), pausedPlanIds.has(p.id));
        const active = p.id === activePlanId;
        if (renameId === p.id) {
          return (
            <div key={p.id} style={{ display: "flex", gap: 4, padding: "2px 0" }}>
              <input
                className="input" autoFocus value={renameValue} aria-label="Plan name"
                style={{ flex: 1, minWidth: 0, height: 28, fontSize: 12 }}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={submitRename}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitRename();
                  if (e.key === "Escape") setRenameId(null);
                }}
              />
            </div>
          );
        }
        return (
          // A drop target for rows: a drop copies them into this plan, Shift moves (M6 §8c).
          <DndRow
            key={p.id}
            drop={{ id: dropId.plan(p.id), data: { kind: "plan", planId: p.id, name: p.name } }}
            className={`comfy-rail-item${active ? " active" : ""}`}
          >
            {({ isOver }) => (<>
            {isOver && <span aria-hidden style={{ position: "absolute", inset: 0, borderRadius: "var(--r)", pointerEvents: "none", ...DROP_RING }} />}
            <button
              className="comfy-rail-node"
              aria-current={active ? "true" : undefined}
              onClick={() => onSelect(p.id)}
              title={p.name}
            >
              <span
                className="comfy-dot"
                style={{ background: dot?.color ?? "var(--line-2)" }}
                title={dot?.label}
                aria-label={dot?.label}
                role={dot ? "img" : undefined}
              />
              <span className="comfy-rail-label">{p.name}</span>
              <span className="comfy-rail-count mono">{p.row_count}</span>
            </button>
            <button
              className="icon-btn comfy-rail-more"
              aria-label={`Plan actions for ${p.name}`}
              aria-haspopup="menu"
              onClick={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                setMenu({ plan: p, x: r.left, y: r.bottom + 2 });
              }}
            >
              <MoreHorizontal size={14} />
            </button>
          </>)}
          </DndRow>
        );
      })}

      {showCreate ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
          <input
            className="input" autoFocus placeholder="Plan name" value={newName}
            style={{ height: 28, fontSize: 12 }}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitCreate();
              if (e.key === "Escape") { setShowCreate(false); setNewName(""); }
            }}
          />
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn primary sm" disabled={!newName.trim() || creating} onClick={submitCreate}>Create</button>
            <button className="btn ghost sm" onClick={() => { setShowCreate(false); setNewName(""); }}>Cancel</button>
          </div>
        </div>
      ) : (
        <button className="btn ghost sm" style={{ marginTop: 4, justifyContent: "flex-start" }} onClick={() => setShowCreate(true)}>
          <Plus size={12} /> New plan
        </button>
      )}

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          actions={[
            {
              label: "Rename…",
              disabled: renaming,
              onClick: () => { setRenameId(menu.plan.id); setRenameValue(menu.plan.name); },
            },
            { label: "Duplicate…", onClick: () => onDuplicate(menu.plan) },
            {
              label: "Delete…", danger: true,
              disabled: busyPlanIds.has(menu.plan.id),
              title: liveRunPlanIds.has(menu.plan.id)
                ? LIVE_RUN_REASON
                : busyPlanIds.has(menu.plan.id) ? "Wait for this plan's run or job to finish" : undefined,
              onClick: () => onDelete(menu.plan),
            },
          ]}
        />
      )}
    </section>
  );
}
