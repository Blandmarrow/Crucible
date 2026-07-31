/** Human-readable clip length: "4:12", "1:02:33", "—" for an unknown duration.
 *
 *  null is *unknown*, never 0:00. A container written to a non-seekable pipe
 *  reports a poisoned frame count and the backend stores NULL rather than a
 *  fabricated number (see docs/dev/video-decode.md § metadata ladder); rendering that
 *  as "0:00" would turn a missing header into a claim about the video. Every
 *  video surface formats through this one helper so they cannot disagree.
 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "—";
  const total = Math.round(ms / 1000);
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** A single frame's position in its source video, to the millisecond: "0:00.760".
 *
 *  Distinct from `formatDuration`, which answers "how long is this clip?" at
 *  second resolution — the right granularity for a length, and the wrong one for
 *  a frame. Frames cut from one held shot sit tens of milliseconds apart, so two
 *  of them both render as "0:01" and a UI showing timestamps *so the user can
 *  tell them apart* tells them nothing. That is not hypothetical: it is what the
 *  duplicate-group panel did on its first run.
 *
 *  Same null contract as its sibling — null is unknown, never 0:00. Use this
 *  wherever a `source_timestamp_ms` is shown; use `formatDuration` for a
 *  `Video.duration_ms`.
 */
export function formatFramePosition(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "—";
  const millis = Math.round(ms) % 1000;
  return `${formatDuration(Math.floor(ms / 1000) * 1000)}.${String(millis).padStart(3, "0")}`;
}
