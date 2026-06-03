import { useEffect } from "react";
import { useJobStore } from "../store/jobStore";
import type { JobProgress } from "../types";

export function useJobSSE(jobId: string | null) {
  const updateJob = useJobStore((s) => s.updateJob);

  useEffect(() => {
    if (!jobId) return;
    const id = jobId;

    let es: EventSource | null = null;
    let closed = false;

    function connect() {
      if (closed) return;
      es = new EventSource(`/api/v1/jobs/stream/${id}`);
      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as JobProgress;
          if (data.type !== "heartbeat") {
            updateJob(id, data);
          }
        } catch {}
      };
      // On transient error, close and reconnect after a short delay so progress
      // bars don't stall permanently from a momentary network hiccup.
      es.onerror = () => {
        es?.close();
        if (!closed) setTimeout(connect, 3000);
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
    const es = new EventSource("/api/v1/jobs/stream/all/events");
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as JobProgress;
        if (data.type !== "heartbeat" && data.job_id) {
          updateJob(data.job_id, data);
        }
      } catch {}
    };
    es.onerror = (e) => console.warn("Global job SSE error", e);
    return () => es.close();
  }, [updateJob]);
}
