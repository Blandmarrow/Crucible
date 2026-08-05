import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ChevronDown, ChevronUp, Trash2 } from "lucide-react";

import { labelsApi, type Label, type LabelUpdate } from "../../api/labels";
import ConfirmDialog from "../common/ConfirmDialog";
import { invalidateLabelScope } from "../../constants/queryKeys";
import { useLabels } from "../../hooks/useLabels";
import { usePopover } from "../../hooks/usePopover";
import { useUiPrefsStore } from "../../store/uiPrefsStore";
import { apiErrorDetail } from "../../utils/apiError";
import HotkeyCaptureButton from "./HotkeyCaptureButton";

/** A strict superset of the original nine, so no existing label loses its
 *  "selected" ring when the palette grows. Two rows of ten. */
const SWATCHES = [
  "#ef4444", "#f97316", "#f59e0b", "#eab308", "#84cc16",
  "#22c55e", "#10b981", "#14b8a6", "#06b6d4", "#0ea5e9",
  "#3b82f6", "#6366f1", "#8b5cf6", "#a855f7", "#d946ef",
  "#ec4899", "#f43f5e", "#78716c", "#6b7280", "#94a3b8",
];

/** Named rather than `SWATCHES[5]`: an index silently means a different colour
 *  the moment the list grows. */
const DEFAULT_NEW_COLOR = "#3b82f6";

/** The palette itself, ten to a row. Shared by the create form and the
 *  per-row popover so the two never drift apart. */
function SwatchGrid({
  value, onPick, size,
}: {
  value: string;
  onPick: (color: string) => void;
  size: number;
}) {
  return (
    <span style={{ display: "grid", gridTemplateColumns: `repeat(10, ${size}px)`, gap: 4 }}>
      {SWATCHES.map((c) => (
        <button
          key={c}
          type="button"
          aria-label={`Colour ${c}`}
          aria-pressed={value.toLowerCase() === c}
          onClick={() => onPick(c)}
          style={{
            width: size, height: size, borderRadius: 4, background: c, cursor: "pointer",
            border: value.toLowerCase() === c ? "2px solid var(--fg)" : "1px solid var(--line-2)",
          }}
        />
      ))}
    </span>
  );
}

/**
 * One label row's colour control: a swatch that opens the palette.
 *
 * A button per swatch inline was already crowded at nine and does not fit at
 * twenty, so the palette moved behind a popover. `align="right"` because the row
 * ends close to the panel's right edge.
 */
function ColorSwatchButton({
  label, disabled, onPick,
}: {
  label: Label;
  disabled: boolean;
  onPick: (color: string) => void;
}) {
  const { open, setOpen, anchorRef, triggerRef } = usePopover<HTMLSpanElement>();
  const [custom, setCustom] = useState(label.color);

  return (
    <span ref={anchorRef} style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        ref={triggerRef}
        aria-label={`Change ${label.name} colour`}
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        style={{
          width: 20, height: 20, borderRadius: 4, background: label.color,
          border: "1px solid var(--line-2)", cursor: disabled ? "default" : "pointer",
        }}
      />
      {open && (
        <div
          style={{
            position: "absolute", top: "calc(100% + 4px)", right: 0, zIndex: 1000,
            background: "var(--surface-2)", border: "1px solid var(--line-2)",
            borderRadius: "var(--r)", boxShadow: "0 8px 24px rgba(0,0,0,.4)",
            padding: 8, display: "flex", flexDirection: "column", gap: 8,
          }}
        >
          <SwatchGrid
            value={label.color}
            onPick={(c) => { onPick(c); setOpen(false); }}
            size={16}
          />
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11.5, color: "var(--fg-dim)" }}>
            {/* Commits on blur, never on change: the OS colour picker fires
                `onChange` continuously while it is dragged, and each one here
                would be its own `PATCH /labels/{id}`. */}
            <input
              type="color"
              aria-label={`Custom colour for ${label.name}`}
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              onBlur={() => { if (custom.toLowerCase() !== label.color.toLowerCase()) onPick(custom); }}
              style={{ width: 28, height: 22, padding: 0, background: "none", border: "1px solid var(--line-2)", borderRadius: 4, cursor: "pointer" }}
            />
            Custom
          </label>
        </div>
      )}
    </span>
  );
}

/**
 * Settings → Labels: the whole managed vocabulary.
 *
 * Extracted rather than inlined into the 1150-line `SettingsPage` (the
 * `SecretField` precedent). The vocabulary is deliberately *managed* — there is
 * no free-form typing anywhere else in the app — so this panel is the only place
 * a label is created, renamed, recoloured, rebound, reordered or deleted.
 *
 * Reordering is plain up/down buttons: dnd-kit buys little for a list this short
 * and would be the only drag surface outside the gallery.
 */
export default function LabelsPanel() {
  const qc = useQueryClient();
  const { labels, isLoading } = useLabels();
  const hotkeysEnabled = useUiPrefsStore((s) => s.labelHotkeysEnabled);
  const setHotkeysEnabled = useUiPrefsStore((s) => s.setLabelHotkeysEnabled);

  const [newName, setNewName] = useState("");
  const [newColor, setNewColor] = useState(DEFAULT_NEW_COLOR);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Label | null>(null);

  const refresh = () => invalidateLabelScope(qc);

  const createMutation = useMutation({
    mutationFn: () => labelsApi.create({ name: newName.trim(), color: newColor }),
    onSuccess: () => {
      setNewName("");
      refresh();
      toast.success("Label created");
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Could not create the label")),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: LabelUpdate }) => labelsApi.update(id, body),
    onSuccess: () => refresh(),
    onError: (err) => toast.error(apiErrorDetail(err, "Could not update the label")),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => labelsApi.remove(id),
    onSuccess: () => {
      setPendingDelete(null);
      refresh();
      toast.success("Label deleted");
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Could not delete the label")),
  });

  const reorderMutation = useMutation({
    mutationFn: (ids: string[]) => labelsApi.reorder(ids),
    onSuccess: () => refresh(),
    onError: (err) => toast.error(apiErrorDetail(err, "Could not reorder the labels")),
  });

  function move(index: number, delta: number) {
    const next = [...labels];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    reorderMutation.mutate(next.map((l) => l.id));
  }

  function commitRename(label: Label) {
    const name = editingName.trim();
    setEditingId(null);
    if (!name || name === label.name) return;
    // A rename is "same concept, new spelling": nothing detaches, because every
    // assignment and every snapshot mirror stores the label *id*.
    updateMutation.mutate({ id: label.id, body: { name } });
  }

  const busy =
    createMutation.isPending || updateMutation.isPending ||
    deleteMutation.isPending || reorderMutation.isPending;

  return (
    <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <p style={{ fontSize: 12.5, color: "var(--fg-mute)", margin: 0 }}>
        Labels are a second way to organise images, alongside subfolders — an image can carry
        any number of them. The vocabulary is global: a label means the same thing in every
        dataset, and it travels with an image that is copied or duplicated elsewhere. Labels
        are never written into captions and never appear in an exported <code style={{ fontSize: 11 }}>.txt</code>.
      </p>

      <form
        style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
        onSubmit={(e) => {
          e.preventDefault();
          if (newName.trim()) createMutation.mutate();
        }}
      >
        <input
          className="input"
          style={{ flex: "1 1 180px", minWidth: 140 }}
          placeholder="New label name"
          aria-label="New label name"
          maxLength={64}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <SwatchGrid value={newColor} onPick={setNewColor} size={18} />
        {/* Local state with no request behind it, so a live `onChange` is fine
            here — unlike the per-row picker below, where every intermediate
            value would be a PATCH. */}
        <input
          type="color"
          aria-label="Custom colour"
          value={newColor}
          onChange={(e) => setNewColor(e.target.value)}
          style={{ width: 28, height: 24, padding: 0, background: "none", border: "1px solid var(--line-2)", borderRadius: 4, cursor: "pointer" }}
        />
        <button className="btn-primary btn-sm" type="submit" disabled={!newName.trim() || busy}>
          Add label
        </button>
      </form>

      {isLoading && <div style={{ fontSize: 12.5, color: "var(--fg-dim)" }}>Loading…</div>}

      {!isLoading && labels.length === 0 && (
        <div style={{ fontSize: 12.5, color: "var(--fg-dim)" }}>
          No labels yet. Add one above to start tagging images with it.
        </div>
      )}

      {labels.length > 0 && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
          {labels.map((label, i) => (
            <li
              key={label.id}
              style={{
                display: "flex", alignItems: "center", gap: 10, padding: "6px 8px",
                border: "1px solid var(--line)", borderRadius: 6,
              }}
            >
              <span
                aria-hidden
                style={{ width: 12, height: 12, borderRadius: "50%", background: label.color, flex: "0 0 auto" }}
              />

              {editingId === label.id ? (
                <input
                  className="input"
                  autoFocus
                  aria-label={`Rename ${label.name}`}
                  maxLength={64}
                  value={editingName}
                  onChange={(e) => setEditingName(e.target.value)}
                  onBlur={() => commitRename(label)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(label);
                    if (e.key === "Escape") setEditingId(null);
                  }}
                  style={{ flex: 1 }}
                />
              ) : (
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  style={{ flex: 1, justifyContent: "flex-start", textAlign: "left" }}
                  onClick={() => {
                    setEditingId(label.id);
                    setEditingName(label.name);
                  }}
                >
                  {label.name}
                </button>
              )}

              <span style={{ fontSize: 11.5, color: "var(--fg-dim)", whiteSpace: "nowrap" }}>
                {label.usage_count} image{label.usage_count === 1 ? "" : "s"}
              </span>

              <ColorSwatchButton
                label={label}
                disabled={busy}
                onPick={(c) => updateMutation.mutate({ id: label.id, body: { color: c } })}
              />

              <HotkeyCaptureButton
                value={label.hotkey}
                disabled={busy}
                ownerOf={(key) => labels.find((l) => l.id !== label.id && l.hotkey === key)?.name ?? null}
                onChange={(key) => updateMutation.mutate({ id: label.id, body: { hotkey: key } })}
              />

              <button
                type="button"
                className="btn-ghost btn-sm"
                aria-label={`Move ${label.name} up`}
                disabled={i === 0 || busy}
                onClick={() => move(i, -1)}
              >
                <ChevronUp size={14} />
              </button>
              <button
                type="button"
                className="btn-ghost btn-sm"
                aria-label={`Move ${label.name} down`}
                disabled={i === labels.length - 1 || busy}
                onClick={() => move(i, 1)}
              >
                <ChevronDown size={14} />
              </button>
              <button
                type="button"
                className="btn-ghost btn-sm"
                aria-label={`Delete ${label.name}`}
                disabled={busy}
                onClick={() => setPendingDelete(label)}
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
        <input
          type="checkbox"
          checked={hotkeysEnabled}
          onChange={(e) => setHotkeysEnabled(e.target.checked)}
        />
        Label hotkeys in the image detail view
        <span style={{ fontSize: 11.5, color: "var(--fg-dim)" }}>
          — pressing a bound key toggles that label on the open image.
        </span>
      </label>

      {pendingDelete && (
        <ConfirmDialog
          title={`Delete “${pendingDelete.name}”?`}
          message={
            pendingDelete.usage_count > 0
              ? `This label is on ${pendingDelete.usage_count} image${pendingDelete.usage_count === 1 ? "" : "s"}. Deleting it removes it from all of them. Snapshots that recorded it will restore without it.`
              : "This label is not on any image."
          }
          confirmLabel="Delete"
          danger
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => deleteMutation.mutate(pendingDelete.id)}
        />
      )}
    </div>
  );
}
