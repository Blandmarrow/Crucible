import { useCallback, useEffect, useReducer, useRef } from "react";
import { savePersisted } from "../utils/persistentState";
import { PERSIST_DEBOUNCE_MS } from "../constants/storage";

/**
 * Debounced persistence for a page's UI state blob: to localStorage, or wherever
 * `write` sends it.
 *
 * Replaces the hand-rolled `useEffect` + `setTimeout(…, 350)` + `clearTimeout`
 * pattern that every persisted page used to carry. That pattern's cleanup
 * *cancelled* the pending write, so a change made within the debounce window and
 * followed by a navigation was silently lost.
 *
 * @param key   storage key, or `null` to persist nothing this render (the
 *              per-dataset call sites pass `null` while `datasetId` is unset).
 * @param value the blob to store. Rebuilt every render by callers; it is compared
 *              by serialized form, not identity, so no `useMemo` is needed.
 * @param delay debounce window in ms. Defaults to `PERSIST_DEBOUNCE_MS`, the
 *              shared constant the e2e restore spec waits on.
 * @param write sends the blob somewhere other than localStorage. Must be stable. With
 *              one, a value equal to the last written is skipped, starting from the
 *              value at mount: a server write costs a request, and for data where NULL
 *              means "never saved" a write on mount would destroy that fact just by
 *              opening the page. The localStorage path keeps writing on mount.
 * @returns     `rebaseline`, for use with `write`: call it in the same update as a state
 *              change that isn't the user's edit, so that change isn't saved for them.
 *              An edit still waiting to be written goes ahead anyway, unless
 *              `discardPending` says it's being thrown away too (a reset).
 */
export function useDebouncedPersist(
  key: string | null,
  value: object,
  delay = PERSIST_DEBOUNCE_MS,
  write?: (key: string, value: object) => void,
): (discardPending?: boolean) => void {
  // The dependency. Callers build `value` from an object literal, so property
  // order is stable and JSON equality is a sound change check. This also spares
  // every call site a hand-maintained dep array (Captioning's was 23 entries).
  const serialized = JSON.stringify(value);

  // The write owed but not yet performed. Null when nothing is pending, so an
  // unmount after a completed write doesn't rewrite the same blob.
  const pendingRef = useRef<{ key: string; value: object; serialized: string } | null>(null);

  const writerRef = useRef(write);
  const lastWrittenRef = useRef(write ? serialized : null);

  const rebaselineRef = useRef(false);
  // The commit this forces is what ends a rebaseline, even if the state change turns out
  // to change nothing, which would otherwise swallow the next edit.
  const [rebaselineCommit, forceCommit] = useReducer((n: number) => n + 1, 0);
  const rebaseline = useCallback((discardPending = false) => {
    if (discardPending) pendingRef.current = null;
    rebaselineRef.current = true;
    forceCommit();
  }, []);

  const perform = (pending: { key: string; value: object; serialized: string }) => {
    if (writerRef.current) {
      writerRef.current(pending.key, pending.value);
      lastWrittenRef.current = pending.serialized;
    } else {
      savePersisted(pending.key, pending.value);
    }
  };

  useEffect(() => {
    if (!key) return;
    if (rebaselineRef.current && !pendingRef.current) lastWrittenRef.current = serialized;
    if (serialized === lastWrittenRef.current) {
      // Edited and changed back inside the window: nothing is owed any more.
      pendingRef.current = null;
      return;
    }
    const pending = { key, value, serialized };
    pendingRef.current = pending;
    const t = setTimeout(() => {
      // Discarded by a rebaseline that changed nothing, so no cleanup cancelled this.
      if (pendingRef.current !== pending) return;
      perform(pending);
      pendingRef.current = null;
    }, delay);
    // Cancel — deliberately NOT flush. `key` changes while mounted in split-pane
    // mode, and the effect that reloads the blob for the new dataset lands a
    // render later; flushing here would write the previous dataset's values under
    // the new dataset's key. Unmount is the only safe flush point, below.
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `serialized` stands in for `value`
  }, [key, serialized, delay]);

  // Declared after the effect above, so that has already seen the flag this commit. Keyed
  // on the forced commit and not run on every commit, because a caller's own effect can
  // ask for a rebaseline earlier in the very commit this would otherwise clear it in.
  useEffect(() => {
    rebaselineRef.current = false;
  }, [rebaselineCommit]);

  useEffect(() => {
    const flush = () => {
      const pending = pendingRef.current;
      if (!pending) return;
      perform(pending);
      pendingRef.current = null;
    };
    // `pagehide` covers tab close / reload inside the debounce window; the
    // cleanup covers ordinary unmount via navigation.
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, []);

  return rebaseline;
}
