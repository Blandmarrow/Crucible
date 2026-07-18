import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { detectionApi } from "../../api/detection";
import ConfirmDialog from "../common/ConfirmDialog";

interface Props {
  datasetId: string;
  imageIds?: string[];
  subfolder?: string;
  qualityFlags?: string[];
  disabled?: boolean;
}

export default function DetectionBulkDeleteForm({ datasetId, imageIds, subfolder, qualityFlags, disabled }: Props) {
  const qc = useQueryClient();

  const [selectedLabels, setSelectedLabels] = useState<Set<string>>(new Set());
  const [selectedModels, setSelectedModels] = useState<Set<string>>(new Set());
  const [scoreBelow, setScoreBelow] = useState("");
  const [confirming, setConfirming] = useState(false);

  const scoreBelowNum = scoreBelow.trim() === "" ? null : Math.min(Math.max(parseFloat(scoreBelow), 0), 1);

  const { data: labels = [] } = useQuery({
    queryKey: ["detection-labels", datasetId],
    queryFn: () => detectionApi.labels(datasetId),
  });
  const { data: models = [] } = useQuery({
    queryKey: ["detection-models", datasetId],
    queryFn: () => detectionApi.models(datasetId),
  });

  const params = {
    dataset_id: datasetId,
    image_ids: imageIds,
    subfolder,
    quality_flags: qualityFlags,
    labels: selectedLabels.size > 0 ? [...selectedLabels] : undefined,
    models: selectedModels.size > 0 ? [...selectedModels] : undefined,
    score_below: scoreBelowNum,
  };

  const { data: countData, isFetching: countFetching } = useQuery({
    queryKey: ["detection-bulk-count", datasetId, imageIds ?? null, subfolder ?? null, qualityFlags ?? null, [...selectedLabels], [...selectedModels], scoreBelowNum],
    queryFn: () => detectionApi.bulkDelete({ ...params, dry_run: true }),
    enabled: !disabled,
    staleTime: 5_000,
  });

  const count = countData?.deleted ?? 0;

  const deleteMutation = useMutation({
    mutationFn: () => detectionApi.bulkDelete({ ...params, dry_run: false }),
    onSuccess: (data) => {
      setConfirming(false);
      qc.invalidateQueries({ queryKey: ["detection-labels", datasetId] });
      qc.invalidateQueries({ queryKey: ["detection-models", datasetId] });
      qc.invalidateQueries({ queryKey: ["detection-bulk-count", datasetId] });
      qc.invalidateQueries({ queryKey: ["image"] });
      toast.success(`Deleted ${data.deleted} detection${data.deleted !== 1 ? "s" : ""}`);
    },
    onError: () => { setConfirming(false); toast.error("Failed to delete detections"); },
  });

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key); else next.add(key);
    setter(next);
  };

  return (
    <div className="space-y-4">
      {/* Labels */}
      <div>
        <label className="label">
          Detection labels{selectedLabels.size === 0 ? " — all labels" : ""}
        </label>
        {labels.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--fg-dim)" }}>No detections in this dataset yet.</p>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
            {labels.map(({ label, image_count }) => (
              <button
                key={label}
                className={`btn sm${selectedLabels.has(label) ? " primary" : ""}`}
                onClick={() => toggle(selectedLabels, setSelectedLabels, label)}
                title={`${image_count} image${image_count === 1 ? "" : "s"}`}
              >
                {label}
                <span style={{ fontSize: 10, opacity: 0.7, marginLeft: 4 }}>{image_count}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Models */}
      {models.length > 0 && (
        <div>
          <label className="label">Detection models{selectedModels.size === 0 ? " — all models" : ""}</label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
            {models.map(({ model, image_count }) => (
              <button
                key={model}
                className={`btn sm${selectedModels.has(model) ? " primary" : ""}`}
                onClick={() => toggle(selectedModels, setSelectedModels, model)}
                title={`${image_count} image${image_count === 1 ? "" : "s"}`}
              >
                {model}
                <span style={{ fontSize: 10, opacity: 0.7, marginLeft: 4 }}>{image_count}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Score threshold */}
      <div>
        <label className="label">Score below</label>
        <input
          type="number"
          min="0"
          max="1"
          step="0.05"
          className="input"
          style={{ width: 100 }}
          placeholder="any"
          value={scoreBelow}
          onChange={(e) => setScoreBelow(e.target.value)}
        />
        <p className="text-xs mt-1" style={{ color: "var(--fg-mute)" }}>
          Only detections scoring below this are deleted. Unscored/manual rows never match.
        </p>
      </div>

      {/* Count + action */}
      <div className="flex items-center gap-3 justify-between">
        <p className="text-xs" style={{ color: count === 0 ? "var(--warn)" : "var(--fg-mute)" }}>
          {countFetching ? "Counting…" : `${count.toLocaleString()} detection${count !== 1 ? "s" : ""} will be deleted`}
        </p>
        <button
          className="btn-danger flex items-center gap-2"
          onClick={() => setConfirming(true)}
          disabled={disabled || count === 0 || deleteMutation.isPending}
        >
          <Trash2 size={14} /> Delete {count > 0 ? count.toLocaleString() : ""} detection{count !== 1 ? "s" : ""}
        </button>
      </div>

      {confirming && (
        <ConfirmDialog
          title="Delete detections"
          message={`Permanently delete ${count.toLocaleString()} detection${count !== 1 ? "s" : ""} matching this scope? This cannot be undone.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  );
}
