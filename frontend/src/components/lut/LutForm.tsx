import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Palette } from "lucide-react";
import toast from "react-hot-toast";
import { lutApi } from "../../api/lut";
import { useJobStore } from "../../store/jobStore";

interface Props {
  datasetId: string;
  imageIds?: string[];
  subfolder?: string;
  onSuccess?: () => void;
  onCancel?: () => void;
}

export default function LutForm({ datasetId, imageIds, subfolder, onSuccess, onCancel }: Props) {
  const qc = useQueryClient();

  const [lutPath, setLutPath] = useState("");
  const [intensity, setIntensity] = useState(100);
  const [replace, setReplace] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  const jobProgress = useJobStore((s) => s.activeJobs.get(jobId ?? ""));

  const { data: models = [], isLoading: modelsLoading } = useQuery({
    queryKey: ["lut-models"],
    queryFn: lutApi.models,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (!jobId || !jobProgress) return;
    if (jobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setJobId(null);
      toast.success("LUT applied");
      onSuccess?.();
    } else if (jobProgress.status === "failed") {
      setJobId(null);
      toast.error("LUT grading failed");
    }
  }, [jobProgress?.status, jobId, datasetId, qc, onSuccess]);

  const runMutation = useMutation({
    mutationFn: () =>
      lutApi.run({
        dataset_id: datasetId,
        image_ids: imageIds,
        subfolder,
        lut_path: lutPath,
        intensity: intensity / 100,
        replace,
      }),
    onSuccess: (data) => {
      if (data.total === 0) {
        toast("No images to process");
        return;
      }
      setJobId(data.job_id);
      toast.success(`Applying LUT to ${data.total} image${data.total !== 1 ? "s" : ""}…`);
    },
    onError: () => toast.error("Failed to start LUT grading"),
  });

  const running = !!jobId && jobProgress?.status === "running";

  return (
    <div className="space-y-4">
      {/* LUT picker */}
      <div>
        <label className="label">LUT File</label>
        {modelsLoading ? (
          <p className="text-sm" style={{ color: "var(--fg-mute)" }}>Loading LUTs…</p>
        ) : models.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--fg-mute)" }}>
            No LUTs found. Add <code>.cube</code> or <code>.3dl</code> files to <code>models/lut/</code>.
          </p>
        ) : (
          <select
            className="select w-full"
            value={lutPath}
            onChange={(e) => setLutPath(e.target.value)}
          >
            <option value="">— select a LUT —</option>
            {models.map((m) => (
              <option key={m.path} value={m.path}>
                {m.name} <span style={{ color: "var(--fg-mute)" }}>({m.format})</span>
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Intensity */}
      <div>
        <label className="label">
          Intensity
          <span style={{ color: "var(--fg-mute)", fontWeight: 400, marginLeft: 8 }}>
            {intensity}%
          </span>
        </label>
        <input
          type="range"
          min={0}
          max={100}
          value={intensity}
          onChange={(e) => setIntensity(Number(e.target.value))}
          style={{ width: "100%" }}
        />
      </div>

      {/* Output mode */}
      <div>
        <label className="label">Output</label>
        <div className="flex gap-3">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="radio"
              name="lut-output"
              checked={!replace}
              onChange={() => setReplace(false)}
            />
            New file
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="radio"
              name="lut-output"
              checked={replace}
              onChange={() => setReplace(true)}
            />
            Replace original
          </label>
        </div>
      </div>

      {/* Progress */}
      {running && jobProgress && (
        <div className="progress-pill">
          <span className="pp-dot" />
          <span className="pp-label">
            {jobProgress.current_item ?? "Applying LUT…"}
          </span>
          <div className="pp-bar">
            <div className="pp-fill" style={{ width: `${jobProgress.percent ?? 0}%` }} />
          </div>
          <span className="pp-num">{jobProgress.done}/{jobProgress.total}</span>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 justify-end">
        {onCancel && (
          <button className="btn-ghost" onClick={onCancel} disabled={running}>
            Cancel
          </button>
        )}
        <button
          className="btn-primary flex items-center gap-2"
          onClick={() => runMutation.mutate()}
          disabled={!lutPath || running || runMutation.isPending}
        >
          <Palette size={14} /> {running ? "Applying…" : "Apply LUT"}
        </button>
      </div>
    </div>
  );
}
