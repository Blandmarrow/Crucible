import { useEffect, useState } from "react";

/**
 * Lazily-loaded caption tokenizer, GPT-2 aligned.
 *
 * The token counter is only needed on the image-detail caption editor, so the
 * ~encoder chunk is code-split behind a one-time dynamic import instead of being
 * pulled into the page's static graph. We load `r50k_base` (GPT-2 / the `gpt2`
 * BPE) so the client count matches the backend `caption_token_count`
 * (`utils.count_caption_tokens`, a GPT-2 tiktoken encoder) — see CLAUDE.md.
 */
type EncodeFn = (text: string) => number[];

let encodeFn: EncodeFn | null = null;
let loadPromise: Promise<void> | null = null;

/** Kick off (or reuse) the one-time encoder import. Resolves when ready. */
export function loadTokenizer(): Promise<void> {
  if (encodeFn) return Promise.resolve();
  if (!loadPromise) {
    loadPromise = import("gpt-tokenizer/encoding/r50k_base")
      .then((m) => {
        encodeFn = m.encode;
      })
      .catch((err) => {
        // Reset so a later mount can retry rather than being stuck "not ready".
        loadPromise = null;
        throw err;
      });
  }
  return loadPromise;
}

/** GPT-2 token count, or `null` if the encoder hasn't loaded yet. */
export function countTokens(text: string): number | null {
  if (!encodeFn) return null;
  const trimmed = text.trim();
  return trimmed ? encodeFn(trimmed).length : 0;
}

/**
 * Live GPT-2 token count for `text`. Triggers the one-time encoder load on
 * mount and returns `null` until it is ready (callers render a placeholder),
 * then re-counts synchronously as `text` changes.
 */
export function useTokenCount(text: string): number | null {
  const [ready, setReady] = useState(encodeFn !== null);

  useEffect(() => {
    if (ready) return;
    let cancelled = false;
    loadTokenizer()
      .then(() => {
        if (!cancelled) setReady(true);
      })
      .catch(() => {
        /* leave placeholder in place on failure */
      });
    return () => {
      cancelled = true;
    };
  }, [ready]);

  return ready ? countTokens(text) : null;
}
