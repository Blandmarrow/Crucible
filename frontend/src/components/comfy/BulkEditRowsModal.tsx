import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../../utils/apiError";
import { comfyApi, type ComfyPlan } from "../../api/comfy";

interface Props {
  plan: ComfyPlan;
  rowCount: number;
  selectedIds: string[];
  onClose: () => void;
}

type Op = "find_replace" | "prepend" | "append" | "remove";

const OP_LABEL: Record<Op, string> = {
  find_replace: "Find & replace",
  prepend: "Prepend text",
  append: "Append text",
  remove: "Remove text",
};

/** Bulk text operations on the prompt column — mirrors the Bulk Edit page's caption tab. */
export default function BulkEditRowsModal({ plan, rowCount, selectedIds, onClose }: Props) {
  const qc = useQueryClient();
  const [op, setOp] = useState<Op>("find_replace");
  const [text, setText] = useState("");
  const [replacement, setReplacement] = useState("");
  const [useRegex, setUseRegex] = useState(false);
  const [scope, setScope] = useState<"all" | "selected">(selectedIds.length > 0 ? "selected" : "all");

  const targetCount = scope === "selected" ? selectedIds.length : rowCount;

  const mutation = useMutation({
    mutationFn: () =>
      comfyApi.bulkEditRows(plan.id, {
        operation: op,
        text,
        replacement: op === "find_replace" ? replacement : undefined,
        use_regex: useRegex,
        row_ids: scope === "selected" ? selectedIds : undefined,
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", plan.id] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", plan.dataset_id] });
      toast.success(`Updated ${data.affected} prompt${data.affected !== 1 ? "s" : ""}${data.skipped ? ` (${data.skipped} unchanged)` : ""}`);
      onClose();
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Bulk edit failed"));
    },
  });

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={onClose}
    >
      <div className="panel" style={{ width: 520, maxWidth: "92vw" }} onClick={(e) => e.stopPropagation()}>
        <div className="panel-h"><h3>Edit prompts</h3></div>
        <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            Edits the prompt column. A row's base text is its effective prompt (own value, else run
            default / template); changed completed/failed rows reset to pending.
          </p>

          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 12 }}>
            <select className="select" style={{ fontSize: 12 }} value={op} onChange={(e) => setOp(e.target.value as Op)}>
              {Object.entries(OP_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            {(op === "find_replace" || op === "remove") && (
              <label style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--fg-mute)", cursor: "pointer" }}>
                <input type="checkbox" className="checkbox" checked={useRegex} onChange={(e) => setUseRegex(e.target.checked)} />
                regex
              </label>
            )}
            <div style={{ flex: 1 }} />
            <label style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--fg-mute)", cursor: "pointer" }}>
              <input type="radio" name="rows-scope" checked={scope === "all"} onChange={() => setScope("all")} />
              all rows ({rowCount})
            </label>
            <label style={{
              display: "flex", alignItems: "center", gap: 5, cursor: selectedIds.length ? "pointer" : "not-allowed",
              color: selectedIds.length ? "var(--fg-mute)" : "var(--fg-dim)",
            }}>
              <input
                type="radio" name="rows-scope" checked={scope === "selected"} disabled={selectedIds.length === 0}
                onChange={() => setScope("selected")}
              />
              selected ({selectedIds.length})
            </label>
          </div>

          <input
            className="input mono" autoFocus
            style={{ width: "100%", fontSize: 12 }}
            placeholder={op === "find_replace" || op === "remove" ? (useRegex ? "pattern to match" : "text to find") : "text to add"}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          {op === "find_replace" && (
            <input
              className="input mono"
              style={{ width: "100%", fontSize: 12 }}
              placeholder={useRegex ? "replacement (\\1 groups supported)" : "replacement"}
              value={replacement}
              onChange={(e) => setReplacement(e.target.value)}
            />
          )}

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button className="btn ghost" onClick={onClose}>Cancel</button>
            <button
              className="btn primary"
              disabled={!text || targetCount === 0 || mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending ? "Applying…" : `Apply to ${targetCount} row${targetCount !== 1 ? "s" : ""}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
