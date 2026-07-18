import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Crop } from "lucide-react";
import toast from "react-hot-toast";
import { detectionApi } from "../../api/detection";
import { jobsApi } from "../../api/jobs";
import { ASPECT_PRESETS } from "../../constants/aspectRatios";
import { useJobStore } from "../../store/jobStore";

interface Props {
  datasetId: string;
  imageIds?: string[];
  subfolder?: string;
  qualityFlags?: string[];
  // When set, these labels are offered instead of the dataset-wide label query —
  // used by ImageDetailPage to show only the current image's labels (count =
  // detections, not images).
  availableLabels?: { label: string; count: number }[];
  disabled?: boolean;
  onSuccess?: () => void;
  onCancel?: () => void;
}

function resultToast(result: Record<string, unknown>) {
  const cropped = Number(result.cropped ?? 0);
  const noDet = Number(result.skipped_no_detection ?? 0);
  const noop = Number(result.skipped_noop ?? 0);
  const failed = Number(result.failed ?? 0);
  const parts = [`Cropped ${cropped} image${cropped !== 1 ? "s" : ""}`];
  if (noDet > 0) parts.push(`${noDet} skipped (no detections)`);
  if (noop > 0) parts.push(`${noop} unchanged`);
  if (failed > 0) parts.push(`${failed} failed`);
  const msg = parts.join(", ");
  failed > 0 ? toast(msg, { icon: "⚠️" }) : toast.success(msg);
}

export default function CropToDetectionForm({ datasetId, imageIds, subfolder, qualityFlags, availableLabels, disabled, onSuccess, onCancel }: Props) {
  const qc = useQueryClient();

  const [selectedLabels, setSelectedLabels] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"union" | "largest">("union");
  const [paddingPct, setPaddingPct] = useState("5");
  const [aspect, setAspect] = useState<number | undefined>(undefined);
  const [replace, setReplace] = useState(false);
  const [jobLabel, setJobLabel] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const jobProgress = useJobStore((s) => s.activeJobs.get(jobId ?? ""));

  const { data: datasetLabels = [], isLoading: labelsQueryLoading } = useQuery({
    queryKey: ["detection-labels", datasetId],
    queryFn: () => detectionApi.labels(datasetId),
    enabled: !availableLabels,
  });
  const detectionLabels = availableLabels ?? datasetLabels.map(({ label, image_count }) => ({ label, count: image_count }));
  const labelsLoading = !availableLabels && labelsQueryLoading;
  const countNoun = availableLabels ? "detection" : "image";

  useEffect(() => {
    if (!jobId || !jobProgress) return;
    if (jobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      const finishedId = jobId;
      setJobId(null);
      jobsApi
        .get(finishedId)
        .then((job) => resultToast(job.result_data ?? {}))
        .catch(() => toast.success("Cropping complete"));
      onSuccess?.();
    } else if (jobProgress.status === "failed") {
      setJobId(null);
      toast.error("Cropping failed");
    }
  }, [jobProgress?.status, jobId, datasetId, qc, onSuccess]);

  const toggleLabel = (label: string) => {
    setSelectedLabels((prev) => {
      const next = new Set(prev);
      next.has(label) ? next.delete(label) : next.add(label);
      return next;
    });
  };

  const runMutation = useMutation({
    mutationFn: () =>
      detectionApi.cropToDetection({
        dataset_id: datasetId,
        image_ids: imageIds,
        subfolder,
        quality_flags: qualityFlags,
        labels: selectedLabels.size > 0 ? [...selectedLabels] : undefined,
        mode,
        padding_pct: Math.min(Math.max(parseFloat(paddingPct) || 0, 0), 100),
        target_ar: aspect ?? null,
        replace,
        label: jobLabel.trim() || undefined,
      }),
    onSuccess: (data) => {
      if (data.total === 0) {
        toast("No images with matching detections");
        return;
      }
      setJobId(data.job_id);
      let msg = `Cropping ${data.total} image${data.total !== 1 ? "s" : ""}…`;
      if (data.skipped > 0) msg += ` — ${data.skipped} skipped (no matching detections)`;
      toast.success(msg);
    },
    onError: () => toast.error("Failed to start cropping"),
  });

  const running = !!jobId && jobProgress?.status === "running";
  const noDetections = !labelsLoading && detectionLabels.length === 0;

  return (
    <div className="space-y-4">
      {/* Detection labels */}
      <div>
        <label className="label">
          Detection labels{selectedLabels.size === 0 ? " — none selected, all labels used" : ""}
        </label>
        {labelsLoading ? (
          <p className="text-sm" style={{ color: "var(--fg-mute)" }}>Loading labels…</p>
        ) : noDetections ? (
          <p className="text-sm" style={{ color: "var(--fg-dim)" }}>
            No detections in this dataset yet — run object detection first.
          </p>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
            {detectionLabels.map(({ label, count }) => (
              <button
                key={label}
                className={`btn sm${selectedLabels.has(label) ? " primary" : ""}`}
                onClick={() => toggleLabel(label)}
                title={`${count} ${countNoun}${count === 1 ? "" : "s"}`}
              >
                {label}
                <span style={{ fontSize: 10, opacity: 0.7, marginLeft: 4 }}>{count}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Crop box mode */}
      <div>
        <label className="label">Crop box</label>
        <div className="flex gap-3">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="radio" name="detcrop-mode" checked={mode === "union"} onChange={() => setMode("union")} />
            Union of all matches
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="radio" name="detcrop-mode" checked={mode === "largest"} onChange={() => setMode("largest")} />
            Largest match
          </label>
        </div>
      </div>

      {/* Padding + aspect */}
      <div className="flex items-end gap-4">
        <div>
          <label className="label">Padding %</label>
          <input
            type="number"
            min="0"
            max="100"
            step="1"
            className="input"
            style={{ width: 80 }}
            value={paddingPct}
            onChange={(e) => setPaddingPct(e.target.value)}
          />
        </div>
        <div>
          <label className="label">Aspect ratio <span style={{ color: "var(--fg-dim)", fontWeight: 400 }}>(grow-only snap)</span></label>
          <select
            className="select"
            value={aspect ?? ""}
            onChange={(e) => setAspect(e.target.value ? Number(e.target.value) : undefined)}
          >
            <option value="">Free</option>
            {ASPECT_PRESETS.map(({ label, value }) => (
              <option key={label} value={value}>{label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Output mode */}
      <div>
        <label className="label">Output</label>
        <div className="flex gap-3">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="radio" name="detcrop-output" checked={!replace} onChange={() => setReplace(false)} />
            New file
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="radio" name="detcrop-output" checked={replace} onChange={() => setReplace(true)} />
            Replace original
          </label>
        </div>
      </div>

      {/* Progress */}
      {running && jobProgress && (
        <div className="progress-pill">
          <span className="pp-dot" />
          <span className="pp-label">{jobProgress.current_item ?? "Cropping…"}</span>
          <div className="pp-bar">
            <div className="pp-fill" style={{ width: `${jobProgress.percent ?? 0}%` }} />
          </div>
          <span className="pp-num">{jobProgress.done}/{jobProgress.total}</span>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 justify-end items-center">
        <input
          className="input"
          type="text"
          placeholder="Job label (optional)"
          value={jobLabel}
          onChange={(e) => setJobLabel(e.target.value)}
          style={{ flex: 1, fontSize: 12 }}
          title="Optional name shown in the job queue"
        />
        {onCancel && (
          <button className="btn-ghost" onClick={onCancel} disabled={running}>
            Cancel
          </button>
        )}
        <button
          className="btn-primary flex items-center gap-2"
          onClick={() => runMutation.mutate()}
          disabled={disabled || noDetections || running || runMutation.isPending}
        >
          <Crop size={14} /> {running ? "Cropping…" : "Run Crop"}
        </button>
      </div>
    </div>
  );
}
