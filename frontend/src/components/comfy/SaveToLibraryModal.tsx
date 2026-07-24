import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../../utils/apiError";
import { comfyApi } from "../../api/comfy";

interface Props {
  planId: string;
  /** Selected rows whose effective prompts are saved. */
  rowIds: string[];
  onClose: () => void;
}

/** Save the selected rows' prompts into a category of the global prompt library. */
export default function SaveToLibraryModal({ planId, rowIds, onClose }: Props) {
  const qc = useQueryClient();
  const [category, setCategory] = useState("");

  const { data: library = [] } = useQuery({
    queryKey: ["comfy", "library"],
    queryFn: comfyApi.libraryList,
  });
  const categories = useMemo(
    () => [...new Set(library.map((p) => p.category))].sort((a, b) => a.localeCompare(b)),
    [library],
  );

  const saveMutation = useMutation({
    mutationFn: async (cat: string) => {
      // Effective prompts (row → run default → template) are backend-authoritative.
      const prompts = await comfyApi.listPlanPrompts(planId);
      const chosen = new Set(rowIds);
      const lines = prompts.filter((p) => chosen.has(p.row_id)).map((p) => p.prompt.replace(/\s+/g, " ").trim()).filter(Boolean);
      if (lines.length === 0) throw new Error("empty");
      return comfyApi.libraryAdd(cat, lines);
    },
    onSuccess: ({ created, skipped }, cat) => {
      qc.invalidateQueries({ queryKey: ["comfy", "library"] });
      toast.success(
        `Saved ${created} prompt${created !== 1 ? "s" : ""} to "${cat}"` +
        (skipped > 0 ? ` (${skipped} already there)` : ""),
      );
      onClose();
    },
    onError: (err: unknown) => {
      if (err instanceof Error && err.message === "empty") {
        toast.error("The selected rows have no prompt text");
        return;
      }
      toast.error(apiErrorDetail(err, "Failed to save prompts"));
    },
  });

  const trimmed = category.trim();

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={onClose}
    >
      <div className="panel" style={{ width: 420, maxWidth: "92vw" }} onClick={(e) => e.stopPropagation()}>
        <div className="panel-h"><h3>Save to library</h3></div>
        <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            Save the prompts of {rowIds.length} selected row{rowIds.length !== 1 ? "s" : ""} to the global
            prompt library. Duplicates already in the category are skipped.
          </p>
          <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", flexDirection: "column", gap: 4 }}>
            Category
            <input
              className="input" autoFocus list="library-categories" maxLength={100}
              placeholder={categories.length > 0 ? "Pick or type a new category" : "e.g. Fantasy scenes"}
              value={category} onChange={(e) => setCategory(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && trimmed && !saveMutation.isPending) saveMutation.mutate(trimmed);
                if (e.key === "Escape") onClose();
              }}
            />
            <datalist id="library-categories">
              {categories.map((c) => <option key={c} value={c} />)}
            </datalist>
          </label>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button className="btn ghost" onClick={onClose}>Cancel</button>
            <button
              className="btn primary"
              disabled={!trimmed || saveMutation.isPending}
              onClick={() => saveMutation.mutate(trimmed)}
            >
              {saveMutation.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
