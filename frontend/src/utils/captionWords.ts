/**
 * Word-level helpers for the caption editor's synonym card (#52). `captionTags.ts`
 * works at tag granularity (comma parts); these work on a textarea selection, which
 * can sit inside prose or inside one tag.
 */

/** A span of the caption, by character offsets, and the text it held when taken. */
export interface WordSpan {
  start: number;
  end: number;
  text: string;
}

/** Mirrors the server's limits (`dictionary_service.normalize`). */
export const MAX_SYNONYM_WORDS = 4;
export const MAX_SYNONYM_CHARS = 40;

const LETTER = /\p{L}/u;
const LETTER_OR_MARK = /[\p{L}\p{M}]/u;
// A combining mark belongs to its letter (`İ` can arrive as `I` + U+0307).
const WORDISH = /^\p{L}[\p{L}\p{M}'\-_ ]*$/u;

/**
 * The word (or short phrase) a textarea selection covers, or null when it is not one.
 *
 * Trims what a double-click or a loose drag picks up around the word: Chromium on
 * Windows selects the trailing space, and a drag can catch a comma or full stop.
 * Underscores count as spaces, so `school_uniform` in a tag caption is one phrase.
 */
export function selectedWord(text: string, start: number, end: number): WordSpan | null {
  if (end <= start) return null;
  let s = start;
  let e = end;
  while (s < e && !LETTER.test(text[s])) s++;
  while (e > s && !LETTER_OR_MARK.test(text[e - 1])) e--;
  const picked = text.slice(s, e);
  if (!picked || picked.length > MAX_SYNONYM_CHARS || !WORDISH.test(picked)) return null;
  if (picked.split(/[ _]+/).length > MAX_SYNONYM_WORDS) return null;
  return { start: s, end: e, text: picked };
}

/** `word` spelled the way `source` is: its case, and underscores if it used them. */
export function matchStyle(source: string, word: string): string {
  let out = word;
  const letters = source.replace(/[^\p{L}]/gu, "");
  if (letters.length > 1 && letters === letters.toUpperCase() && letters !== letters.toLowerCase()) {
    out = out.toUpperCase();
  } else if (letters && letters[0] === letters[0].toUpperCase() && letters[0] !== letters[0].toLowerCase()) {
    out = out.charAt(0).toUpperCase() + out.slice(1);
  }
  return source.includes("_") ? out.replace(/ /g, "_") : out;
}

/**
 * `text` with `span` replaced by `word`, styled after `styleFrom` (the word the user
 * first selected, so a run of swaps keeps its capitals and underscores), plus the span
 * the new word now occupies. Null when the span no longer holds what it held: the user
 * may have edited the box since, and offsets alone would then overwrite the wrong
 * letters (the same guard as `captionTags.replaceTag`).
 */
export function replaceSpan(
  text: string,
  span: WordSpan,
  word: string,
  styleFrom: string = span.text,
): { text: string; span: WordSpan } | null {
  if (text.slice(span.start, span.end) !== span.text) return null;
  const styled = matchStyle(styleFrom, word);
  return {
    text: text.slice(0, span.start) + styled + text.slice(span.end),
    span: { start: span.start, end: span.start + styled.length, text: styled },
  };
}
