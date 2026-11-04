/**
 * Hand the viewer a file the page made: a Blob behind a temporary link, clicked and
 * removed. Shared by the Stats page's CSV exports and the Runs page's recipe export.
 */
export function triggerDownload(content: string, filename: string, mime = "text/plain") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/** A name with the characters Windows and POSIX refuse in filenames replaced by `_`. */
export function safeFilename(name: string): string {
  return name.replace(/[/\\:*?"<>|]/g, "_").trim();
}
