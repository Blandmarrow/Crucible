import { useState, useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CaseSensitive } from "lucide-react";
import toast from "react-hot-toast";
import { imagesApi } from "../../api/images";

interface Props {
  datasetId: string;
  imageIds?: string[];
  qualityFlags?: string[];
  subfolder?: string;
  disabled?: boolean;
}

function clientSlug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/[\s_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    || "image";
}

export default function BulkRenameForm({ datasetId, imageIds, qualityFlags, subfolder, disabled }: Props) {
  const qc = useQueryClient();
  const [stem, setStem] = useState("");
  const [result, setResult] = useState<{ affected: number } | null>(null);

  const slug = useMemo(() => (stem.trim() ? clientSlug(stem.trim()) : ""), [stem]);

  const mutation = useMutation({
    mutationFn: () =>
      imagesApi.bulkRename(datasetId, {
        newStem: stem.trim(),
        imageIds,
        qualityFlags,
        subfolder,
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setResult(data);
      if (data.affected === 0) {
        toast("No images matched — nothing was renamed");
      } else {
        toast.success(`Renamed ${data.affected} image${data.affected !== 1 ? "s" : ""}`);
      }
    },
    onError: () => toast.error("Bulk rename failed"),
  });

  const canSubmit = !disabled && stem.trim().length > 0 && !mutation.isPending;

  return (
    <div className="space-y-4">
      <div>
        <label className="label">Base name</label>
        <input
          className="input w-full"
          value={stem}
          onChange={(e) => { setStem(e.target.value); setResult(null); }}
          placeholder="e.g. portrait, landscape_photo"
          autoFocus
        />
        {slug && (
          <p className="text-xs mt-1.5" style={{ color: "var(--fg-mute)" }}>
            Images will be named: <code>{slug}_001.ext</code>, <code>{slug}_002.ext</code>, …
          </p>
        )}
      </div>

      {result && (
        <div className="flex items-center gap-2 text-sm">
          <span className="badge badge-good">{result.affected} renamed</span>
        </div>
      )}

      <div className="flex gap-2 justify-end pt-1">
        <button
          className="btn-primary flex items-center gap-2"
          onClick={() => mutation.mutate()}
          disabled={!canSubmit}
        >
          <CaseSensitive size={14} /> {mutation.isPending ? "Renaming…" : "Rename Images"}
        </button>
      </div>
    </div>
  );
}
