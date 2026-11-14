/** Client mirror of the server's tag-like rule for captions (similar_captions_service). */

/** Case-, whitespace- and `_`-insensitive form of a tag, for comparing. */
export function normTag(tag: string): string {
  return tag.replace(/_/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
}

/** The comma parts of a caption, trimmed, empty parts dropped, spelling kept. */
export function splitTags(text: string): string[] {
  return text.split(",").map((p) => p.trim()).filter(Boolean);
}

/** Tag-like: at least two comma parts and most of them four words or fewer. */
export function looksLikeTags(text: string): boolean {
  const parts = splitTags(text);
  if (parts.length < 2) return false;
  const short = parts.filter((p) => normTag(p).split(" ").length <= 4).length;
  return short > parts.length / 2;
}

/**
 * Whether tags can be appended to this caption: it is blank, tag-like, or a single
 * short tag with no sentence punctuation (`1girl`, the start of hand-tagging).
 */
export function canTakeTags(text: string): boolean {
  const parts = splitTags(text);
  if (parts.length === 0) return true;
  if (parts.length === 1) return normTag(parts[0]).split(" ").length <= 4 && !/[.!?]/.test(parts[0]);
  return looksLikeTags(text);
}

/** The other caption's tags that the current one lacks, in the other's spelling, no repeats. */
export function missingTags(current: string, other: string): string[] {
  const have = new Set(splitTags(current).map(normTag));
  const out: string[] = [];
  for (const t of splitTags(other)) {
    const n = normTag(t);
    if (!n || have.has(n)) continue;
    have.add(n);
    out.push(t);
  }
  return out;
}

/** Append tags to a caption with ", ". No leading comma when it is blank. */
export function appendTags(current: string, tags: string[]): string {
  const base = current.trim().replace(/,\s*$/, "").trimEnd();
  const add = tags.join(", ");
  if (!add) return current;
  return base ? `${base}, ${add}` : add;
}

/**
 * Swap one tag in a caption for a Danbooru tag name, or null when the tag is no longer there.
 *
 * Found by its text, not its position, so it is safe after other edits; the first exact
 * match wins, then the first `normTag` match. The name is written in the caption's style:
 * underscores only if the original part used them, and parens escaped (`\(`) if the
 * caption escapes them. The part's surrounding whitespace is kept.
 */
export function replaceTag(caption: string, original: string, name: string): string | null {
  const parts = caption.split(",");
  let i = parts.findIndex((p) => p.trim() === original);
  if (i < 0) i = parts.findIndex((p) => normTag(p) === normTag(original) && normTag(p) !== "");
  if (i < 0) return null;
  const raw = parts[i];
  let word = raw.includes("_") ? name : name.replace(/_/g, " ");
  if (/\\[()]/.test(caption)) word = word.replace(/(?<!\\)([()])/g, "\\$1");
  parts[i] = raw.match(/^\s*/)![0] + word + raw.match(/\s*$/)![0];
  return parts.join(",");
}
