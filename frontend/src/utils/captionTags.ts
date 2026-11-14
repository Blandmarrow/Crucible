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
