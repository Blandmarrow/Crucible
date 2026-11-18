import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import { APP_NOTES_KEY, datasetNotesKey, notesApi, type NoteItem, type Notes } from "../../api/notes";
import { apiErrorDetail } from "../../utils/apiError";


interface SectionDef {
  section: string;
  title: string;
  hint: string;
  placeholder: string;
}

const DATASET_SECTIONS: SectionDef[] = [
  { section: "preference", title: "Preferences", hint: "Standing rules for this dataset.", placeholder: "Trigger word zxc comes first." },
  { section: "decision", title: "Decisions", hint: "What you decided, and why.", placeholder: "Removed 30 blurry images, kept 5 with deliberate motion blur." },
  { section: "open_item", title: "Open items", hint: "What is still to do.", placeholder: "Re-caption the night shots." },
];
const APP_SECTIONS: SectionDef[] = [
  { section: "about_me", title: "About me", hint: "Shared by every dataset.", placeholder: "I train SDXL LoRAs with kohya." },
];

/**
 * One scope of notes (a dataset's, or the app-wide "About me"), as editable items
 * grouped by section. Items, not one text box: the analyst (phase 2) proposes
 * changes one item at a time. A dismissal of a health finding shows here too, as a
 * preference; removing it brings the finding back.
 */
export default function NotesEditor({ scope, datasetId }: { scope: "dataset" | "app"; datasetId?: string }) {
  const qc = useQueryClient();
  const key = scope === "dataset" ? datasetNotesKey(datasetId!) : APP_NOTES_KEY;
  const api = scope === "dataset" ? notesApi.dataset(datasetId!) : notesApi.app();
  const sections = scope === "dataset" ? DATASET_SECTIONS : APP_SECTIONS;

  const { data } = useQuery<Notes>({ queryKey: key, queryFn: () => api.list() });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: key });
    // A dismissal is a note: removing or restoring one changes the report.
    if (scope === "dataset") qc.invalidateQueries({ queryKey: ["dataset-health", datasetId] });
  };
  const onError = (err: unknown) => toast.error(apiErrorDetail(err, "Could not save the note"));

  const add = useMutation({
    mutationFn: (v: { section: string; text: string }) =>
      api.add({ ...v, decided_at: v.section === "decision" ? new Date().toISOString() : undefined }),
    onSuccess: refresh,
    onError,
  });
  const update = useMutation({
    mutationFn: (v: { id: string; text?: string; position?: number }) =>
      api.update(v.id, { text: v.text, position: v.position }),
    onSuccess: refresh,
    onError,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.remove(id),
    onSuccess: (item) => {
      refresh();
      toast((t) => (
        <span>
          Note removed{" "}
          <button className="btn sm ghost" onClick={() => { toast.dismiss(t.id); restore.mutate(item.id); }}>Undo</button>
        </span>
      ));
    },
    onError,
  });
  const restore = useMutation({ mutationFn: (id: string) => api.restore(id), onSuccess: refresh, onError });

  const items = data?.items ?? [];
  const over = data ? data.used_chars / data.cap_chars : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {data && (
        <div style={{ fontSize: 11.5, color: over > 0.9 ? "var(--warn)" : "var(--fg-dim)" }} title="Notes are kept short so they fit beside the findings in a small model's context.">
          {data.used_chars.toLocaleString()} / {data.cap_chars.toLocaleString()} characters
        </div>
      )}
      {sections.map((def) => (
        <NoteSection
          key={def.section}
          def={def}
          items={items.filter((i) => i.section === def.section)}
          busy={add.isPending || update.isPending}
          onAdd={(text, done) => add.mutate({ section: def.section, text }, { onSuccess: done })}
          onEdit={(id, text) => update.mutate({ id, text })}
          onMove={(id, position) => update.mutate({ id, position })}
          onRemove={(id) => remove.mutate(id)}
        />
      ))}
    </div>
  );
}

function NoteSection({ def, items, busy, onAdd, onEdit, onMove, onRemove }: {
  def: SectionDef;
  items: NoteItem[];
  busy: boolean;
  /** `done` runs once the note is saved, so a refused one keeps its text. */
  onAdd: (text: string, done: () => void) => void;
  onEdit: (id: string, text: string) => void;
  onMove: (id: string, position: number) => void;
  onRemove: (id: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onAdd(text, () => setDraft(""));
  };
  return (
    <section aria-label={def.title}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>{def.title}</h3>
        <span style={{ fontSize: 11.5, color: "var(--fg-dim)" }}>{def.hint}</span>
      </div>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
        {items.map((item, i) => (
          <NoteRow
            key={item.id}
            item={item}
            first={i === 0}
            last={i === items.length - 1}
            busy={busy}
            onEdit={(text) => onEdit(item.id, text)}
            onUp={() => onMove(item.id, i - 1)}
            onDown={() => onMove(item.id, i + 1)}
            onRemove={() => onRemove(item.id)}
          />
        ))}
      </ol>
      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        <input
          className="input"
          aria-label={`Add to ${def.title}`}
          placeholder={def.placeholder}
          value={draft}
          maxLength={2000}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          style={{ flex: 1, fontSize: 12.5 }}
        />
        <button className="btn sm" onClick={submit} disabled={!draft.trim() || busy}>Add</button>
      </div>
    </section>
  );
}

function NoteRow({ item, first, last, busy, onEdit, onUp, onDown, onRemove }: {
  item: NoteItem;
  first: boolean;
  last: boolean;
  /** A save is in flight: the move buttons wait, since their indices would be stale. */
  busy: boolean;
  onEdit: (text: string) => void;
  onUp: () => void;
  onDown: () => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.text);
  const save = () => {
    setEditing(false);
    const t = text.trim();
    if (t && t !== item.text) onEdit(t);
    else setText(item.text);
  };
  return (
    <li
      data-testid="note-item"
      style={{ display: "flex", alignItems: "flex-start", gap: 6, padding: "5px 8px", borderRadius: "var(--r)", background: "var(--surface-2)", fontSize: 12.5 }}
    >
      {editing ? (
        <textarea
          className="input"
          autoFocus
          rows={2}
          value={text}
          maxLength={2000}
          aria-label="Edit note"
          onChange={(e) => setText(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save(); }
            if (e.key === "Escape") { e.stopPropagation(); setText(item.text); setEditing(false); }
          }}
          style={{ flex: 1, fontSize: 12.5 }}
        />
      ) : (
        <button
          type="button"
          // Re-seed from the server each time: another pane may have changed it, and
          // a stale draft would be saved back over that change on blur.
          onClick={() => { setText(item.text); setEditing(true); }}
          title="Click to edit"
          style={{ flex: 1, textAlign: "left", background: "none", border: "none", padding: 0, color: "var(--fg)", cursor: "text", font: "inherit", whiteSpace: "pre-wrap" }}
        >
          {item.finding_id && <span className="badge" style={{ marginRight: 6 }} title={`Dismisses the health finding “${item.finding_id}”`}>dismissal</span>}
          {item.text}
          {item.decided_at && (
            <span style={{ color: "var(--fg-dim)", fontSize: 11, marginLeft: 6 }}>{new Date(item.decided_at).toLocaleDateString()}</span>
          )}
        </button>
      )}
      <button className="icon-btn" aria-label="Move up" disabled={first || busy} onClick={onUp}><ArrowUp size={13} /></button>
      <button className="icon-btn" aria-label="Move down" disabled={last || busy} onClick={onDown}><ArrowDown size={13} /></button>
      <button className="icon-btn" aria-label="Remove note" onClick={onRemove}><X size={13} /></button>
    </li>
  );
}
