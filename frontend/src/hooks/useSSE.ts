import { useEffect } from "react";
import { jobsApi } from "../api/jobs";
import { useJobStore } from "../store/jobStore";
import { TERMINAL_JOB_STATUSES } from "../constants/jobs";
import { applyDurable, hasWatchedJobs, reconcileJobs } from "../utils/jobReconcile";
import type { JobProgress } from "../types";

/** How often `useAllJobsSSE` re-reads the durable rows while any job is watched. */
const RECONCILE_POLL_MS = 20_000;
/** Delay before re-creating an EventSource the browser has given up on. */
const RECONNECT_MS = 3_000;

export function useJobSSE(jobId: string | null) {
  const updateJob = useJobStore((s) => s.updateJob);

  useEffect(() => {
    if (!jobId) return;
    const id = jobId;

    let es: EventSource | null = null;
    let closed = false;
    // Set once the job is known to be over: the per-job stream has nothing more
    // to say, and reconnecting to it would only wait on a channel nothing emits to.
    let finished = false;

    function finish() {
      finished = true;
      es?.close();
    }

    function connect() {
      if (closed || finished) return;
      es = new EventSource(`/api/v1/jobs/stream/${id}`);
      // The stream is live-only, so whatever happened while it was down — the job
      // may even have finished — comes from the durable row (GPT-014).
      es.onopen = () => {
        jobsApi.get(id)
          .then((row) => {
            applyDurable(row, { seed: true });
            if (TERMINAL_JOB_STATUSES.has(row.status)) finish();
          })
          .catch(() => { /* the stream itself, or the global reconcile, catches up */ });
      };
      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as JobProgress;
          if (data.type !== "heartbeat") {
            // optimistic: false, not spread-through: jobStore merges partials, so a
            // cancel button's optimistic flag would otherwise survive onto server
            // events and TopBar would never treat the job's terminal status as real.
            updateJob(id, { ...data, optimistic: false });
            if (TERMINAL_JOB_STATUSES.has(data.status)) finish();
          }
        } catch { /* a malformed event: skip it */ }
      };
      // On transient error, close and reconnect after a short delay so progress
      // bars don't stall permanently from a momentary network hiccup.
      es.onerror = () => {
        es?.close();
        if (!closed && !finished) setTimeout(connect, RECONNECT_MS);
      };
    }

    connect();
    return () => {
      closed = true;
      es?.close();
    };
  }, [jobId, updateJob]);
}

export function useAllJobsSSE() {
  const updateJob = useJobStore((s) => s.updateJob);

  useEffect(() => {
    let es: EventSource | null = null;
    let disposed = false;
    let retry: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (disposed) return;
      es = new EventSource("/api/v1/jobs/stream/all/events");
      // Every (re)open may follow a gap, and the stream never replays one.
      es.onopen = () => { void reconcileJobs(); };
      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as JobProgress;
          if (data.type !== "heartbeat" && data.job_id) {
            // See useJobSSE: server events must clear any optimistic cancel flag.
            updateJob(data.job_id, { ...data, optimistic: false });
          }
        } catch { /* a malformed event: skip it */ }
      };
      es.onerror = (e) => {
        console.warn("Global job SSE error", e);
        // A network drop leaves the EventSource CONNECTING, and the browser
        // retries by itself. An HTTP error response (Vite's 502 while the backend
        // restarts) closes it for good, so re-create it ourselves.
        if (es?.readyState === EventSource.CLOSED && !disposed && retry === null) {
          retry = setTimeout(() => {
            retry = null;
            connect();
          }, RECONNECT_MS);
        }
      };
    }

    connect();
    void reconcileJobs();
    const poll = setInterval(() => {
      if (hasWatchedJobs()) void reconcileJobs();
    }, RECONCILE_POLL_MS);
    const onVisibility = () => {
      if (document.visibilityState === "visible") void reconcileJobs();
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      disposed = true;
      es?.close();
      if (retry !== null) clearTimeout(retry);
      clearInterval(poll);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [updateJob]);
}
