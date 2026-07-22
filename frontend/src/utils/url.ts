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
  // Trimmed with the *same* character class the guard below rejects, deliberately
  // not `String.trim()`. `trim()` strips exactly ECMA `\s` and Python's `strip()`
  // strips exactly `isspace()`, and the two differ on U+FEFF and U+0085 — the very
  // characters this guard names. A U+FEFF at the *end* of a URL was therefore
  // trimmed here and rejected by the export, and U+0085 there did the reverse.
  // eslint-disable-next-line no-control-regex
  const s = (value ?? "").replace(/^[\s\u0000-\u001f\u007f\u0085]+/, "")
    // eslint-disable-next-line no-control-regex
    .replace(/[\s\u0000-\u001f\u007f\u0085]+$/, "");
  // Whitespace or control characters: a URL with a space in the middle is not a
  // URL, and an embedded newline is how a value breaks out of its context.
  // `\s` for the Unicode whitespace the backend's `isspace()` also rejects
  // (U+00A0 &c.), plus the C0 range and DEL, which `\s` does not cover. U+0085
  // is spelled out because `isspace()` rejects it and `\s` does not; the Python
  // side names U+FEFF for the mirror-image reason. The two sets agree over the
  // whole Unicode range — a URL must not link in the UI and go inert in CREDITS.md.
  // eslint-disable-next-line no-control-regex
  if (!s || /[\s\u0000-\u001f\u007f\u0085]/.test(s)) return "";
  const scheme = s.includes(":") ? s.slice(0, s.indexOf(":")).toLowerCase() : "";
  return scheme === "http" || scheme === "https" ? s : "";
}
