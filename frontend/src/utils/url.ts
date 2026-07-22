/**
 * A provenance URL if it is safe to put behind an `href`, else "".
 *
 * Mirrors `backend/utils.py::safe_external_url`. Source URLs come from scrapers,
 * sidecars and EXIF tags, so only `http`/`https` are allowed through — React 19
 * already blocks a `javascript:` href, but the value should not reach an href
 * unvalidated, and this is also what decides whether to render a link at all.
 * A rejected value is shown as inert text by the caller.
 */
export function safeExternalUrl(value: string | null | undefined): string {
  const s = (value ?? "").trim();
  // Whitespace or control characters: a URL with a space in the middle is not a
  // URL, and an embedded newline is how a value breaks out of its context.
  if (!s || [...s].some((c) => c.charCodeAt(0) <= 0x20 || c.charCodeAt(0) === 0x7f)) return "";
  const scheme = s.includes(":") ? s.slice(0, s.indexOf(":")).toLowerCase() : "";
  return scheme === "http" || scheme === "https" ? s : "";
}
