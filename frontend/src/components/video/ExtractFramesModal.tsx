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

  // Which decode fixups the user actually changed this session. Everything in
  // step 1 is seeded from the **primary** video but written by the endpoint to
  // **every** video in the batch, so sending an untouched control clears the
  // other videos' stored settings — opening a batch whose primary happens to
  // carry no crop and pressing Extract wiped every other video's rect. The API
  // is built for this: `None` means "leave the row alone" for all four fields,
  // so an untouched control sends `undefined`. It also makes the single-video
  // case a no-op instead of a rewrite.
  const [cropTouched, setCropTouched] = useState(false);
  const [deinterlaceTouched, setDeinterlaceTouched] = useState(false);
  const [trimTouched, setTrimTouched] = useState(false);

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
    // Each entry holds eight base64 JPEGs — ~350 KB — and the debounced trim
    // drag mints a new key per handle position, so the 5-minute default keeps a
    // drag's worth of them resident long after the modal has closed.
    gcTime: 60_000,
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

  // The filter this run will actually use. Without the coercion a row carrying
  // `"bwdif"` from an earlier run, opened on a host that has since lost
  // imageio-ffmpeg, submits `"bwdif"` behind a disabled checkbox and takes the
  // endpoint's 503 — the one case its own `effective` check cannot cover,
  // because the request *does* send the field.
  const effectiveDeinterlace: "" | "bwdif" = caps.deinterlace === false ? "" : deinterlace;
  const deinterlaceCoerced = effectiveDeinterlace !== deinterlace;

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

  // One row per video with a job to show, whether this modal started it or found
  // it already running. A **union** keyed by video id, not "result if present,
  // otherwise liveJobs": submit A+B+C with A already extracting and the endpoint
  // returns A under `skipped` only, so the otherwise-form would drop A's live bar
  // the instant the response landed and leave an amber line for the video working
  // hardest. `result.jobs` goes first because it alone carries `filename` and the
  // resolved `subfolder`.
  const incomingRows = useMemo<ProgressRow[]>(() => {
    const out: ProgressRow[] = [];
    // First, so `mergeRows` sees the richer row before the derived one.
    for (const j of result?.jobs ?? []) {
      out.push({ videoId: j.video_id, jobId: j.job_id, filename: j.filename, subfolder: j.subfolder });
    }
    for (const [videoId, p] of liveJobs) {
      // `useVideoExtractJobs` returns every live extraction in the app, not only
      // this modal's, so the membership check is load-bearing.
      if (!videoIds.includes(videoId)) continue;
      out.push({
        videoId,
        jobId: p.job_id,
        filename: videos.find((v) => v.id === videoId)?.filename ?? videoId,
      });
    }
    return out;
  }, [result, liveJobs, videoIds, videos]);

  // **A row persists once seen**, which is what lets a run that finishes while the
  // modal is open settle into "Finished or no longer reporting" instead of
  // disappearing. `useVideoExtractJobs` filters terminal statuses, so a row derived
  // from it has no other source and would otherwise vanish at the exact moment the
  // user is watching for an outcome — while a row from `result`, whose array is
  // terminal-stable, settled correctly. This remembers *rows*, not a view mode: the
  // step content stays interactive throughout, so there is nothing to be trapped in.
  // Adjusted during render (`mergeRows` returns the same Map when nothing changed),
  // the same idiom as `lastProbe` above — not a ref, which cannot be written here.
  const [seenRows, setSeenRows] = useState<Map<string, ProgressRow>>(() => new Map());
  const mergedRows = mergeRows(seenRows, incomingRows);
  if (mergedRows !== seenRows) setSeenRows(mergedRows);
  const rows = useMemo(() => [...mergedRows.values()], [mergedRows]);

  // A video that produced a row is being watched, so its amber "already
  // extracting" line would only contradict the bar directly above it.
  const skipped = useMemo(
    () => (result?.skipped ?? []).filter((s) => !rows.some((r) => r.videoId === s.video_id)),
    [result, rows],
  );

  // Derived during render rather than reset in the radio's `onChange` — the
  // idiom this file already uses for `lastProbe`, and `VideoStrip` for
  // `selectionFor`. In `new_subfolder` mode the router steps the chosen name
  // through `_step_subfolder`, so picking an existing subfolder there silently
  // becomes `{name}_2`; the option must not be offered at all. A reset in the
  // handler cannot be relied on either way: a `<select>` whose value matches no
  // option renders *blank* while the state still holds the old path, which is
  // worse than the bug. Deriving also preserves the add-mode choice across a
  // round trip through the other modes, and mirrors `effectiveDeinterlace`.
  const effectiveSubSelect =
    mode === "new_subfolder" && subSelect !== SUB_AUTO && subSelect !== SUB_CUSTOM
      ? SUB_AUTO
      : subSelect;

  async function handleSubmit() {
    setSubmitting(true);
    try {
      const res = await videosApi.extract({
        video_ids: videoIds,
        // Untouched decode fixups are omitted, not sent — see `cropTouched`.
        crop: cropTouched ? crop : undefined,
        // `crop: null` alone is ambiguous — it also means "leave the stored rect
        // alone". This modal always shows the stored rect, so an empty rect here
        // really is the user clearing it.
        clear_crop: cropTouched ? crop === null : undefined,
        // Sent when coerced even if untouched: that is the only way off a stale
        // `"bwdif"` this host can no longer run.
        deinterlace: deinterlaceTouched || deinterlaceCoerced ? effectiveDeinterlace : undefined,
        trim_start_ms: trimUnavailable || !trimTouched ? undefined : trimStart,
        trim_end_ms: trimUnavailable || !trimTouched ? undefined : trimEnd,
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
    if (effectiveSubSelect === SUB_AUTO) return "";
    if (effectiveSubSelect === SUB_CUSTOM) return subCustom.trim();
    return effectiveSubSelect;
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
              ? <>Previewing <b>{primary.filename}</b> — these settings apply to all {videos.length} videos.
                The crop, deinterlacer and trim shown are this video's; each is written to the whole
                batch only if you change it here, and otherwise every video keeps its own.</>
              : <span className="mono">{primary.filename}</span>}
          </div>

          {/* Deliberately **not** a view swap on `liveJobs.size > 0`:
              `useVideoExtractJobs` filters terminal statuses, so the view would
              empty the instant a run finished and dump the user back into a fresh
              probe. The full swap stays gated on `result` alone — terminal-stable
              — and a run this modal did not start is shown as a block *above* the
              step content instead, following `GeneratePromptsModal`. Reopening
              over a live job then lands on step 1 with a watchable, cancellable
              bar on top, and a mixed batch can still be configured for the videos
              that are not busy. No view state, no latch, no vanish-on-complete. */}
          {!result && rows.length > 0 && <ExtractProgressList rows={rows} liveJobs={liveJobs} skipped={[]} />}

          {result ? (
            <ExtractProgressList rows={rows} liveJobs={liveJobs} skipped={skipped} />
          ) : step === 1 ? (
            <>
              {probeQuery.isPending && !probe && (
                <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>Sampling the video…</p>
              )}

              {/* Naming exactly what is lost, because extraction itself needs no
                  probe — only this step's previews do. Capabilities come from
                  their own route, so the warnings below survive this. */}
              {!probe && !probeQuery.isPending && (
                <p style={{ fontSize: 11.5, color: "var(--warn)", margin: 0 }}>
                  This video could not be sampled, so there is no crop preview, no detected
                  matte and no interlace or telecine warning. Extraction does not need any of
                  them — it will run with whatever crop, deinterlacer and trims are already
                  stored on the video.
                </p>
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
                  onChange={(r) => { setCrop(r); setCropTouched(true); }}
                />
              )}

              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 12 }}>
                <button
                  className="btn ghost sm"
                  disabled={!probe?.crop}
                  onClick={() => { if (probe?.crop) { setCrop(probe.crop); setCropTouched(true); } }}
                  title={probe?.crop ? "Apply the matte the probe detected" : "No letterbox matte was detected"}
                >
                  Use detected
                </button>
                {probe?.crop && (
                  <span style={{ color: "var(--fg-dim)" }}>
                    {Math.round((probe.crop_confidence ?? 0) * 100)}% of samples agreed
                  </span>
                )}
                <button className="btn ghost sm" onClick={() => { setCrop(null); setCropTouched(true); }} disabled={!crop}>
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
                  checked={effectiveDeinterlace === "bwdif"}
                  disabled={caps.deinterlace === false}
                  onChange={(e) => { setDeinterlace(e.target.checked ? "bwdif" : ""); setDeinterlaceTouched(true); }}
                />
                Deinterlace (bwdif)
                {caps.deinterlace === false && <span style={{ color: "var(--fg-dim)" }}>— imageio-ffmpeg not installed</span>}
              </label>

              {deinterlaceCoerced && (
                <p style={{ fontSize: 11.5, color: "var(--warn)", margin: 0 }}>
                  This video has <span className="mono">bwdif</span> saved from an earlier run,
                  but imageio-ffmpeg is not installed here, so extracting would fail. Running it
                  now extracts without deinterlacing <b>and clears the saved setting</b> — you
                  will have to switch it back on once the package is installed.
                </p>
              )}

              <div>
                <div style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 4 }}>Trim</div>
                <TrimBar
                  durationMs={durationMs}
                  startMs={trimStart}
                  endMs={trimEnd}
                  onChange={(s, e) => { setTrimStart(s); setTrimEnd(e); setTrimTouched(true); }}
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
                    {/* `framesSummary` is the *primary* video's history, but the
                        router resolves "previous" per video — so naming one
                        subfolder would be a lie for the rest of a batch. */}
                    {batch
                      ? "Add to each video's own previous subfolder"
                      : previousCount > 0 && lastSubfolder !== undefined
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
                  <select className="select" style={{ fontSize: 12 }} data-testid="extract-subfolder" value={effectiveSubSelect} onChange={(e) => setSubSelect(e.target.value)}>
                    {/* The two modes resolve an empty subfolder differently, so
                        one label for both was wrong in whichever mode it did not
                        describe. */}
                    <option value={SUB_AUTO}>
                      {mode === "add"
                        ? "Automatic — this video's previous subfolder"
                        : "Automatic — a new subfolder named after the video"}
                    </option>
                    {/* Existing subfolders are offered in `add` mode only: in
                        `new_subfolder` the router steps the name it is given, so
                        picking one here would silently produce `{name}_2`. */}
                    {mode === "add" && subfolders.filter((sf) => sf.path !== "").map((sf) => (
                      <option key={sf.path} value={sf.path}>
                        {sf.path} ({sf.image_count} image{sf.image_count !== 1 ? "s" : ""})
                      </option>
                    ))}
                    <option value={SUB_CUSTOM}>Name it…</option>
                  </select>
                  {effectiveSubSelect === SUB_CUSTOM && (
                    <input
                      className="input"
                      style={{ marginTop: 6, fontSize: 12 }}
                      placeholder="Subfolder name"
                      value={subCustom}
                      onChange={(e) => setSubCustom(e.target.value)}
                      autoFocus
                    />
                  )}
                  <p style={{ fontSize: 11.5, color: "var(--fg-dim)", margin: "6px 0 0" }}>
                    {mode === "new_subfolder"
                      ? <>A name that is already taken is stepped — <span className="mono">foo</span>,{" "}
                        <span className="mono">foo_2</span>, <span className="mono">foo_3</span> across a
                        batch. To put a whole batch in <i>one</i> folder, choose “Add to…” and name it there.</>
                      : <>Only subfolders that already hold images are listed. The dataset root is
                        reachable here through “Automatic”, for a video whose last extraction went
                        there.</>}
                  </p>
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
                // `POST /videos/extract` needs no probe at all, so gating Next on one
                // made an unprobeable video permanently un-extractable. Mirrors the
                // "Sampling the video…" gate above; `retry: false` settles `isPending`
                // on the first error, so a failure never leaves this stuck.
                <button className="btn primary" onClick={() => setStep(2)} disabled={probeQuery.isPending && !probe}>Next</button>
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

/** One extraction being watched, whether this modal started it or found it live. */
interface ProgressRow {
  videoId: string;
  jobId: string;
  filename: string;
  /** Only a row built from the extract response knows where the frames land. A
   *  row derived from a live job does not, and must render nothing rather than
   *  fall back to "root" — which would be a guess printed as a fact. */
  subfolder?: string;
}

/** Fold freshly-observed rows into the remembered set.
 *
 *  Returns the **same** Map when nothing changed, so the render-time adjust in the
 *  component is a no-op on the overwhelming majority of renders (this runs on every
 *  SSE event app-wide). A row already carrying a `subfolder` came from the extract
 *  response and knows strictly more than a live payload does, so it is never
 *  overwritten by one.
 */
function mergeRows(prev: Map<string, ProgressRow>, incoming: ProgressRow[]) {
  let next: Map<string, ProgressRow> | null = null;
  for (const r of incoming) {
    const old = prev.get(r.videoId);
    if (old && (old.subfolder !== undefined || r.subfolder === undefined)) continue;
    next ??= new Map(prev);
    next.set(r.videoId, r);
  }
  return next ?? prev;
}

/** The live extractions, plus any video the server refused to double-start.
 *
 *  Rendered in two places for the same run: on its own once this modal has
 *  submitted, and above the step content when it mounted over a job that was
 *  already going. Keyed by video id, because the same video reached by both
 *  routes is one row.
 */
function ExtractProgressList({
  rows, liveJobs, skipped,
}: {
  rows: ProgressRow[];
  liveJobs: Map<string, JobProgress>;
  skipped: VideoExtractResult["skipped"];
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }} data-testid="extract-running">
      {rows.map((r) => {
        const live = liveJobs.get(r.videoId);
        return (
          <div key={r.videoId} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
              <span className="mono" style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {r.filename}
              </span>
              {r.subfolder !== undefined && (
                <span style={{ color: "var(--fg-dim)", fontSize: 11 }}>→ {r.subfolder || "root"}</span>
              )}
              {live && (
                // No optimistic `jobStore` write here, unlike TopBar's pill: this
                // block *is* `liveJobs`, so marking the job cancelled at click
                // time would empty the map and yank the row away before the
                // backend had actually cancelled anything.
                <button className="icon-btn" title="Cancel this extraction" onClick={() => jobsApi.cancel(r.jobId)}>×</button>
              )}
            </div>
            <JobProgressBar
              message={live ? extractPhaseLabel(live) : "Finished or no longer reporting"}
              // `?? 0` for a live job, not `?? 100`: the queue's `pending` event
              // carries no percent, and the queue is serial, so a 3-video batch
              // otherwise showed two *full* bars labelled "Queued". The clamp
              // stays — `JobProgressBar` interpolates `width: ${percent}%` raw,
              // and an invalid `width: -1%` is dropped, leaving `width: auto`,
              // i.e. a full bar again.
              percent={live ? Math.max(0, live.percent ?? 0) : 100}
            />
          </div>
        );
      })}

      {skipped.map((s) => (
        <div key={s.video_id} style={{ fontSize: 12, color: "var(--warn)" }}>
          {/* A skip for a video that no longer resolves has no filename to
              print — the row it would have come from is gone. */}
          {s.filename || s.video_id} — {s.reason}
        </div>
      ))}

      {/* No promise that another run can always be started: `POST
          /videos/extract` calls `ensure_not_busy`, so a second submit landing
          during another video's replace step 409s. */}
      <p style={{ fontSize: 11.5, color: "var(--fg-dim)", margin: 0 }}>
        Frames appear in the gallery as they are written. Closing this window is safe —
        the jobs keep running.
      </p>
    </div>
  );
}
