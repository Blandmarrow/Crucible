import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Scissors } from "lucide-react";
import toast from "react-hot-toast";
import { videosApi } from "../../api/videos";
import { jobsApi } from "../../api/jobs";
import { apiErrorDetail } from "../../utils/apiError";
import { TERMINAL_JOB_STATUSES } from "../../constants/jobs";
import { useJobStore } from "../../store/jobStore";
import type { VideoReextractRequest } from "../../types";

/** Mirrors `VideoReextractRequest.max_long_edge`'s `ge`/`le` on the server. */
const LONG_EDGE_MIN = 64;
const LONG_EDGE_MAX = 16384;

interface Props {
  datasetId: string;
  /** Exactly one scope, mirroring the request. */
  imageIds?: string[];
  videoId?: string;
  subfolder?: string;
  onSuccess?: () => void;
  onCancel?: () => void;
}

/** Human-readable accounting, grouped by reason so 300 identical skips read as
 *  one line rather than three hundred. */
function skipSummary(skipped: { reason: string }[]): string[] {
  const counts = new Map<string, number>();
  for (const s of skipped) counts.set(s.reason, (counts.get(s.reason) ?? 0) + 1);
  return [...counts.entries()].map(([reason, n]) => `${n} skipped (${reason})`);
}

function resultToast(result: Record<string, unknown>) {
  const rewritten = Number(result.rewritten ?? 0);
  const failed = Number(result.failed ?? 0);
  const parts = [`Re-extracted ${rewritten} frame${rewritten !== 1 ? "s" : ""}`];
  if (failed > 0) parts.push(`${failed} failed`);
  const msg = parts.join(", ");
  if (failed > 0) toast(msg, { icon: "⚠️" });
  else toast.success(msg);
  // The stale-scores note travels on `result_data` so it is said at the end as
  // well as before the run — a user who scrolled past it in the form still sees
  // it when the numbers change.
  if (rewritten > 0 && typeof result.note === "string") {
    toast(result.note, { duration: 8000 });
  }
}

/**
 * Pass 2 — re-cut curated frames from their source video at full resolution.
 *
 * Owns its own API call, job ids and cache invalidation, like `UpscaleForm` and
 * `CropToDetectionForm`. The preview endpoint does all the accounting: the
 * selection store holds ids only and a selection can span videos and datasets,
 * so no client-side lineage gate could be honest about what will actually run.
 *
 * Closing mid-run is safe: the jobs are server-side and the adoption effect below
 * re-attaches to them on reopen, folding any live `video_reextract` job for one of
 * the preview's videos back into `jobIds`. That is why there is no persisted key
 * here and pass 1 needs one — pass 2 emits per frame, so a reconnected stream
 * repopulates `jobStore` within a frame, while pass 1's `detecting`/`replacing`
 * phases are long and silent.
 */
export default function ReextractFramesForm({
  datasetId, imageIds, videoId, subfolder, onSuccess, onCancel,
}: Props) {
  const qc = useQueryClient();

  const [format, setFormat] = useState<"jpeg" | "png">("jpeg");
  const [maxLongEdge, setMaxLongEdge] = useState("");
  const [jobLabel, setJobLabel] = useState("");
  const [jobIds, setJobIds] = useState<string[]>([]);

  const activeJobs = useJobStore((s) => s.activeJobs);

  const scope: VideoReextractRequest = useMemo(
    () => (imageIds ? { image_ids: imageIds } : { video_id: videoId, subfolder }),
    [imageIds, videoId, subfolder],
  );

  const { data: preview, error: previewError } = useQuery({
    queryKey: ["reextract-preview", imageIds ?? [], videoId ?? "", subfolder ?? ""],
    queryFn: () => videosApi.reextractPreview(scope),
    staleTime: 0,
  });

  // The videos this dialog's scope resolves to — known from the *preview*, which
  // writes nothing, so a reopened dialog knows what to re-attach to without
  // starting anything.
  const groupVideoIds = useMemo(
    () => new Set((preview?.groups ?? []).map((g) => g.video_id)),
    [preview],
  );

  // Re-attach: fold any live `video_reextract` job for one of those videos into
  // the tracked ids, so closing this dialog mid-run and reopening restores the
  // progress pill, the completion toast and the invalidations below.
  //
  // Adopted **into state** rather than derived into a `trackedIds` list on
  // purpose: the completion effect needs the id to stay tracked once the job goes
  // terminal, and a list filtered on non-terminal would drop it at exactly the
  // moment the toast and the invalidations are owed — `ExtractProgressList`'s
  // vanish-on-complete failure. No loop, either: that effect only removes
  // terminal ids and this one only adds non-terminal ones.
  useEffect(() => {
    const adopt: string[] = [];
    for (const p of activeJobs.values()) {
      if (p.job_type !== "video_reextract" || !p.video_id) continue;
      if (!groupVideoIds.has(p.video_id)) continue;
      if (TERMINAL_JOB_STATUSES.has(p.status)) continue;
      adopt.push(p.job_id);
    }
    if (adopt.length === 0) return;
    setJobIds((prev) => {
      const missing = adopt.filter((id) => !prev.includes(id));
      return missing.length ? [...prev, ...missing] : prev;  // same identity ⇒ no render loop
    });
  }, [activeJobs, groupVideoIds]);

  // One job per video, so an array — `SelectionToolbar`'s `detectJobIds` shape.
  // Iterate the tracked ids whenever activeJobs changes and drop the terminal ones.
  useEffect(() => {
    if (jobIds.length === 0) return;
    const done: string[] = [];
    for (const jobId of jobIds) {
      const progress = activeJobs.get(jobId);
      if (!progress) continue;
      if (progress.status === "completed") {
        done.push(jobId);
        jobsApi
          .get(jobId)
          .then((job) => resultToast(job.result_data ?? {}))
          .catch(() => toast.success("Re-extraction complete"));
      } else if (progress.status === "failed") {
        toast.error(progress.message || "Re-extraction failed");
        done.push(jobId);
      } else if (progress.status === "cancelled") {
        done.push(jobId);
      }
    }
    if (done.length === 0) return;
    // Pass 2 changes width/height/phash and the file itself, so the singular
    // ["image"] key matters as much as the gallery list — an open detail pane
    // would otherwise keep showing the triage dimensions.
    qc.invalidateQueries({ queryKey: ["images", datasetId] });
    qc.invalidateQueries({ queryKey: ["image"] });
    qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
    qc.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
    qc.invalidateQueries({ queryKey: ["duplicates", datasetId] });
    setJobIds((prev) => prev.filter((id) => !done.includes(id)));
    if (done.length === jobIds.length) onSuccess?.();
  }, [activeJobs, jobIds, datasetId, qc, onSuccess]);

  // `max_long_edge` is `ge=64, le=16384` server-side, and an input's `min`/`max`
  // attributes enforce nothing on a typed value — `30` used to reach the API and
  // come back as a raw 422 toast. Empty stays valid: it means native resolution.
  const parsedLongEdge = maxLongEdge.trim() === "" ? null : Number(maxLongEdge);
  const longEdgeInvalid =
    parsedLongEdge !== null &&
    (!Number.isInteger(parsedLongEdge) || parsedLongEdge < LONG_EDGE_MIN || parsedLongEdge > LONG_EDGE_MAX);

  const runMutation = useMutation({
    mutationFn: () =>
      videosApi.reextract({
        ...scope,
        format,
        max_long_edge: parsedLongEdge,
        label: jobLabel.trim() || undefined,
      }),
    onSuccess: (data) => {
      if (data.groups.length === 0) {
        toast("Nothing to re-extract");
        return;
      }
      setJobIds(data.groups.map((g) => g.job_id!).filter(Boolean));
      const videos = data.groups.length;
      toast.success(
        `Re-extracting ${data.eligible} frame${data.eligible !== 1 ? "s" : ""} ` +
        `from ${videos} video${videos !== 1 ? "s" : ""}…`,
      );
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Failed to start re-extraction")),
  });

  const running = jobIds.length > 0;
  const eligible = preview?.eligible ?? 0;
  const jobProgress = jobIds.length > 0 ? activeJobs.get(jobIds[0]) : undefined;

  return (
    <div className="space-y-4">
      {/* Accounting, straight from the endpoint that will do the work */}
      <div className="text-sm" style={{ color: "var(--fg-mute)" }}>
        {/* error → no-data → content, never `isLoading` → content: TanStack v5's
            default `networkMode: "online"` leaves an offline tab `pending` *and*
            `paused`, i.e. `isLoading` false with `data` undefined, which sent the
            old chain into the content branch and took out the pane's
            ErrorBoundary on `preview!.groups`. `!preview` subsumes the loading
            state anyway — loading implies no data. */}
        {previewError ? (
          <p style={{ color: "var(--bad)" }}>
            {apiErrorDetail(previewError, "Could not check these frames")}
          </p>
        ) : !preview ? (
          <p>Checking which frames can be re-extracted…</p>
        ) : (
          <>
            <p style={{ color: eligible > 0 ? "var(--fg)" : "var(--fg-mute)" }}>
              {eligible} frame{eligible !== 1 ? "s" : ""} from{" "}
              {preview.groups.length} video{preview.groups.length !== 1 ? "s" : ""} will be
              re-extracted
            </p>
            {skipSummary(preview.skipped).map((line) => (
              <p key={line} style={{ fontSize: 12 }}>· {line}</p>
            ))}
          </>
        )}
      </div>

      {/* Output format */}
      <div>
        <label className="label">Format</label>
        <div className="flex gap-3">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="radio"
              name="reextract-format"
              checked={format === "jpeg"}
              onChange={() => setFormat("jpeg")}
            />
            JPEG
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="radio"
              name="reextract-format"
              checked={format === "png"}
              onChange={() => setFormat("png")}
            />
            PNG <span style={{ color: "var(--fg-dim)" }}>(lossless, larger)</span>
          </label>
        </div>
      </div>

      {/* Resolution */}
      <div>
        <label className="label">
          Max long edge{" "}
          <span style={{ color: "var(--fg-dim)", fontWeight: 400 }}>
            (optional — empty means the video's native resolution)
          </span>
        </label>
        <input
          type="number"
          min={LONG_EDGE_MIN}
          max={LONG_EDGE_MAX}
          className="input"
          style={{ width: 110 }}
          placeholder="native"
          value={maxLongEdge}
          onChange={(e) => setMaxLongEdge(e.target.value)}
          aria-invalid={longEdgeInvalid || undefined}
        />
        {longEdgeInvalid && (
          <p className="text-xs" style={{ color: "var(--bad)", margin: "4px 0 0" }}>
            Must be a whole number between {LONG_EDGE_MIN} and {LONG_EDGE_MAX}, or empty for native.
          </p>
        )}
      </div>

      <p className="text-xs" style={{ color: "var(--fg-mute)" }}>
        Quality scores were measured on the triage frames and are kept as they are. Re-run
        scoring if you want scores that reflect the full-resolution images.
      </p>

      {/* Progress */}
      {running && jobProgress && (
        <>
          <div className="progress-pill">
            <span className="pp-dot" />
            <span className="pp-label">{jobProgress.message ?? "Re-extracting…"}</span>
            <div className="pp-bar">
              <div className="pp-fill" style={{ width: `${jobProgress.percent ?? 0}%` }} />
            </div>
            <span className="pp-num">{jobProgress.done}/{jobProgress.total}</span>
          </div>
          <p style={{ fontSize: 11.5, color: "var(--fg-dim)", margin: 0 }}>
            Closing this window is safe — the jobs keep running, and reopening reattaches to them.
          </p>
        </>
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
        {/* Never disabled while running: the jobs are server-side and the
            adoption effect re-attaches on reopen, so leaving is reversible. */}
        {onCancel && (
          <button className="btn-ghost" onClick={onCancel}>
            {running ? "Close" : "Cancel"}
          </button>
        )}
        <button
          className="btn-primary flex items-center gap-2"
          onClick={() => runMutation.mutate()}
          disabled={eligible === 0 || running || runMutation.isPending || longEdgeInvalid}
        >
          <Scissors size={14} /> {running ? "Re-extracting…" : "Re-extract"}
        </button>
      </div>
    </div>
  );
}
