import { useEffect, useRef, useState } from "react";

interface Props extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> {
  value: number;
  /** The caller's whole clamp — used to decide whether a draft can commit live,
   *  and applied again on blur. The single home for each field's bounds. */
  clamp: (n: number) => number;
  onCommit: (n: number) => void;
}

/**
 * A `type="number"` input that does not rewrite what you are typing.
 *
 * The naive form — `onChange={(e) => setX(clamp(Number(e.target.value)))}` —
 * re-clamps every intermediate prefix, so typing `2048` into a 64–8192 field
 * gives `8192`: the first keystroke `"2"` clamps up to `64` and the remaining
 * three append to it. Same shape turns `800` into `1600` in a crop width, and an
 * even-snap turns `150` into `250`.
 *
 * So the raw string is held in `draft` and clamped on blur — with one refinement:
 * **commit live whenever clamping would be the identity**, so a consumer that
 * paints from the value (the crop mattes) keeps moving for every keystroke that
 * is not a lie. `"8"` in a width is held; `"80"` and `"800"` commit immediately.
 *
 * There are no per-field `|| 1024`-style fallbacks anywhere in the commit path.
 * Those are artifacts of `Number("") === 0` and they make a typed `0`
 * indistinguishable from an empty field; an empty or unparseable field reverts to
 * the current `value` instead.
 *
 * Note that `type="number"` reports `""` for anything it does not consider a
 * valid float — `-`, `1e`, `1.2.3` — so those land in the revert-on-empty branch.
 * Harmless, but confusing to debug from the outside.
 */
export default function NumberField({ value, clamp, onCommit, onBlur, onKeyDown, ...rest }: Props) {
  const [draft, setDraft] = useState<string | null>(null);

  // Drop a stale draft when `value` changes underneath — the render-time-adjust
  // idiom React documents for derived state, used elsewhere in the extraction
  // modal. Not hypothetical: `CropOverlay` calls `preventDefault()` on its edge
  // handles' `pointerdown`, which suppresses the focus shift, so one of these
  // inputs keeps focus *and* keeps its draft while a drag rewrites the rect.
  const [seen, setSeen] = useState(value);
  if (value !== seen) {
    setSeen(value);
    setDraft(null);
  }

  function commit() {
    // Load-bearing, not tidiness: this is what stops a focus-and-tab with no
    // typing from firing `onCommit`. In the extraction modal that would trip
    // `cropTouched`, which writes the crop across the whole batch.
    if (draft === null) return;
    const n = Number(draft);
    setDraft(null);
    if (draft.trim() === "" || !Number.isFinite(n)) return;
    onCommit(clamp(n));
  }

  // Commit on unmount. React fires no blur for a focused element that is
  // removed, and the extraction modal swaps its whole step-1 subtree on **Next**
  // — without this a typed crop width is silently discarded.
  // The latest-ref pattern, kept current in its own dependency-free effect
  // rather than assigned during render (`react-hooks/refs` forbids the latter,
  // and it is the same rule that would make this stale).
  const commitRef = useRef(commit);
  useEffect(() => {
    commitRef.current = commit;
  });
  useEffect(() => () => commitRef.current(), []);

  return (
    <input
      {...rest}
      type="number"
      value={draft ?? String(value)}
      onChange={(e) => {
        const next = e.target.value;
        const n = Number(next);
        if (next.trim() !== "" && Number.isFinite(n) && clamp(n) === n) {
          setDraft(null);
          onCommit(n);
        } else {
          setDraft(next);
        }
      }}
      onBlur={(e) => {
        commit();
        onBlur?.(e);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        onKeyDown?.(e);
      }}
    />
  );
}
