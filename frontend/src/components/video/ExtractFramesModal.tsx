import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { datasetsApi } from "../../api/datasets";
import { jobsApi } from "../../api/jobs";
import { videosApi } from "../../api/videos";
import { useModalBehavior } from "../../hooks/useModalBehavior";
import { extractPhaseLabel, useVideoExtractJobs } from "../../hooks/useVideoExtractJobs";
import { apiErrorDetail } from "../../utils/apiError";
import { formatDuration } from "../../utils/duration";
import JobProgressBar from "../common/JobProgressBar";
import CropOverlay from "./CropOverlay";
import TrimBar from "./TrimBar";
import type { CropRect, JobProgress, Video, VideoExtractResult, VideoProbeResult } from "../../types";

interface Props {
  datasetId: string | undefined;
  /** Batch-capable from the start; the two entry points differ only in what they
   *  pass here. One parameter set is applied across the whole series. */
  videos: Video[];
  onClose: () => void;
}

const PROBE_SAMPLES = 8;
const PROBE_MAX_EDGE = 640;
const PROBE_DEBOUNCE_MS = 400;

const SUB_AUTO = "__auto__";
const SUB_CUSTOM = "__custom__";

/** The rect stored on a Video row, or null when it carries no crop. */
function storedCrop(v: Video): CropRect | null {
  return v.crop_w && v.crop_h ? { x: v.crop_x ?? 0, y: v.crop_y ?? 0, w: v.crop_w, h: v.crop_h } : null;
}

/**
 * Turn one or more videos into frames — two steps, then a running view.
 *
 * Step 1 probes `videos[0]` only. A batch applies a single parameter set across
 * the series, so probing every video would spend seeks on samples nothing shows;
 * the header says which video is being previewed and how many the settings cover.
 *
 * Backdrop-click closing stays **off** (the `useModalBehavior` default): this
 * modal holds unsaved probe decisions, and a stray click on the overlay would
 * throw away a crop the user spent time dragging. Escape still closes — that is
 * an explicit gesture.
 *
 * Once submitted, closing is free. The jobs are queued server-side and this modal
 * re-attaches to them through `useVideoExtractJobs`, which reads `jobStore` and
 * a persisted id per video rather than owning an SSE subscription.
 */
export default function ExtractFramesModal({ datasetId, videos, onClose }: Props) {
  const primary = videos[0];
  const batch = videos.length > 1;

  // ── Step 1 state ───────────────────────────────────────────────────────────
  const [step, setStep] = useState<1 | 2>(1);
  const [trimStart, setTrimStart] = useState(primary.trim_start_ms);
  const [trimEnd, setTrimEnd] = useState(primary.trim_end_ms);
  const [debouncedTrim, setDebouncedTrim] = useState({ start: primary.trim_start_ms, end: primary.trim_end_ms });
  const [crop, setCrop] = useState<CropRect | null>(() => storedCrop(primary));
  const [deinterlace, setDeinterlace] = useState<"" | "bwdif">(primary.deinterlace === "bwdif" ? "bwdif" : "");
  const [selectedSample, setSelectedSample] = useState(0);

  // ── Step 2 state ───────────────────────────────────────────────────────────
  const [framesPerShot, setFramesPerShot] = useState(1);
  const [pick, setPick] = useState<"sharpest" | "middle">("sharpest");
  const [candidates, setCandidates] = useState(5);
  const [longEdge, setLongEdge] = useState(1024);
  const [mode, setMode] = useState<"add" | "new_subfolder" | "replace">("new_subfolder");
  const [subSelect, setSubSelect] = useState(SUB_AUTO);
  const [subCustom, setSubCustom] = useState("");
  const [sensitivity, setSensitivity] = useState(3);
  const [minShotMs, setMinShotMs] = useState(600);
  const [frameSkip, setFrameSkip] = useState(0);
  const [maxShots, setMaxShots] = useState(5000);

  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<VideoExtractResult | null>(null);

  // Re-probing on every handle position would be one seek storm per drag.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedTrim({ start: trimStart, end: trimEnd }), PROBE_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [trimStart, trimEnd]);

  const probeQuery = useQuery({
    queryKey: ["video-probe", primary.id, debouncedTrim.start, debouncedTrim.end],
    queryFn: () =>
      videosApi.probe(primary.id, {
        samples: PROBE_SAMPLES,
        max_edge: PROBE_MAX_EDGE,
        trim_start_ms: debouncedTrim.start,
        trim_end_ms: debouncedTrim.end,
      }),
    staleTime: 5 * 60_000,
    // A probe failure is reported once and kept; retrying a 504 on slow storage
    // just spends another 25 s arriving at the same answer.
    retry: false,
  });

  // The last probe that succeeded, kept so a 504 leaves the previous samples on
  // screen instead of blanking the step the user is working in.
  // Adjusted during render rather than in an effect (the pattern React documents
  // for derived state, and the one `VideoDetailPage`'s rename editor uses): an
  // effect would paint one frame with no samples at all before restoring them.
  const [lastProbe, setLastProbe] = useState<VideoProbeResult | null>(null);
  if (probeQuery.data && probeQuery.data !== lastProbe) setLastProbe(probeQuery.data);
  useEffect(() => {
    if (probeQuery.error) toast.error(apiErrorDetail(probeQuery.error, "Could not probe this video"));
  }, [probeQuery.error]);

  // Read in preference to `probe.capabilities`: a video that will not probe
  // still extracts, and the deinterlace gate and shot-detection warning have to
  // keep working when it doesn't. Server-side pure, so one long-lived cache
  // entry covers every modal in the session.
  const { data: serverCaps } = useQuery({
    queryKey: ["extract-capabilities"],
    queryFn: () => videosApi.capabilities(),
    staleTime: 60 * 60_000,
  });

  const probe = probeQuery.data ?? lastProbe;
  const caps = serverCaps ?? probe?.capabilities ?? {};
  const frameW = probe?.width ?? primary.width ?? 0;
  const frameH = probe?.height ?? primary.height ?? 0;
  const durationMs = probe?.duration_ms ?? primary.duration_ms ?? 0;
  const trimUnavailable = probe?.duration_source === "unknown" || !durationMs;

  const samples = probe?.samples ?? [];
  const sample = samples[Math.min(selectedSample, Math.max(samples.length - 1, 0))];

  // The primary video's extraction history: the replace-mode label needs the
  // number *before* anything is deleted.
  const { data: framesSummary } = useQuery({
    queryKey: ["video-frames", primary.id],
    queryFn: () => videosApi.framesSummary(primary.id),
    staleTime: 0,
  });

  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  const startedJobIds = useMemo(
    () => Object.fromEntries((result?.jobs ?? []).map((j) => [j.video_id, j.job_id])),
    [result],
  );
  const videoIds = useMemo(() => videos.map((v) => v.id), [videos]);
  const liveJobs = useVideoExtractJobs(videoIds, startedJobIds);

  async function handleSubmit() {
    setSubmitting(true);
    try {
      const res = await videosApi.extract({
        video_ids: videoIds,
        crop,
        // `crop: null` alone is ambiguous — it also means "leave the stored rect
        // alone". This modal always shows the stored rect, so an empty rect here
        // really is the user clearing it.
        clear_crop: crop === null,
        deinterlace,
        trim_start_ms: trimUnavailable ? undefined : trimStart,
        trim_end_ms: trimUnavailable ? undefined : trimEnd,
        sensitivity,
        min_shot_ms: minShotMs,
        detector_frame_skip: frameSkip,
        max_shots: maxShots,
        frames_per_shot: framesPerShot,
        pick,
        candidates,
        long_edge: longEdge,
        mode,
        // Omitted rather than "" so the router derives the slug from the
        // filename — which is what it does with no subfolder at all.
        subfolder: mode === "replace" ? undefined : resolvedSubfolder() || undefined,
      });
      setResult(res);
      if (res.skipped.length && !res.jobs.length) {
        toast(`Already extracting — nothing new was started`, { icon: "⚠️" });
      }
    } catch (err) {
      toast.error(apiErrorDetail(err, "Could not start extraction"));
    } finally {
      setSubmitting(false);
    }
  }

  function resolvedSubfolder(): string {
    if (subSelect === SUB_AUTO) return "";
    if (subSelect === SUB_CUSTOM) return subCustom.trim();
    return subSelect;
  }

  const { overlayProps, panelProps } = useModalBehavior({ onClose, label: "Extract frames" });

  const previousCount = framesSummary?.total ?? 0;
  const lastSubfolder = framesSummary?.groups[0]?.subfolder;

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
      {...overlayProps}
    >
      <div className="panel" style={{ width: 640, maxWidth: "94vw", maxHeight: "92vh", display: "flex", flexDirection: "column" }} {...panelProps}>
        <div className="panel-h">
          <h3>Extract frames</h3>
          <div style={{ flex: 1 }} />
          <button className="icon-btn" title="Close" onClick={onClose}>×</button>
        </div>

        <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10, overflowY: "auto" }}>
          <div style={{ fontSize: 12, color: "var(--fg-mute)" }}>
            {batch
              ? <>Previewing <b>{primary.filename}</b> — these settings apply to all {videos.length} videos</>
              : <span className="mono">{primary.filename}</span>}
          </div>

          {result ? (
            <RunningView
              result={result}
              liveJobs={liveJobs}
              videos={videos}
            />
          ) : step === 1 ? (
            <>
              {probeQuery.isPending && !probe && (
                <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>Sampling the video…</p>
              )}

              {samples.length > 0 && (
                <div style={{ display: "flex", gap: 6, overflowX: "auto", paddingBottom: 2 }} data-testid="probe-filmstrip">
                  {samples.map((s, i) => (
                    <button
                      key={s.timestamp_ms}
                      onClick={() => setSelectedSample(i)}
                      title={formatDuration(s.timestamp_ms)}
                      style={{
                        flexShrink: 0, padding: 0, lineHeight: 0, cursor: "pointer",
                        border: `2px solid ${i === selectedSample ? "var(--accent)" : "transparent"}`,
                        borderRadius: "var(--r-sm)", background: "none",
                      }}
                    >
                      <img src={s.data_url} alt={`Sample at ${formatDuration(s.timestamp_ms)}`} style={{ height: 46, display: "block", borderRadius: 2 }} />
                    </button>
                  ))}
                </div>
              )}

              {sample && frameW > 0 && frameH > 0 && (
                <CropOverlay
                  src={sample.data_url}
                  frameW={frameW}
                  frameH={frameH}
                  rect={crop}
                  onChange={setCrop}
                />
              )}

              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 12 }}>
                <button
                  className="btn ghost sm"
                  disabled={!probe?.crop}
                  onClick={() => probe?.crop && setCrop(probe.crop)}
                  title={probe?.crop ? "Apply the matte the probe detected" : "No letterbox matte was detected"}
                >
                  Use detected
                </button>
                {probe?.crop && (
                  <span style={{ color: "var(--fg-dim)" }}>
                    {Math.round((probe.crop_confidence ?? 0) * 100)}% of samples agreed
                  </span>
                )}
                <button className="btn ghost sm" onClick={() => setCrop(null)} disabled={!crop}>
                  Clear crop
                </button>
              </div>

              <label
                style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--fg-mute)", cursor: caps.deinterlace === false ? "not-allowed" : "pointer" }}
                title={
                  caps.deinterlace === false
                    ? "Deinterlacing needs the imageio-ffmpeg package, which is not installed. Run the update command (manage.sh update / manage.ps1 update), or extract with deinterlacing switched off."
                    : "bwdif — the only deinterlacer that ships"
                }
              >
                <input
                  type="checkbox"
                  className="checkbox"
                  checked={deinterlace === "bwdif"}
                  disabled={caps.deinterlace === false}
                  onChange={(e) => setDeinterlace(e.target.checked ? "bwdif" : "")}
                />
                Deinterlace (bwdif)
                {caps.deinterlace === false && <span style={{ color: "var(--fg-dim)" }}>— imageio-ffmpeg not installed</span>}
              </label>

              <div>
                <div style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 4 }}>Trim</div>
                <TrimBar
                  durationMs={durationMs}
                  startMs={trimStart}
                  endMs={trimEnd}
                  onChange={(s, e) => { setTrimStart(s); setTrimEnd(e); }}
                  disabled={trimUnavailable}
                  disabledNote="This container will not seek, so trimming is unavailable"
                />
              </div>

              {probe && (
                <div style={{ fontSize: 11.5, color: "var(--fg-dim)", display: "flex", flexDirection: "column", gap: 3 }}>
                  {probe.warnings.map((w) => (
                    <span key={w} style={{ color: "var(--warn)" }}>⚠ {w}</span>
                  ))}
                  {probe.interlace && <span>Interlacing detected — switch the deinterlacer on if the frames comb.</span>}
                  {/* Honest about the limit: telecine is detected, never corrected
                      — only bwdif ships, and it is not an inverse pulldown. */}
                  {probe.telecine && (
                    <span>Telecine (3:2 pulldown) detected. It is <b>not</b> corrected — only bwdif ships, so expect some duplicated frames.</span>
                  )}
                  {probe.samples_failed > 0 && <span>{probe.samples_failed} sample(s) failed to decode.</span>}
                  {probe.truncated && <span>The file appears truncated — the tail may not decode.</span>}
                </div>
              )}
            </>
          ) : (
            <>
              <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap", fontSize: 12, color: "var(--fg-mute)" }}>
                <label style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  Frames per shot
                  <input className="input" type="number" min={1} max={20} style={{ width: 56, fontSize: 12 }}
                    value={framesPerShot}
                    onChange={(e) => setFramesPerShot(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  Pick
                  <select className="select" style={{ fontSize: 12 }} value={pick} onChange={(e) => setPick(e.target.value as "sharpest" | "middle")}>
                    <option value="sharpest">Sharpest</option>
                    <option value="middle">Middle of shot</option>
                  </select>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 5 }} title="How many frames are scored before one is chosen">
                  Candidates
                  <input className="input" type="number" min={1} max={15} style={{ width: 52, fontSize: 12 }}
                    value={candidates}
                    onChange={(e) => setCandidates(Math.max(1, Math.min(15, Number(e.target.value) || 5)))} />
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  Long edge
                  <input className="input" type="number" min={64} max={8192} step={64} style={{ width: 68, fontSize: 12 }}
                    value={longEdge}
                    onChange={(e) => setLongEdge(Math.max(64, Math.min(8192, Number(e.target.value) || 1024)))} />
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 5 }} title="Lower = more cuts detected">
                  Sensitivity
                  <input type="range" min={0.5} max={20} step={0.5} style={{ width: 90 }}
                    value={sensitivity} onChange={(e) => setSensitivity(Number(e.target.value))} />
                  <span className="mono">{sensitivity.toFixed(1)}</span>
                </label>
              </div>

              {caps.shot_detection === false && (
                <p style={{ fontSize: 11.5, color: "var(--warn)", margin: 0 }}>
                  Shot detection is unavailable (the scenedetect package is not installed), so
                  frames will be sampled at fixed intervals instead of at cuts.
                </p>
              )}

              <fieldset style={{ border: 0, margin: 0, padding: 0 }}>
                <legend style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 4 }}>Where the frames go</legend>
                <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                    <input type="radio" name="extract-mode" checked={mode === "new_subfolder"} onChange={() => setMode("new_subfolder")} />
                    New subfolder
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                    <input type="radio" name="extract-mode" checked={mode === "add"} onChange={() => setMode("add")} />
                    {previousCount > 0 && lastSubfolder !== undefined
                      ? <>Add to <span className="mono">{lastSubfolder || "the dataset root"}</span></>
                      : "Add to the existing subfolder"}
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", color: "var(--bad)" }}>
                    <input type="radio" name="extract-mode" checked={mode === "replace"} onChange={() => setMode("replace")} />
                    {batch
                      ? "Replace (deletes each video's previous frames)"
                      : previousCount > 0
                        ? `Replace (deletes ${previousCount} previous frame${previousCount === 1 ? "" : "s"})`
                        : "Replace (nothing to replace yet)"}
                  </label>
                </div>
              </fieldset>

              {mode !== "replace" && (
                <div>
                  <label className="label" style={{ fontSize: 12 }}>Subfolder</label>
                  <select className="select" style={{ fontSize: 12 }} value={subSelect} onChange={(e) => setSubSelect(e.target.value)}>
                    <option value={SUB_AUTO}>Automatic — named after the video</option>
                    {subfolders.filter((sf) => sf.path !== "").map((sf) => (
                      <option key={sf.path} value={sf.path}>
                        {sf.path} ({sf.image_count} image{sf.image_count !== 1 ? "s" : ""})
                      </option>
                    ))}
                    <option value={SUB_CUSTOM}>Name it…</option>
                  </select>
                  {subSelect === SUB_CUSTOM && (
                    <input
                      className="input"
                      style={{ marginTop: 6, fontSize: 12 }}
                      placeholder="Subfolder name"
                      value={subCustom}
                      onChange={(e) => setSubCustom(e.target.value)}
                      autoFocus
                    />
                  )}
                </div>
              )}

              {/* The cost-cliff levers, not first-run controls — a frame skip of 2
                  triples detection speed and quietly changes which cuts are found. */}
              <details>
                <summary style={{ fontSize: 12, color: "var(--fg-mute)", cursor: "pointer" }}>Detector tuning</summary>
                <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap", fontSize: 12, color: "var(--fg-mute)", paddingTop: 6 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 5 }} title="Shots shorter than this are merged into their neighbour">
                    Min shot (ms)
                    <input className="input" type="number" min={0} max={600000} step={100} style={{ width: 74, fontSize: 12 }}
                      value={minShotMs} onChange={(e) => setMinShotMs(Math.max(0, Math.min(600000, Number(e.target.value) || 0)))} />
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 5 }} title="Skip N frames between detector reads — faster, but short cuts are missed">
                    Frame skip
                    <input className="input" type="number" min={0} max={10} style={{ width: 52, fontSize: 12 }}
                      value={frameSkip} onChange={(e) => setFrameSkip(Math.max(0, Math.min(10, Number(e.target.value) || 0)))} />
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 5 }} title="Detection stops once this many shots have been found">
                    Max shots
                    <input className="input" type="number" min={1} max={50000} step={100} style={{ width: 74, fontSize: 12 }}
                      value={maxShots} onChange={(e) => setMaxShots(Math.max(1, Math.min(50000, Number(e.target.value) || 5000)))} />
                  </label>
                </div>
              </details>
            </>
          )}
        </div>

        <div className="panel-b" style={{ display: "flex", justifyContent: "flex-end", gap: 8, borderTop: "1px solid var(--line)" }}>
          {result ? (
            <button className="btn primary" onClick={onClose}>Close</button>
          ) : (
            <>
              <button className="btn ghost" onClick={onClose}>Cancel</button>
              {step === 2 && <button className="btn ghost" onClick={() => setStep(1)}>Back</button>}
              {step === 1 ? (
                <button className="btn primary" onClick={() => setStep(2)} disabled={!probe}>Next</button>
              ) : (
                <button className="btn primary" onClick={handleSubmit} disabled={submitting}>
                  {submitting ? "Starting…" : `Extract from ${videos.length} video${videos.length === 1 ? "" : "s"}`}
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** One row per started job, plus the videos the server refused to double-start. */
function RunningView({
  result, liveJobs, videos,
}: {
  result: VideoExtractResult;
  liveJobs: Map<string, JobProgress>;
  videos: Video[];
}) {
  const nameFor = (videoId: string) =>
    result.jobs.find((j) => j.video_id === videoId)?.filename
    ?? videos.find((v) => v.id === videoId)?.filename
    ?? videoId;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {result.jobs.map((j) => {
        const live = liveJobs.get(j.video_id);
        return (
          <div key={j.job_id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
              <span className="mono" style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {nameFor(j.video_id)}
              </span>
              <span style={{ color: "var(--fg-dim)", fontSize: 11 }}>→ {j.subfolder || "root"}</span>
              {live && (
                <button className="icon-btn" title="Cancel this extraction" onClick={() => jobsApi.cancel(j.job_id)}>×</button>
              )}
            </div>
            <JobProgressBar
              message={live ? extractPhaseLabel(live) : "Finished or no longer reporting"}
              percent={Math.max(0, live?.percent ?? 100)}
            />
          </div>
        );
      })}

      {result.skipped.map((s) => (
        <div key={s.video_id} style={{ fontSize: 12, color: "var(--warn)" }}>
          {s.filename} — {s.reason}
        </div>
      ))}

      <p style={{ fontSize: 11.5, color: "var(--fg-dim)", margin: 0 }}>
        Frames appear in the gallery as they are written. Closing this window is safe —
        the jobs keep running.
      </p>
    </div>
  );
}
