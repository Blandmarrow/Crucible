// Mirrors the VIDEO_EXTENSIONS frozenset in backend/media_types.py.
//
// This exists because a MIME check is not sufficient on the client. Browsers
// report `File.type` as `""` for `.mkv` and often for `.avi`, so filtering a
// drop with `type.startsWith("video/")` silently discards exactly the files a
// user is most likely to be working with. The backend remains the authority on
// what is actually accepted — it answers every upload with a `skipped` list —
// so this only has to be permissive enough not to drop a file before the
// request is made.

export const VIDEO_EXTENSIONS = [".mp4", ".mkv", ".webm", ".mov", ".avi"] as const;

/** `accept` value for a file input that takes images or videos. The explicit
 *  extensions cover the containers whose MIME type browsers get wrong. */
export const MEDIA_ACCEPT = `image/*,video/*,${VIDEO_EXTENSIONS.join(",")}`;

export function hasVideoExtension(name: string): boolean {
  const lower = name.toLowerCase();
  return VIDEO_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/** True for anything worth POSTing to the upload endpoint. */
export function isMediaFile(file: File): boolean {
  return (
    file.type.startsWith("image/") ||
    file.type.startsWith("video/") ||
    hasVideoExtension(file.name)
  );
}

/** Drag-over variant, for deciding whether to light the upload overlay.
 *
 *  A DataTransferItem exposes `type` but never a filename, so the extension
 *  fallback above is unavailable and a `.mkv` reporting `""` will not light the
 *  overlay. That is cosmetic only: the grid's `onDragOver` preventDefaults
 *  unconditionally, so the drop still fires and `isMediaFile` — which does see
 *  filenames — accepts the file. Being permissive here instead would light the
 *  overlay for a `.txt` caption drag, which reports `""` just as often. */
export function isMediaDragItem(item: DataTransferItem): boolean {
  return item.kind === "file" && (item.type.startsWith("image/") || item.type.startsWith("video/"));
}
