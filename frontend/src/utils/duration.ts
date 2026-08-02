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

/** How long ago a timestamp was, in words: "just now", "12m ago", "3h ago", "5d ago".
 *
 *  Null/unparseable is "—", the same contract as its two siblings above.
 *
 *  Four hand-rolled copies of this arithmetic exist in the codebase
 *  (`QualityPage`, `SyncCanvasModal`, `SidebarVersionPanel`, `LogsPage`), which is
 *  why a fifth was not written: `QualityPage`'s is converted in the same change
 *  that added this. The other three are a known seam, left alone here because
 *  each renders a slightly different granularity and converting them is a
 *  behaviour change to three screens, not a refactor.
 */
export function formatTimeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  // Every timestamp in this DB is written by `datetime.utcnow()` and serialized
  // without an offset, and `new Date("…T12:00:00")` reads a bare string as *local*
  // time. Left alone, a browser at UTC+3 sees every event three hours in the
  // future and reports "just now" forever. Append the designator when the string
  // carries none of its own.
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const then = new Date(hasZone ? iso : `${iso}Z`).getTime();
  if (!Number.isFinite(then)) return "—";
  // A server clock marginally ahead of the browser's must not read "in 3 seconds".
  const mins = Math.max(0, Math.floor((Date.now() - then) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
