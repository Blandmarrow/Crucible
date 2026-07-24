import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ScanSearch } from "lucide-react";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../../utils/apiError";
import { detectionApi } from "../../api/detection";
import { invalidateDetectionQueries } from "../../utils/detectionQueries";
import { useJobStore } from "../../store/jobStore";
import { detectionModelFamily } from "../../constants/detectionModels";

interface Props {
  datasetId: string;
  imageIds?: string[];
  subfolder?: string;
  qualityFlags?: string[];
  disabled?: boolean;
}

/**
 * Inline bulk form to run object detection across a dataset scope (all /
 * subfolder / exclude-flags / selected). Mirrors the Detect modal in
 * SelectionToolbar but without a pop-up; SAM 2.1 is text-prompt only here
 * (point prompts are per-image). Job tracking uses the id-list pattern so
 * multiple runs can be queued.
 */
export default function DetectionRunForm({ datasetId, imageIds, subfolder, qualityFlags, disabled }: Props) {
  const qc = useQueryClient();

  const [model, setModel] = useState("florence2_large");
  const [task, setTask] = useState("<OD>");
  const [prompt, setPrompt] = useState("");
  const [useCaptions, setUseCaptions] = useState(false);
  const [overwrite, setOverwrite] = useState(true);
  const [syncWatermark, setSyncWatermark] = useState(false);
  const [minProb, setMinProb] = useState(0.5);
  const [jobLabel, setJobLabel] = useState("");
  const [jobIds, setJobIds] = useState<string[]>([]);

  const activeJobs = useJobStore((s) => s.activeJobs);

  // Track a list of detection jobs so multiple runs can be queued; drop any
  // that reached a terminal status (toasting completed/failed, silent cancel).
  useEffect(() => {
    if (jobIds.length === 0) return;
    const done: string[] = [];
    for (const jobId of jobIds) {
      const progress = activeJobs.get(jobId);
      if (!progress) continue;
      if (progress.status === "completed") {
        invalidateDetectionQueries(qc, datasetId);
        qc.invalidateQueries({ queryKey: ["image"] });
        // Gallery too (matches SelectionToolbar): watermark-sync runs change
        // flag badges/filters, which TopBar's detection branch doesn't cover.
        qc.invalidateQueries({ queryKey: ["images", datasetId] });
        toast.success("Detection complete");
        done.push(jobId);
      } else if (progress.status === "failed") {
        toast.error(progress.message || "Detection failed");
        done.push(jobId);
      } else if (progress.status === "cancelled") {
        done.push(jobId);
      }
    }
    if (done.length > 0) {
      setJobIds((prev) => prev.filter((id) => !done.includes(id)));
    }
  }, [activeJobs, jobIds, datasetId, qc]);

  // Effective task for the model family (Florence uses the task select;
  // NudeNet/SAM have a fixed task).
  const effectiveTask =
    model === "nudenet" ? "nudenet" : model === "sam2" || model === "sam3" ? "text_prompt" : task;

  // Watermark-flag sync is only meaningful for text-prompt grounding tasks.
  const syncEligible = model === "sam2" || model === "sam3" || task === "<CAPTION_TO_PHRASE_GROUNDING>";

  const isFlorence = model !== "nudenet" && model !== "sam2" && model !== "sam3";
  const showPrompt =
    (isFlorence && task === "<CAPTION_TO_PHRASE_GROUNDING>" && !useCaptions) ||
    model === "sam2" ||
    model === "sam3";
  const promptRequired = showPrompt && !prompt.trim();

  const runMutation = useMutation({
    mutationFn: () =>
      detectionApi.run({
        dataset_id: datasetId,
        image_ids: imageIds,
        subfolder,
        quality_flags: qualityFlags,
        model,
        task: effectiveTask,
        custom_prompt: useCaptions ? "" : prompt,
        use_caption_as_prompt: useCaptions,
        overwrite,
        min_prob: minProb,
        sync_watermark_flag: syncEligible && syncWatermark,
        label: jobLabel.trim() || undefined,
      }),
    onSuccess: (data) => {
      if (data.job_id) {
        setJobIds((prev) => [...prev, data.job_id!]);
        toast.success("Detection queued");
      } else {
        toast("No images to process");
      }
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Failed to start detection"));
    },
  });

  return (
    <div className="space-y-4">
      <div>
        <label className="label">Model</label>
        <select
          className="select w-full"
          value={model}
          onChange={(e) => {
            const m = e.target.value;
            const familyChanged = detectionModelFamily(m) !== detectionModelFamily(model);
            setModel(m);
            // Only reset task/prompt/use-captions when the model family changes.
            if (familyChanged) {
              setTask(m === "sam2" || m === "sam3" ? "text_prompt" : "<OD>");
              setPrompt("");
              setUseCaptions(false);
            }
          }}
        >
          <option value="florence2_large">Florence-2 Large</option>
          <option value="florence2_promptgen">Florence-2 PromptGen</option>
          <option value="nudenet">NudeNet (NSFW regions)</option>
          <option value="sam2">SAM 2.1 + Grounding DINO (segmentation)</option>
          <option value="sam3">SAM 3 (text-prompt segmentation)</option>
        </select>
      </div>

      {model === "nudenet" && (
        <div>
          <label className="label">Min confidence: {minProb.toFixed(2)}</label>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={minProb}
            onChange={(e) => setMinProb(Number(e.target.value))}
            className="w-full"
          />
        </div>
      )}

      {isFlorence && (
        <div>
          <label className="label">Task</label>
          <select
            className="select w-full"
            value={task}
            onChange={(e) => { setTask(e.target.value); setPrompt(""); setUseCaptions(false); }}
          >
            <option value="<OD>">Object Detection (auto-detect everything)</option>
            <option value="<CAPTION_TO_PHRASE_GROUNDING>">Grounded Caption (draw boxes around phrases)</option>
          </select>
        </div>
      )}

      {(showPrompt || (isFlorence && task === "<CAPTION_TO_PHRASE_GROUNDING>")) && (
        <div className="space-y-2">
          {isFlorence && task === "<CAPTION_TO_PHRASE_GROUNDING>" && (
            <label className="flex items-center gap-2 cursor-pointer text-sm">
              <input
                type="checkbox"
                checked={useCaptions}
                onChange={(e) => { setUseCaptions(e.target.checked); setPrompt(""); }}
              />
              Use each image's existing caption as prompt
            </label>
          )}
          {showPrompt && (
            <>
              <label className="label">
                {model === "sam2" || model === "sam3" ? "Text prompt" : "Caption to ground"}
              </label>
              <input
                className="input"
                placeholder={model === "sam2" || model === "sam3" ? "e.g. face, hand, watermark" : "e.g. a cat sitting on a dog"}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
              />
              <p className="text-xs text-gray-500">
                {model === "sam2" || model === "sam3"
                  ? "Every instance of each phrase gets a segmentation mask. Separate multiple phrases with commas."
                  : "Florence-2 will draw boxes around the phrases from this caption."}
              </p>
            </>
          )}
          {useCaptions && (
            <p className="text-xs text-gray-500">Images without a caption will be skipped.</p>
          )}
        </div>
      )}

      <label className="flex items-center gap-2 cursor-pointer text-sm">
        <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
        Overwrite this model's existing detections
      </label>

      {syncEligible && (
        <label
          className="flex items-center gap-2 cursor-pointer text-sm"
          title="After the run, set the watermark flag on images where a region was found and clear it on images scanned clean. Only images actually scanned are updated."
        >
          <input type="checkbox" checked={syncWatermark} onChange={(e) => setSyncWatermark(e.target.checked)} />
          Sync watermark flag from results
        </label>
      )}

      <input
        className="input w-full"
        type="text"
        placeholder="Job label (optional)"
        value={jobLabel}
        onChange={(e) => setJobLabel(e.target.value)}
        style={{ fontSize: 12 }}
        title="Optional name shown in the job queue"
      />

      <div className="flex justify-end">
        <button
          className="btn-primary flex items-center gap-2"
          onClick={() => runMutation.mutate()}
          disabled={disabled || runMutation.isPending || promptRequired}
        >
          <ScanSearch size={14} /> Run Detection
        </button>
      </div>
    </div>
  );
}
