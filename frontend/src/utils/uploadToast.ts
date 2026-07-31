import toast from "react-hot-toast";

import type { UploadResult } from "../api/images";

/** What one upload gesture did, however many requests it took.
 *
 *  `errors` counts thrown requests; `skipped` counts files the server answered
 *  201 for but declined to store. The two are separate on purpose — a network
 *  failure and an unsupported file type need different wording. */
export interface UploadTally {
  images: number;
  videos: number;
  errors: number;
  skipped: { file: string; reason: string }[];
}

/**
 * Fold one or more upload responses into a single tally.
 *
 * A file the server declines comes back inside a 201 with a `skipped` entry, not
 * as an exception, so counting thrown errors alone reports a rejected upload as a
 * successful one. Every caller must go through here rather than assuming the
 * files it sent are the files that landed.
 */
export function tallyUpload(results: UploadResult[], errors = 0): UploadTally {
  return {
    images: results.reduce((n, r) => n + r.added, 0),
    videos: results.reduce((n, r) => n + r.videos_added, 0),
    errors,
    skipped: results.flatMap((r) => r.skipped),
  };
}

/**
 * Report an upload: what landed, what threw, and what was declined and why.
 *
 * Shared by the gallery grid and the dataset-card drop so the two cannot drift —
 * they already had, with the card reporting every dropped file as an uploaded
 * image regardless of what the server took.
 */
export function showUploadSummaryToast({ images, videos, errors, skipped }: UploadTally): void {
  const parts: string[] = [];
  if (images > 0) parts.push(`${images} image(s)`);
  if (videos > 0) parts.push(`${videos} video(s)`);
  if (parts.length) toast.success(`Uploaded ${parts.join(" and ")}`);
  if (errors > 0) toast.error(`${errors} file(s) failed to upload`);
  if (skipped.length) {
    // Name the reason: "skipped 1 file" leaves the user guessing whether the
    // file was the wrong type or simply unreadable.
    const head = skipped.slice(0, 3).map((s) => `${s.file} — ${s.reason}`).join("; ");
    const rest = skipped.length > 3 ? ` (+${skipped.length - 3} more)` : "";
    toast.error(`Skipped ${skipped.length} file(s): ${head}${rest}`);
  }
}
