/** Why a `<video>` element failed, in words that do not blame the file.
 *
 *  Firefox renders a decode failure as "the file is corrupt", which is its own
 *  string and not a fact: a High 10 (10-bit) H.264, an HEVC stream or a
 *  Matroska container is a perfectly good file the browser simply has no
 *  decoder for. Crucible knows the codec — it stored one at ingest — so it can
 *  say the true thing instead, and say the part the user actually cares about:
 *  probing, posters, shot detection and frame extraction all read the file
 *  through cv2/ffmpeg and are unaffected.
 *
 *  Every video surface classifies through here so the two cannot disagree.
 */

/** Containers no mainstream browser decodes reliably, whatever is inside them. */
const UNPLAYABLE_CONTAINERS = new Set([".mkv", ".avi"]);

/** Codecs a browser is unlikely to have, keyed on both the raw FOURCC and the
 *  display label `backend/media_types.py::codec_label` produces — `Video.codec_label`
 *  carries the label, and the file browser has neither. AV1 is deliberately
 *  absent: current Firefox and Chrome both decode it in .mp4/.webm, and an .mkv
 *  carrying it is already caught by the container rule. */
const UNPLAYABLE_CODECS = [
  "hevc", "hev1", "hvc1", "h265",
  "prores", "apch", "apcn", "apcs", "ap4h",
  "mpeg-4", "mp4v", "fmp4",
  "motion jpeg", "mjpg",
  "windows media", "wmv3",
  "theora", "theo",
];

function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? "" : filename.slice(dot).toLowerCase();
}

function codecMatches(codecLabel: string | null | undefined): boolean {
  const c = (codecLabel ?? "").trim().toLowerCase();
  if (!c) return false;
  return UNPLAYABLE_CODECS.some((bad) => c.includes(bad));
}

/** A pre-emptive note for a video whose codec/container the browser probably
 *  cannot decode, or null when there is nothing to warn about.
 *
 *  Shown *before* playback is attempted (the Video Info panel), which is why it
 *  hedges — it is a prediction from stored metadata, not an observed failure.
 *  `playbackErrorMessage` is the observed half.
 */
export function browserPlaybackHint(
  codecLabel: string | null | undefined,
  filename: string,
): string | null {
  const ext = extensionOf(filename);
  const badContainer = UNPLAYABLE_CONTAINERS.has(ext);
  const badCodec = codecMatches(codecLabel);
  if (!badContainer && !badCodec) return null;
  const what = badCodec && badContainer
    ? `${codecLabel} in ${ext}`
    : badCodec
      ? `${codecLabel}`
      : `${ext} files`;
  return `${what} may not play in this browser. Extraction and probing are unaffected.`;
}

/** The message for a `<video>` that has already failed, or null for no overlay.
 *
 *  `el.error` is a MediaError; the four codes are the whole vocabulary.
 *  MEDIA_ERR_ABORTED is **null on purpose** — unmounting the player to release
 *  the file handle before a delete or rename (see docs/dev/video-ui.md) produces
 *  exactly that, and an overlay there would accuse the app of a failure it
 *  performed itself.
 */
export function playbackErrorMessage(
  el: HTMLVideoElement,
  opts: { filename: string; codecLabel?: string | null },
): string | null {
  const code = el.error?.code;
  if (code == null) return null;
  // 1 — MEDIA_ERR_ABORTED. Our own teardown. Never an overlay.
  if (code === 1) return null;
  // 2 — MEDIA_ERR_NETWORK. The transfer died, which says nothing about the codec.
  if (code === 2) {
    return "The video stopped loading — the connection to the server was interrupted. Try again.";
  }
  // 3 / 4 — MEDIA_ERR_DECODE and MEDIA_ERR_SRC_NOT_SUPPORTED. Firefox calls both
  // of these "corrupt"; neither is a claim about the bytes on disk.
  //
  // But nor is code 4 a claim about the *codec*. Browsers report a 404, a 403 or
  // a 500 on the source as MEDIA_ERR_SRC_NOT_SUPPORTED, not as
  // MEDIA_ERR_NETWORK — code 2 only fires once loading has begun — so a file
  // that has been renamed or deleted out from under an open player lands here.
  // The strong wording is therefore earned only when the stored metadata
  // predicted this failure; otherwise the message names both possibilities and
  // makes no promise about the file.
  const ext = extensionOf(opts.filename);
  const codec = (opts.codecLabel ?? "").trim();
  const predicted = codecMatches(opts.codecLabel) || UNPLAYABLE_CONTAINERS.has(ext);
  if (predicted) {
    const detail = codec && ext
      ? `${codec} in ${ext} is not a format this browser can decode.`
      : ext
        ? `${ext} is not a format this browser can decode.`
        : "This browser has no decoder for this format.";
    return `Your browser can't play this video. ${detail} The file itself is fine — probing, posters, shot detection and frame extraction all still work.`;
  }
  const what = ext ? `this ${ext} file` : "this video";
  return `Your browser couldn't play ${what} — either it has no decoder for the format, or the file couldn't be loaded (it may have been renamed, moved or deleted). Probing, posters, shot detection and frame extraction read the file directly and are unaffected.`;
}
