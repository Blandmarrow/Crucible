import { useMemo, useState } from "react";
import { FolderInput } from "lucide-react";
import type { SubfolderInfo } from "../../types";
import { canDropFolderOn } from "../../constants/galleryOptions";
import { useModalBehavior } from "../../hooks/useModalBehavior";

interface Props {
  /** The folder being moved — `path` is its current whole path. */
  node: { path: string; label: string };
  /** Every subfolder in the dataset, as returned by `["subfolders", datasetId]`. */
  subfolders: SubfolderInfo[];
  isPending: boolean;
  /** `newPath` is the composed destination path, never a bare parent. */
  onConfirm: (newPath: string) => void;
  onClose: () => void;
}

// Sentinel for "top level". A normalized subfolder path never has a leading or
// trailing slash, so "/" is not a path any row can carry and cannot collide.
const ROOT = "/";

/**
 * Destination picker for moving a gallery subfolder under another one.
 *
 * A **vertical list**, not the chip row `SelectionToolbar`'s move modal uses — a list
 * row can ellipsis a long path in place, so this is immune by construction to the
 * overflow that row had. The current parent is rendered *disabled* rather than hidden,
 * so the tree the user is reading stays intact.
 */
export default function MoveSubfolderModal({ node, subfolders, isPending, onConfirm, onClose }: Props) {
  const parent = node.path.includes("/") ? node.path.slice(0, node.path.lastIndexOf("/")) : "";
  const [selected, setSelected] = useState<string | null>(null);

  const choices = useMemo(
    () => subfolders
      .filter(sf => sf.path !== "" && canDropFolderOn(node.path, sf.path))
      .map(sf => ({ path: sf.path, depth: sf.path.split("/").length - 1 }))
      .sort((a, b) => a.path.localeCompare(b.path)),
    [subfolders, node.path]
  );

  const destParent = selected === ROOT ? "" : selected;
  const newPath = destParent === null ? null : destParent ? `${destParent}/${node.label}` : node.label;
  const collides = newPath !== null && subfolders.some(sf => sf.path === newPath);
  const unchanged = destParent !== null && destParent === parent;

  const { overlayProps, panelProps } = useModalBehavior({ onClose, label: `Move subfolder ${node.path}` });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" {...overlayProps}>
      <div className="card p-5 w-full max-w-sm space-y-3" style={{ maxHeight: "85vh", overflowY: "auto" }} {...panelProps}>
        <h4 className="font-medium flex items-center gap-2">
          <FolderInput size={15} /> Move "{node.label}"
        </h4>
        <p style={{ fontSize: 12, color: "var(--fg-mute)" }}>
          Moves the folder and everything inside it. Files are not renamed.
        </p>

        <div style={{ maxHeight: 260, overflowY: "auto", display: "flex", flexDirection: "column", gap: 2 }}>
          <button
            className={`btn btn-sm ${selected === ROOT ? "primary" : ""}`}
            style={{ justifyContent: "flex-start", textAlign: "left", maxWidth: "100%", minWidth: 0 }}
            disabled={parent === ""}
            onClick={() => setSelected(ROOT)}
          >
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              (root) — top level{parent === "" ? " (current location)" : ""}
            </span>
          </button>
          {choices.map(c => {
            const isCurrent = c.path === parent;
            return (
              <button
                key={c.path}
                className={`btn btn-sm ${selected === c.path ? "primary" : ""}`}
                style={{
                  justifyContent: "flex-start", textAlign: "left",
                  maxWidth: "100%", minWidth: 0, paddingLeft: 8 + c.depth * 10,
                }}
                title={c.path}
                disabled={isCurrent}
                onClick={() => setSelected(c.path)}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.path.split("/").pop()}{isCurrent ? " (current location)" : ""}
                </span>
              </button>
            );
          })}
        </div>

        {/* A re-path rewrites a whole subtree, so showing the composed result is the point. */}
        <p style={{ fontSize: 12, color: collides ? "var(--bad)" : "var(--fg-mute)", minHeight: 18 }}>
          {newPath === null
            ? "Pick a destination."
            : collides
              ? `A subfolder named "${newPath}" already exists.`
              : <>Result: <code style={{ fontSize: 11 }}>{newPath}</code></>}
        </p>

        <div className="flex gap-2 justify-end">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button
            className="btn-primary"
            disabled={newPath === null || collides || unchanged || isPending}
            onClick={() => newPath && onConfirm(newPath)}
          >
            {isPending ? "Moving…" : "Move"}
          </button>
        </div>
      </div>
    </div>
  );
}
