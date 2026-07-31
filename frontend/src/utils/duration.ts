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
