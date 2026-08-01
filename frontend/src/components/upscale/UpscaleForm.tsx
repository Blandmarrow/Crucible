import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Maximize2 } from "lucide-react";
import toast from "react-hot-toast";
import { upscalingApi, upscaleModelLabel } from "../../api/upscaling";
import { jobsApi } from "../../api/jobs";
import { useJobStore } from "../../store/jobStore";

interface Props {
  datasetId: string;
  imageIds?: string[];
  subfolder?: string;
  qualityFlags?: string[];
  onSuccess?: () => void;
  onCancel?: () => void;
}

// Deliberately silent about `thumbnails_stale`: TopBar owns that warning, for
// every one of these four job types and every screen that starts them, so
// repeating it here would double-toast whoever happened to stay on this form.
function resultToast(result: Record<string, unknown>) {
  const done = Number(result.processed ?? 0);
  const skipped = Number(result.skipped ?? 0);
  const failed = Number(result.failed ?? 0);
  const parts = [`Upscaled ${done} image${done !== 1 ? "s" : ""}`];
  if (skipped > 0) parts.push(`${skipped} skipped`);
  if (failed > 0) parts.push(`${failed} failed`);
  const msg = parts.join(", ");
  if (failed > 0) toast(msg, { icon: "⚠️" });
  else toast.success(msg);
}

export default function UpscaleForm({ datasetId, imageIds, subfolder, qualityFlags, onSuccess, onCancel }: Props) {
  const qc = useQueryClient();

  const [modelPath, setModelPath] = useState("");
  const [replace, setReplace] = useState(false);
  const [targetW, setTargetW] = useState("");
  const [targetH, setTargetH] = useState("");
  const [jobLabel, setJobLabel] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const jobProgress = useJobStore((s) => s.activeJobs.get(jobId ?? ""));

  const { data: models = [], isLoading: modelsLoading } = useQuery({
    queryKey: ["upscale-models"],
    queryFn: upscalingApi.models,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (!jobId || !jobProgress) return;
    if (jobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      const finishedId = jobId;
      setJobId(null);
      jobsApi
        .get(finishedId)
        .then((job) => resultToast(job.result_data ?? {}))
        .catch(() => toast.success("Upscaling complete"));
      onSuccess?.();
    } else if (jobProgress.status === "failed") {
      setJobId(null);
      toast.error("Upscaling failed");
    }
  }, [jobProgress?.status, jobId, datasetId, qc, onSuccess]);

  const runMutation = useMutation({
    mutationFn: () =>
      upscalingApi.run({
        dataset_id: datasetId,
        image_ids: imageIds,
        subfolder,
        quality_flags: qualityFlags,
        model_path: modelPath,
        replace,
        target_width: parseInt(targetW) > 0 ? parseInt(targetW) : null,
        target_height: parseInt(targetH) > 0 ? parseInt(targetH) : null,
        label: jobLabel.trim() || undefined,
      }),
    onSuccess: (data) => {
      if (data.total === 0) {
        toast("No images to upscale");
        return;
      }
      setJobId(data.job_id);
      toast.success(`Upscaling ${data.total} image${data.total !== 1 ? "s" : ""}…`);
    },
    onError: () => toast.error("Failed to start upscaling"),
  });

  const running = !!jobId && jobProgress?.status === "running";

  return (
    <div className="space-y-4">
      {/* Model picker */}
      <div>
        <label className="label">Model</label>
        {modelsLoading ? (
          <p className="text-sm" style={{ color: "var(--fg-mute)" }}>Loading models…</p>
        ) : models.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--fg-mute)" }}>
            No models found. Set <code>UPSCALE_MODELS_DIR</code> in <code>.env</code> or add models to <code>models/upscale_models/</code>.
          </p>
        ) : (
          <select
            className="select w-full"
            value={modelPath}
            onChange={(e) => setModelPath(e.target.value)}
          >
            <option value="">— select a model —</option>
            {models.map((m) => (
              <option key={m.path} value={m.path}>
                {upscaleModelLabel(m)}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Output mode */}
      <div>
        <label className="label">Output</label>
        <div className="flex gap-3">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="radio"
              name="upscale-output"
              checked={!replace}
              onChange={() => setReplace(false)}
            />
            New file
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="radio"
              name="upscale-output"
              checked={replace}
              onChange={() => setReplace(true)}
            />
            Replace original
          </label>
        </div>
      </div>

      {/* Target resolution */}
      <div>
        <label className="label">Target resolution <span style={{ color: "var(--fg-dim)", fontWeight: 400 }}>(optional — upscale then resize down)</span></label>
        <div className="flex items-center gap-2">
          <input
            type="number"
            min="1"
            className="input"
            style={{ width: 80 }}
            placeholder="W px"
            value={targetW}
            onChange={(e) => setTargetW(e.target.value)}
          />
          <span style={{ color: "var(--fg-mute)", fontSize: 13 }}>×</span>
          <input
            type="number"
            min="1"
            className="input"
            style={{ width: 80 }}
            placeholder="H px"
            value={targetH}
            onChange={(e) => setTargetH(e.target.value)}
          />
        </div>
      </div>

      {/* Progress */}
      {running && jobProgress && (
        <div className="progress-pill">
          <span className="pp-dot" />
          <span className="pp-label">
            {jobProgress.current_item ?? "Upscaling…"}
          </span>
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
          disabled={!modelPath || running || runMutation.isPending}
        >
          <Maximize2 size={14} /> {running ? "Upscaling…" : "Run Upscale"}
        </button>
      </div>
    </div>
  );
}
