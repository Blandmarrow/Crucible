import { useState } from "react";
import ModalShell from "../common/ModalShell";

interface Props {
  /** "new" makes a folder inside `parent`; "rename" renames the last segment of `path`. */
  mode: "new" | "rename";
  /** For "new": the parent ("" = top level). For "rename": the folder's whole path. */
  path: string;
  isPending: boolean;
  /** The composed whole path. */
  onConfirm: (newPath: string) => void;
  onClose: () => void;
}

/** Name a folder in a plan's tree: a new one inside a parent, or a rename in place.
 *  One segment only; a slash would silently nest it, so it is refused here. */
export default function FolderNameModal({ mode, path, isPending, onConfirm, onClose }: Props) {
  const parent = mode === "new" ? path : path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
  const current = mode === "rename" ? path.split("/").pop()! : "";
  const [name, setName] = useState(current);
  const trimmed = name.trim();
  // A backslash too: the server folds it into a slash, which would silently nest it.
  const invalid = /[/\\]/.test(trimmed) || trimmed === "." || trimmed === "..";
  const unchanged = mode === "rename" && trimmed === current;
  const canSubmit = !!trimmed && !invalid && !unchanged && !isPending;
  const title = mode === "new" ? "New folder" : `Rename "${current}"`;

  function submit() {
    if (canSubmit) onConfirm(parent ? `${parent}/${trimmed}` : trimmed);
  }

  return (
    <ModalShell label={title} onClose={onClose} closeOnBackdrop>
      <h3>{title}</h3>
      <p>
        {mode === "new"
          ? <>Inside <span className="mono">{parent || "(root)"}</span>. It stays in the tree while empty.</>
          : <>Renames the folder and moves every row inside it. Images already made stay where they are.</>}
      </p>
      <input
        className="input" autoFocus aria-label="Folder name" maxLength={200}
        style={{ width: "100%" }} value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
      />
      {invalid && (
        <div style={{ fontSize: 12, color: "var(--bad)", marginTop: 6 }}>
          One folder name only, without slashes. Use Move to… to nest it.
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!canSubmit} onClick={submit}>
          {mode === "new" ? "Create" : "Rename"}
        </button>
      </div>
    </ModalShell>
  );
}
