/**
 * Reconcile `jobStore` from the durable `background_jobs` rows (GPT-014).
 *
 * SSE is live-only — no replay, no snapshot — so a job that finishes while the
 * stream is down (a network gap, a server restart, a backgrounded tab) never
 * delivers its terminal event, and without this the TopBar pill stays stuck and no
 * completion effect ever runs. `useAllJobsSSE` calls `reconcileJobs()` on mount, on
 * every (re)open, on a poll while anything is watched, and when the tab becomes
 * visible again; `useJobSSE` and the import entry points use `applyDurable` /
 * `adoptJob` for the one job they care about.
 *
 * **Which jobs it may touch is the whole safety argument.** Only ids the store
 * already watches, plus rows that are pending/running *right now*, are ever
 * written — so a job that finished before this page load is never synthesized,
 * and never toasts. And every synthesized event goes through `updateJob`, the same
 * path SSE uses, so `TopBar`'s `processedJobsRef` dedups a terminal status that
 * arrives twice (once from here, once from a late SSE event).
 */
import { jobsApi } from "../api/jobs";
import { useJobStore } from "../store/jobStore";
import { TERMINAL_JOB_STATUSES } from "../constants/jobs";
import type { Job, JobProgress } from "../types";

// `ids` per request: far below the server's 500, so the query string stays a few
// KB — a request line of 500 uuids would overrun uvicorn's limit.
const IDS_PER_REQUEST = 100;

function definedString(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

function definedNumber(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

/**
 * A `background_jobs` row as the `JobProgress` an SSE event would have carried.
 *
 * Optional keys are spread **only when the row defines them**: `updateJob` merges,
 * so an explicit `undefined` would erase a value a live event already delivered.
 * `done` follows the worker's own terminal event — `total_items` on completion —
 * because no worker writes `done_items`; `comfy_prompts` reports its created count
 * in `result_data.created`, which its toasts read as `done`.
 */
export function jobRowToProgress(row: Job): JobProgress {
  const config = row.config ?? {};
  const result = row.result_data ?? {};
  const created = row.job_type === "comfy_prompts" ? definedNumber(result.created) : undefined;
  const done = created ?? (row.status === "completed" ? row.total_items : row.done_items);
  const total = row.total_items;
  const percent = row.status === "completed" ? 100 : total > 0 ? (done / total) * 100 : 0;

  const videoId = definedString(config.video_id);
  const planId = definedString(config.plan_id);
  const requested = row.job_type === "comfy_prompts" ? definedNumber(result.requested) : undefined;
  const message =
    row.status === "failed" ? definedString(row.error_msg)
    : row.status === "completed" ? "Done."
    : undefined;

  return {
    type: "progress",
    job_id: row.id,
    job_type: row.job_type,
    label: row.label,
    status: row.status,
    done,
    total,
    percent,
    ...(row.dataset_id ? { dataset_id: row.dataset_id } : {}),
    ...(message !== undefined ? { message } : {}),
    ...(videoId !== undefined ? { video_id: videoId } : {}),
    ...(planId !== undefined ? { plan_id: planId } : {}),
    ...(requested !== undefined ? { requested } : {}),
  };
}

/**
 * Merge one durable row into `jobStore`.
 *
 * - A store entry that is terminal and **not** optimistic is absorbing: the server
 *   already said how it ended, and nothing here may revive or re-fire it.
 * - A non-terminal row only ever *seeds* an id the store lacks, and only when
 *   `seed` is set — SSE is fresher than a row for a job it is already streaming.
 * - A terminal row always applies (`optimistic: false`, like every server event),
 *   which is what runs `TopBar`'s terminal effect for a job whose event was lost.
 */
export function applyDurable(row: Job, { seed }: { seed: boolean }): void {
  const store = useJobStore.getState();
  const existing = store.activeJobs.get(row.id);
  if (existing && TERMINAL_JOB_STATUSES.has(existing.status) && !existing.optimistic) return;

  const progress: Partial<JobProgress> = { ...jobRowToProgress(row), optimistic: false };
  if (!TERMINAL_JOB_STATUSES.has(row.status)) {
    if (existing || !seed) return;
    store.updateJob(row.id, progress);
    return;
  }
  // A failed/cancelled row's done_items is never written, so unless the row carries
  // a real count (comfy_prompts' `created`), the live count the store already holds
  // is the better number — keep it.
  const hasCreated = row.job_type === "comfy_prompts" && definedNumber(row.result_data?.created) !== undefined;
  if (existing && row.status !== "completed" && !hasCreated) {
    delete progress.done;
    delete progress.total;
    delete progress.percent;
  }
  store.updateJob(row.id, progress);
}

/** Ids worth asking the server about: anything not yet known to be finished,
 *  including an optimistic cancel the server has not confirmed. */
function watchedIds(): string[] {
  const ids: string[] = [];
  useJobStore.getState().activeJobs.forEach((p, id) => {
    if (!TERMINAL_JOB_STATUSES.has(p.status) || p.optimistic) ids.push(id);
  });
  return ids;
}

export function hasWatchedJobs(): boolean {
  return watchedIds().length > 0;
}

async function fetchWatched(ids: string[]): Promise<Job[]> {
  const rows: Job[] = [];
  for (let i = 0; i < ids.length; i += IDS_PER_REQUEST) {
    const chunk = ids.slice(i, i + IDS_PER_REQUEST);
    try {
      rows.push(...(await jobsApi.list(chunk.length, { ids: chunk })));
    } catch {
      // Swallowed: the next reconcile (poll, reopen, focus) retries.
    }
  }
  return rows;
}

async function reconcileOnce(): Promise<void> {
  const ids = watchedIds();
  const [watched, active] = await Promise.all([
    ids.length ? fetchWatched(ids) : Promise.resolve([] as Job[]),
    jobsApi.list(500, { status: ["pending", "running"] }).catch(() => [] as Job[]),
  ]);
  // Ids the server does not know (a row deleted out from under us) are simply
  // absent from `watched`, and stay as they are.
  for (const row of watched) applyDurable(row, { seed: false });
  for (const row of active) applyDurable(row, { seed: true });
}

let inFlight: Promise<void> | null = null;
let queued = false;

/** Coalesced: at most one reconcile in flight and one queued behind it, however
 *  many triggers (open, poll, focus) fire at once. Never rejects. */
export function reconcileJobs(): Promise<void> {
  if (inFlight) {
    queued = true;
    return inFlight;
  }
  inFlight = (async () => {
    try {
      do {
        queued = false;
        await reconcileOnce();
      } while (queued);
    } catch {
      // reconcileOnce swallows its own request errors; this is belt and braces.
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}

/**
 * Start watching a job the user just submitted, whatever the SSE stream saw.
 *
 * Seeds it if the store has no entry yet, and applies its terminal status if it
 * already finished — the case where a fast failure (an import of a folder that
 * does not exist) lands in an SSE gap. Never rejects.
 */
export function adoptJob(jobId: string): Promise<void> {
  return jobsApi
    .get(jobId)
    .then((row) => applyDurable(row, { seed: true }))
    .catch(() => {});
}
