/**
 * The three statuses a `BackgroundJob` never leaves once it reaches them.
 *
 * Shared rather than re-declared because three separate places need the same
 * answer to "is this job still worth watching?": `TopBar`'s invalidation pass,
 * `useVideoExtractJobs`' derivation of the live job per video, and
 * `ReextractFramesForm`'s adoption of live jobs on reopen. A local copy in each
 * is how one of them ends up learning about a fourth status and the others do
 * not.
 */
export const TERMINAL_JOB_STATUSES: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "cancelled",
]);
