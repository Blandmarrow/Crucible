import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2, TriangleAlert } from "lucide-react";
import toast from "react-hot-toast";
import { imagesApi } from "../../api/images";
import { useSelectionStore } from "../../store/selectionStore";

interface Props {
  datasetId: string;
  imageIds?: string[];
  qualityFlags?: string[];
  subfolder?: string;
  disabled?: boolean;
}

export default function BulkDeleteForm({ datasetId, imageIds, qualityFlags, subfolder, disabled }: Props) {
  const qc = useQueryClient();
  const clearSelection = useSelectionStore((s) => s.clear);
  const [result, setResult] = useState<{ deleted: number } | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      imagesApi.bulkDelete(datasetId, { imageIds, qualityFlags, subfolder }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      clearSelection();
      setResult(data);
      if (data.deleted === 0) {
        toast("No images matched — nothing was deleted");
      } else {
        toast.success(`Deleted ${data.deleted} image${data.deleted !== 1 ? "s" : ""}`);
      }
    },
    onError: () => toast.error("Bulk delete failed"),
  });

  return (
    <div className="space-y-4">
      <div
        className="flex items-start gap-3 rounded p-3 text-sm"
        style={{ background: "color-mix(in srgb, var(--warn) 12%, transparent)", border: "1px solid color-mix(in srgb, var(--warn) 30%, transparent)" }}
      >
        <TriangleAlert size={16} style={{ color: "var(--warn)", flexShrink: 0, marginTop: 1 }} />
        <span style={{ color: "var(--fg)" }}>
          This permanently deletes the matching images and their sidecar files. This cannot be undone.
        </span>
      </div>

      {result && (
        <div className="flex items-center gap-2 text-sm">
          <span className="badge badge-bad">{result.deleted} deleted</span>
        </div>
      )}

      <div className="flex gap-2 justify-end pt-1">
        <button
          className="btn btn-danger flex items-center gap-2"
          onClick={() => mutation.mutate()}
          disabled={disabled || mutation.isPending}
        >
          <Trash2 size={14} /> {mutation.isPending ? "Deleting…" : "Delete Images"}
        </button>
      </div>
    </div>
  );
}
