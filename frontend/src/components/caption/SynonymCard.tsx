import { useEffect, useRef, type RefObject } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { BookOpen, X } from "lucide-react";
import { dictionaryApi } from "../../api/dictionary";
import { synonymsKey } from "../../constants/queryKeys";
import { dictionaryBusy, useDictionaryActions, useDictionaryStatus } from "../../hooks/useDictionaryStatus";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import type { SynonymSense } from "../../types";
import { apiErrorDetail } from "../../utils/apiError";
import { replaceSpan, type WordSpan } from "../../utils/captionWords";

const POS: Record<SynonymSense["pos"], string> = { n: "noun", v: "verb", a: "adjective", r: "adverb" };

interface Props {
  /** Where the current word sits: the selection, or the word a swap put there. */
  span: WordSpan;
  /** The word the user selected. The card looks it up and styles every swap after it,
   *  so the list stays put while the user tries its words one after another. */
  word: string;
  /** The live draft-or-saved caption. A swap edits it. */
  captionText: string;
  /** Put the swapped text in the unsaved draft, never saving, and point the card at the
   *  span the new word occupies. That re-pointing matters: a span left on the old text
   *  after "blond" → "blonde" still matches, and a second swap would garble it. */
  onSwap: (text: string, span: WordSpan) => void;
  onClose: () => void;
  /** The caption box: a press inside it is not a click "elsewhere". */
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}

/**
 * Other words for the selected one (#52), under the caption box. Offline: the lookup
 * reads the WordNet file the server installed, so unlike Check tags it is a plain
 * query, debounced so a drag-select does not send one request per letter.
 */
export default function SynonymCard({ span, word: selected, captionText, onSwap, onClose, textareaRef }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const status = useDictionaryStatus();
  const { install } = useDictionaryActions();
  const installed = status.data?.installed ?? false;
  const word = useDebouncedValue(selected, 150);

  const lookup = useQuery({
    queryKey: synonymsKey(word.toLowerCase()),
    queryFn: ({ signal }) => dictionaryApi.synonyms(word, signal),
    enabled: installed,
    staleTime: Infinity,
    retry: false,
    // While the next word loads, keep the last list on screen (dimmed, chips off) rather
    // than a one-line "Looking up…": a card that shrinks and regrows between words makes
    // the panel's scroll clamp, and the caption moves under the pointer.
    placeholderData: keepPreviousData,
  });

  // A click anywhere but the card or the caption box closes it. On `click`, not on the
  // box's blur (which fires before a chip's click) and not on `pointerdown`: the card
  // sits above Save, so closing at pointerdown moved Save out from under the pointer
  // and the click never reached it.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      // The path as the click began, not `contains()`: Put back unmounts itself as it
      // is clicked, and a detached target would read as a click outside.
      const path = e.composedPath();
      if ((ref.current && path.includes(ref.current)) || (textareaRef.current && path.includes(textareaRef.current))) return;
      onClose();
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [onClose, textareaRef]);

  const data = lookup.data;
  // `data` can be the previous word's while the debounce runs or the next one loads.
  const settled = !!data && word === selected && !lookup.isPlaceholderData;
  const swapped = span.text !== selected;
  const putBack = swapped ? replaceSpan(captionText, span, selected, selected) : null;
  const shown = data && (settled || data.senses.length > 0) ? data : undefined;
  const lemmas = data?.lemmas.filter((l) => l !== data.word) ?? [];

  return (
    <div
      ref={ref}
      className="mt-2 rounded border border-line-2 bg-surface-3 p-2.5 space-y-2 shadow-lg"
      data-testid="synonym-card"
      aria-label={`Synonyms for ${selected}`}
      role="region"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          // Not the page's annotation-mode Escape too (ImageDetailPage's window listener).
          e.stopPropagation();
          onClose();
          textareaRef.current?.focus();
        }
      }}
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="min-w-0 text-xs">
          <BookOpen size={12} className="inline mr-1.5 text-accent-text" aria-hidden />
          <b className="font-mono text-fg-strong">{selected}</b>
          {settled && lemmas.length > 0 && (
            <span className="text-fg-dim" data-testid="synonym-lemma"> · words for {lemmas.join(", ")}</span>
          )}
          {putBack && (
            <button
              type="button"
              className="ml-2 text-fg-mute hover:text-fg underline-offset-2 hover:underline"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onSwap(putBack.text, putBack.span)}
              title={`Put “${selected}” back in place of “${span.text}”. Nothing is saved until you press Save.`}
              data-testid="synonym-put-back"
            >
              Put back
            </button>
          )}
        </div>
        <button
          type="button"
          className="text-fg-dim hover:text-fg shrink-0"
          onClick={onClose}
          aria-label="Close synonyms"
          title="Close (Esc)"
        >
          <X size={14} />
        </button>
      </div>

      {!installed && <NotInstalled status={status.data} onInstall={() => install.mutate()} pending={install.isPending} />}
      {installed && lookup.isError && (
        <p className="text-xs text-bad" role="alert">{apiErrorDetail(lookup.error, "Could not look the word up")}</p>
      )}
      {installed && !lookup.isError && !shown && <p className="text-xs text-fg-mute">Looking up…</p>}
      {settled && shown && shown.senses.length === 0 && (
        <p className="text-xs text-fg-mute" data-testid="synonym-none">No synonyms for “{selected}”.</p>
      )}
      {shown && shown.senses.length > 0 && (
        <ul
          className={`space-y-2 transition-opacity ${settled ? "" : "opacity-50"}`}
          aria-live="polite"
          aria-busy={!settled}
          data-testid="synonym-list"
        >
          {shown.senses.map((s, i) => (
            <li key={i} className="space-y-1" data-testid="synonym-sense">
              <div className="text-[11px] text-fg-mute">
                <span className="uppercase tracking-wider text-fg-dim">{POS[s.pos]}</span>
                <span className="italic"> · {s.definition}</span>
              </div>
              <div className="flex flex-wrap gap-1">
                {s.words.map((w) => {
                  // The previous word's chips stay visible while the next loads, but
                  // must not swap into the new selection.
                  const next = settled ? replaceSpan(captionText, span, w, selected) : null;
                  const current = !!next && next.span.text === span.text;
                  return (
                    <button
                      key={w}
                      type="button"
                      className={`rounded-full border px-2 py-0.5 text-xs disabled:opacity-50 ${
                        current
                          ? "border-accent bg-accent-glow text-accent-text"
                          : "border-line-2 bg-surface-2 text-fg hover:border-accent hover:text-accent-text"
                      }`}
                      aria-pressed={current}
                      disabled={next === null}
                      // Keep focus in the caption box, so keys after a swap edit the
                      // caption rather than reach the page's arrow and label hotkeys.
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => next !== null && !current && onSwap(next.text, next.span)}
                      title={
                        !settled
                          ? "Looking up the selected word…"
                          : next === null
                            ? "The selected word has changed"
                            : `Use “${w}”. Nothing is saved until you press Save.`
                      }
                      data-testid="synonym-word"
                    >
                      {w}
                    </button>
                  );
                })}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NotInstalled({
  status,
  onInstall,
  pending,
}: {
  status: ReturnType<typeof useDictionaryStatus>["data"];
  onInstall: () => void;
  pending: boolean;
}) {
  if (dictionaryBusy(status)) {
    return (
      <p className="text-xs text-fg-mute" data-testid="synonym-installing">
        {status!.message || "Installing the dictionary…"}
      </p>
    );
  }
  return (
    <div className="space-y-1.5" data-testid="synonym-not-installed">
      <p className="text-xs text-fg-mute">
        {status?.state === "failed" && status.error ? status.error : "The dictionary is not installed yet."}
      </p>
      <button type="button" className="btn-ghost btn-sm" onClick={onInstall} disabled={pending || !status}>
        {status?.state === "failed" ? "Retry the download" : "Download it (about 10 MB)"}
      </button>
    </div>
  );
}
