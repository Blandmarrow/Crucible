import { useState, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import toast from "react-hot-toast";
import { imagesApi } from "../../api/images";
import { jobsApi } from "../../api/jobs";
import { apiErrorDetail } from "../../utils/apiError";
import { useJobStore } from "../../store/jobStore";

interface Props {
  datasetId: string;
  imageIds?: string[];
  subfolder?: string;
  qualityFlags?: string[];
  disabled?: boolean;
  onSuccess?: () => void;
  onCancel?: () => void;
}

function resultToast(result: Record<string, unknown>) {
  const done = Number(result.regenerated ?? 0);
  const failed = Number(result.failed ?? 0);
  const skipped = Number(result.skipped ?? 0);
  const parts = [`Rebuilt ${done} thumbnail${done !== 1 ? "s" : ""}`];
  if (skipped > 0) parts.push(`${skipped} skipped`);
  if (failed > 0) parts.push(`${failed} failed`);
  const msg = parts.join(", ");
  if (failed > 0) toast(msg, { icon: "⚠️" });
  else toast.success(msg);
}

export default function RegenerateThumbnailsForm({
  datasetId, imageIds, subfolder, qualityFlags, disabled, onSuccess, onCancel,
}: Props) {
  const qc = useQueryClient();
  const [jobLabel, setJobLabel] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const jobProgress = useJobStore((s) => s.activeJobs.get(jobId ?? ""));

  useEffect(() => {
    if (!jobId || !jobProgress) return;
    if (jobProgress.status === "completed") {
      // The tiles themselves are cache-busted by the `?v=` param, which moves
      // because the job bumps `updated_at` — so what has to refetch is the row
      // carrying that timestamp, not the image.
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["image"] });
      const finishedId = jobId;
      setJobId(null);
      jobsApi
        .get(finishedId)
        .then((job) => resultToast(job.result_data ?? {}))
        .catch(() => toast.success("Thumbnails rebuilt"));
      onSuccess?.();
    } else if (jobProgress.status === "failed") {
      setJobId(null);
      toast.error(jobProgress.message || "Rebuilding thumbnails failed");
    }
  }, [jobProgress?.status, jobId, datasetId, qc, onSuccess]);

  const runMutation = useMutation({
    mutationFn: () =>
      imagesApi.bulkThumbnails(datasetId, {
        imageIds,
        subfolder,
        qualityFlags,
        label: jobLabel.trim() || undefined,
      }),
    onSuccess: (data) => {
      if (data.total === 0) {
        toast("No images in this scope");
        return;
      }
      setJobId(data.job_id);
      toast.success(`Rebuilding ${data.total} thumbnail${data.total !== 1 ? "s" : ""}…`);
    },
    // The 507 is the interesting one: this is the repair you run *because* the
    // volume filled up, so its detail names the shortfall and must be shown.
    onError: (err) => toast.error(apiErrorDetail(err, "Failed to start rebuilding thumbnails")),
  });

  const running = !!jobId && jobProgress?.status === "running";

  return (
    <div className="space-y-4">
      <p className="text-sm" style={{ color: "var(--fg-mute)" }}>
        Re-cuts the preview thumbnail for every image in the scope above. Use this
        when an upscale, LUT, crop or re-extraction run reported that some previews
        are out of date — the images themselves are already correct, only the small
        preview the gallery draws is stale.
      </p>

      {running && jobProgress && (
        <div className="progress-pill">
          <span className="pp-dot" />
          <span className="pp-label">{jobProgress.current_item ?? "Rebuilding…"}</span>
          <div className="pp-bar">
            <div className="pp-fill" style={{ width: `${jobProgress.percent ?? 0}%` }} />
          </div>
          <span className="pp-num">{jobProgress.done}/{jobProgress.total}</span>
        </div>
      )}

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
          disabled={disabled || running || runMutation.isPending}
        >
          <RefreshCw size={14} /> {running ? "Rebuilding…" : "Rebuild Thumbnails"}
        </button>
      </div>
    </div>
  );
}
