import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Eraser } from "lucide-react";
import toast from "react-hot-toast";
import { detectionApi } from "../../api/detection";
import { datasetsApi } from "../../api/datasets";
import { inpaintApi } from "../../api/inpaint";
import { jobsApi } from "../../api/jobs";
import { useJobStore } from "../../store/jobStore";
import { apiErrorDetail } from "../../utils/apiError";

// Destination-subfolder sentinels (new-file mode only)
const DEST_SAME = "__same__";     // inherit source subfolder → omit dest_subfolder
const DEST_ROOT = "__root__";     // root → send ""
const DEST_CUSTOM = "__custom__"; // free-text new subfolder

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
  const done = Number(result.inpainted ?? 0);
  const noDet = Number(result.skipped_no_detection ?? 0);
  // Separate from `skipped_no_detection` on purpose: this one is a stray file on
  // disk under the name the PNG fallback wants, nothing to do with detection.
  const nameTaken = Number(result.skipped_name_taken ?? 0);
  const failed = Number(result.failed ?? 0);
  const parts = [`Removed watermarks from ${done} image${done !== 1 ? "s" : ""}`];
  if (noDet > 0) parts.push(`${noDet} skipped (no detections)`);
  if (nameTaken > 0) parts.push(`${nameTaken} skipped (name already taken)`);
  if (failed > 0) parts.push(`${failed} failed`);
  // Deliberately silent about `thumbnails_stale`: TopBar owns that warning for
  // every job type, and repeating it here double-toasts.
  const msg = parts.join(", ");
  failed > 0 ? toast(msg, { icon: "⚠️" }) : toast.success(msg);
}

export default function InpaintForm({ datasetId, imageIds, subfolder, qualityFlags, availableLabels, disabled, onSuccess, onCancel }: Props) {
  const qc = useQueryClient();

  const [selectedLabels, setSelectedLabels] = useState<Set<string>>(new Set());
  const [dilatePx, setDilatePx] = useState("6");
  const [replace, setReplace] = useState(true);
  const [destSelect, setDestSelect] = useState(DEST_SAME);
  const [destCustom, setDestCustom] = useState("");
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

  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId),
  });

  // Resolve the destination subfolder for the payload. `undefined` = omit the
  // field (inherit source subfolder); "" = root; any string = that subfolder.
  const resolveDestSubfolder = (): string | undefined => {
    if (replace) return undefined;
    if (destSelect === DEST_SAME) return undefined;
    if (destSelect === DEST_ROOT) return "";
    if (destSelect === DEST_CUSTOM) {
      const trimmed = destCustom.trim();
      return trimmed === "" ? undefined : trimmed; // empty → fall back to same-as-source
    }
    return destSelect; // an existing subfolder path
  };

  useEffect(() => {
    if (!jobId || !jobProgress) return;
    if (jobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      // Counts come from the job row, never off the SSE payload: `broadcaster.emit`
      // drops events once a subscriber's 200-slot queue fills, so the terminal
      // event carrying `result_data` is exactly the one a busy run loses.
      const finishedId = jobId;
      setJobId(null);
      jobsApi
        .get(finishedId)
        .then((job) => resultToast(job.result_data ?? {}))
        .catch(() => toast.success("Watermark removal complete"));
      onSuccess?.();
    } else if (jobProgress.status === "failed") {
      setJobId(null);
      toast.error("Watermark removal failed");
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
      inpaintApi.run({
        dataset_id: datasetId,
        image_ids: imageIds,
        subfolder,
        quality_flags: qualityFlags,
        labels: selectedLabels.size > 0 ? [...selectedLabels] : undefined,
        dilate_px: Math.min(Math.max(parseInt(dilatePx, 10) || 0, 0), 64),
        replace,
        dest_subfolder: resolveDestSubfolder(),
        label: jobLabel.trim() || undefined,
      }),
    onSuccess: (data) => {
      if (data.total === 0) {
        toast("No images with matching detections");
        return;
      }
      setJobId(data.job_id);
      let msg = `Removing watermarks from ${data.total} image${data.total !== 1 ? "s" : ""}…`;
      if (data.skipped > 0) msg += ` — ${data.skipped} skipped (no matching detections)`;
      toast.success(msg);
    },
    // The server's own reason has to reach the user here: a missing-weights
    // FileNotFoundError and a 507 out-of-space both arrive as a `detail` string,
    // and "Failed to start" alone tells nobody which one it was.
    onError: (err) => toast.error(apiErrorDetail(err, "Failed to start watermark removal")),
  });

  const running = !!jobId && jobProgress?.status === "running";
  const noDetections = !labelsLoading && detectionLabels.length === 0;

  return (
    <div className="space-y-4">
      <p className="text-sm" style={{ color: "var(--fg-dim)" }}>
        Paints the selected detections out of the image with LaMa inpainting. Run a
        detection pass for <code>watermark</code> first — this consumes what that
        found, and deletes those detections once the region is gone. The first run
        downloads a 196 MB model.
      </p>

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

      {/* Mask dilation */}
      <div>
        <label className="label">
          Mask padding px{" "}
          <span style={{ color: "var(--fg-dim)", fontWeight: 400 }}>
            (grows the mask — raise it if a halo survives)
          </span>
        </label>
        <input
          type="number"
          min="0"
          max="64"
          step="1"
          className="input"
          style={{ width: 80 }}
          value={dilatePx}
          onChange={(e) => setDilatePx(e.target.value)}
        />
      </div>

      {/* Output mode */}
      <div>
        <label className="label">Output</label>
        <div className="flex gap-3">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="radio" name="inpaint-output" checked={replace} onChange={() => setReplace(true)} />
            Replace original
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="radio" name="inpaint-output" checked={!replace} onChange={() => setReplace(false)} />
            New file
          </label>
        </div>
      </div>

      {/* Destination subfolder (new-file mode only) */}
      {!replace && (
        <div>
          <label className="label">Destination subfolder</label>
          <select
            className="select"
            value={destSelect}
            onChange={(e) => setDestSelect(e.target.value)}
          >
            <option value={DEST_SAME}>Same as source</option>
            <option value={DEST_ROOT}>— root (no subfolder) —</option>
            {subfolders.filter((sf) => sf.path !== "").map((sf) => (
              <option key={sf.path} value={sf.path}>
                {sf.path} ({sf.image_count} image{sf.image_count !== 1 ? "s" : ""})
              </option>
            ))}
            <option value={DEST_CUSTOM}>New subfolder…</option>
          </select>
          {destSelect === DEST_CUSTOM && (
            <input
              className="input"
              style={{ marginTop: 6 }}
              placeholder="New subfolder path (empty = same as source)"
              value={destCustom}
              onChange={(e) => setDestCustom(e.target.value)}
              autoFocus
            />
          )}
        </div>
      )}

      {/* Progress */}
      {running && jobProgress && (
        <div className="progress-pill">
          <span className="pp-dot" />
          <span className="pp-label">{jobProgress.current_item ?? "Removing…"}</span>
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
          <Eraser size={14} /> {running ? "Removing…" : "Remove Watermark"}
        </button>
      </div>
    </div>
  );
}
