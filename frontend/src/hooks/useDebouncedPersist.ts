import { useEffect, useRef } from "react";
import { savePersisted } from "../utils/persistentState";

/**
 * Debounced localStorage persistence for a page's UI state blob.
 *
 * Replaces the hand-rolled `useEffect` + `setTimeout(…, 350)` + `clearTimeout`
 * pattern that every persisted page used to carry. That pattern's cleanup
 * *cancelled* the pending write, so a change made within the debounce window and
 * followed by a navigation was silently lost.
 *
 * @param key   localStorage key, or `null` to persist nothing this render (the
 *              per-dataset call sites pass `null` while `datasetId` is unset).
 * @param value the blob to store. Rebuilt every render by callers; it is compared
 *              by serialized form, not identity, so no `useMemo` is needed.
 * @param delay debounce window in ms.
 */
export function useDebouncedPersist(key: string | null, value: object, delay = 350): void {
  // The dependency. Callers build `value` from an object literal, so property
  // order is stable and JSON equality is a sound change check. This also spares
  // every call site a hand-maintained dep array (Captioning's was 23 entries).
  const serialized = JSON.stringify(value);

  // The write owed to localStorage but not yet performed. Null when nothing is
  // pending, so an unmount after a completed write doesn't rewrite the same blob.
  const pendingRef = useRef<{ key: string; value: object } | null>(null);

  useEffect(() => {
    if (!key) return;
    pendingRef.current = { key, value };
    const t = setTimeout(() => {
      savePersisted(key, value);
      pendingRef.current = null;
    }, delay);
    // Cancel — deliberately NOT flush. `key` changes while mounted in split-pane
    // mode, and the effect that reloads the blob for the new dataset lands a
    // render later; flushing here would write the previous dataset's values under
    // the new dataset's key. Unmount is the only safe flush point, below.
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `serialized` stands in for `value`
  }, [key, serialized, delay]);

  useEffect(() => {
    const flush = () => {
      const pending = pendingRef.current;
      if (!pending) return;
      savePersisted(pending.key, pending.value);
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
}
