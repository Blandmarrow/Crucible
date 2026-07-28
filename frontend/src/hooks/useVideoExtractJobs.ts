import { useEffect, useMemo, useRef } from "react";
import { jobsApi } from "../api/jobs";
import { useJobStore } from "../store/jobStore";
import { loadPersisted, savePersisted } from "../utils/persistentState";
import type { JobProgress } from "../types";

const TERMINAL = ["completed", "failed", "cancelled"];

/** Component-local persisted key — computed per video, so it cannot be a static
 *  constant in `constants/storage.ts`. The same sanctioned exception as
 *  `comfy-genprompts-job-${planId}`; see docs/dev/persistence.md. */
export function videoExtractJobKey(videoId: string): string {
  return `video-extract-job-${videoId}`;
}

/**
 * The live `video_extract` job for each of `videoIds`, if any.
 *
 * There is deliberately **no `useJobSSE` here**. `useAllJobsSSE` is mounted once
 * in `TopBar` and never unmounts, so `jobStore` already holds every event for
 * every job; a component-scoped subscription would re-create exactly the
 * component↔job coupling this pattern exists to remove.
 *
 * The job is *derived* from `jobStore` on every render and never mirrored into
 * state: a copy would need clearing on every terminal event and would go stale
 * the moment the modal unmounts — which is precisely what the job is meant to
 * survive. `ExtractFramesModal` and `VideoDetailPage` therefore show the same
 * bar for the same run, with no coordination between them.
 *
 * @param videoIds  the videos to watch; a batch runs one job per video.
 * @param startedJobIds  video id → job id straight from the extract response,
 *   covering the window before the job's first emit: the queue's own
 *   pending/running events carry no `video_id`, only the worker's emits do.
 *
 * A reload is covered by a different route — the recovery effect below re-seeds
 * `jobStore` from the persisted id, and every match after that is by `video_id`.
 */
export function useVideoExtractJobs(
  videoIds: string[],
  startedJobIds: Record<string, string> = {},
): Map<string, JobProgress> {
  const activeJobs = useJobStore((s) => s.activeJobs);
  const idsKey = videoIds.join(",");

  const jobs = useMemo(() => {
    const out = new Map<string, JobProgress>();
    // Primary: any live job that has announced which video it is working on.
    for (const p of activeJobs.values()) {
      if (p.job_type === "video_extract" && p.video_id && !TERMINAL.includes(p.status)) {
        out.set(p.video_id, p);
      }
    }
    // A job we just started is not matchable by `video_id` yet: the queue's own
    // pending/running events don't carry one, only the worker's first emit does.
    for (const id of idsKey ? idsKey.split(",") : []) {
      if (out.has(id)) continue;
      const jobId = startedJobIds[id];
      const started = jobId ? activeJobs.get(jobId) : undefined;
      if (started && !TERMINAL.includes(started.status)) out.set(id, started);
    }
    return out;
  }, [activeJobs, idsKey, startedJobIds]);

  // **Declared before the persist effect on purpose.** Effects run in declaration
  // order, so this reads the stored id before the write below can replace it with
  // null — the hazard GeneratePromptsModal documents and solves with a render-time
  // read. Once per video id: a persisted id with no jobStore entry either finished
  // while we were away (or was TTL-evicted), or is still running and simply hasn't
  // emitted since the reload — seeding the store makes the bar appear now, and the
  // global SSE stream takes over at the next emit.
  const recoveredRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    let dropped = false;
    for (const id of idsKey ? idsKey.split(",") : []) {
      if (recoveredRef.current.has(id)) continue;
      recoveredRef.current.add(id);
      const savedId = loadPersisted(videoExtractJobKey(id), { jobId: null as string | null }).jobId;
      if (!savedId || useJobStore.getState().activeJobs.has(savedId)) continue;
      jobsApi.get(savedId)
        .then((j) => {
          if (dropped) return;
          if (TERMINAL.includes(j.status)) {
            savePersisted(videoExtractJobKey(id), { jobId: null });
            return;
          }
          useJobStore.getState().updateJob(j.id, {
            type: "progress", job_id: j.id, job_type: j.job_type, label: j.label,
            status: j.status, done: j.done_items, total: j.total_items,
            percent: 0, video_id: id, dataset_id: j.dataset_id ?? undefined,
          });
        })
        .catch(() => { /* job row gone — nothing to re-attach to */ });
    }
    return () => { dropped = true; };
  }, [idsKey]);

  // Persist the live job id so a hard reload — which empties jobStore — can still
  // find it. Cleared once the job goes terminal.
  //
  // Written **on transition only**, and never a `null` for an id this instance
  // has not yet seen live. `jobs` is a fresh Map on every `activeJobs` change,
  // i.e. on every SSE event app-wide, so the unguarded form wrote localStorage
  // for every watched video on every event — and nearly all of those writes were
  // `{jobId: null}` for videos with no job at all. That also raced the recovery
  // effect above: `VideoDetailPage` and an open modal both run this hook for the
  // same video, so instance #2's null-write could land inside instance #1's
  // in-flight `jobsApi.get` and erase the id it was re-attaching to.
  const persistedRef = useRef<Map<string, string | null>>(new Map());
  useEffect(() => {
    for (const id of idsKey ? idsKey.split(",") : []) {
      const jobId = jobs.get(id)?.job_id ?? null;
      if (!persistedRef.current.has(id) && jobId === null) continue;
      if (persistedRef.current.get(id) === jobId) continue;
      persistedRef.current.set(id, jobId);
      savePersisted(videoExtractJobKey(id), { jobId });
    }
  }, [idsKey, jobs]);

  return jobs;
}

/** The phase label for a running extraction — the generic done/total counts
 *  frames, which says nothing during the long detection phase. */
export function extractPhaseLabel(job: JobProgress | undefined): string {
  if (!job) return "";
  if (job.message) return job.message;
  switch (job.phase) {
    case "detecting": return "Detecting shots…";
    case "replacing": return "Removing previous frames…";
    case "extracting": return "Extracting frames…";
    default: return "Starting…";
  }
}
