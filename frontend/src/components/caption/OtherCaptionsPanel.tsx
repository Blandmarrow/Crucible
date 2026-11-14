import { useEffect, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, Copy, ExternalLink, Library, Plus } from "lucide-react";
import toast from "react-hot-toast";
import { imagesApi } from "../../api/images";
import { similarCaptionsKey } from "../../constants/queryKeys";
import { OTHER_CAPTIONS_KEY } from "../../constants/storage";
import type { SimilarCaptionItem, SimilarCaptionsBy } from "../../types";
import { apiErrorDetail } from "../../utils/apiError";
import { appendTags, canTakeTags, looksLikeTags, missingTags, normTag, splitTags } from "../../utils/captionTags";
import { copyText } from "../../utils/clipboard";
import { formatTimeAgo } from "../../utils/duration";
import { loadPersisted, savePersisted } from "../../utils/persistentState";

const MODES: { id: SimilarCaptionsBy; label: string; title: string }[] = [
  { id: "tags", label: "Shared tags", title: "Captions that share tags with this one, rarer tags counting more" },
  { id: "objects", label: "Objects", title: "Captions of images with the same detected objects" },
  { id: "clip", label: "Looks alike", title: "Captions of images that look most like this one (needs Style embeddings · CLIP on the Quality page)" },
  { id: "recent", label: "Recent", title: "The most recently captioned images in this dataset" },
];

interface Prefs { open: boolean; by: SimilarCaptionsBy }
const DEFAULT_PREFS: Prefs = { open: false, by: "tags" };

/** A stored blob from an older build, or a hand edit, must not send a bad `by`. */
function validPrefs(p: Prefs): Prefs {
  return { open: p.open === true, by: MODES.some((m) => m.id === p.by) ? p.by : "tags" };
}

/** How long an image must stay on screen before its list is fetched. Arrowing through
 *  images then starts no dataset scan per image passed over. */
const SETTLE_MS = 300;

/**
 * Shared tags / Objects / Looks alike / Recent segmented control.
 * Module scope, so typing elsewhere never remounts it and drops focus.
 */
function ModeSwitch({ by, onSelect }: { by: SimilarCaptionsBy; onSelect: (m: SimilarCaptionsBy) => void }) {
  return (
    <div className="flex flex-wrap gap-1 text-[11px]" role="group" aria-label="Rank other captions by">
      {MODES.map((m) => (
        <button
          key={m.id}
          type="button"
          aria-pressed={by === m.id}
          onClick={() => onSelect(m.id)}
          title={m.title}
          data-testid={`other-captions-mode-${m.id}`}
          className={`px-2 py-0.5 rounded ${by === m.id ? "bg-accent text-on-accent" : "bg-surface-2 text-fg-mute hover:text-fg"}`}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}

/** The caption with the tags the current caption also has picked out. */
function Highlighted({ text, have }: { text: string; have: Set<string> }) {
  const parts = splitTags(text);
  return (
    <>
      {parts.map((p, i) => (
        <span key={i}>
          {have.has(normTag(p)) ? <span className="text-accent-text font-medium">{p}</span> : p}
          {i < parts.length - 1 ? ", " : ""}
        </span>
      ))}
    </>
  );
}

function chipText(by: SimilarCaptionsBy, it: SimilarCaptionItem, liveShared: number | null): string | null {
  if (by === "tags" || by === "objects") return `${liveShared ?? it.shared.length} shared`;
  if (by === "clip") return `${Math.round(it.score * 100)}% alike`;
  return it.captioned_at ? formatTimeAgo(it.captioned_at) : null;
}

interface Props {
  imageId: string;
  datasetId: string;
  /** The live draft-or-saved caption, so highlights and "missing" follow unsaved typing. */
  captionText: string;
  /** Put text in the unsaved draft. Never saves. */
  onSetDraft: (text: string) => void;
  onOpen: (id: string) => void;
}

export default function OtherCaptionsPanel({ imageId, datasetId, captionText, onSetDraft, onOpen }: Props) {
  const [prefs, setPrefs] = useState<Prefs>(() => validPrefs(loadPersisted(OTHER_CAPTIONS_KEY, DEFAULT_PREFS)));
  // The panel is keyed on the image, so this restarts on every image.
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setSettled(true), SETTLE_MS);
    return () => clearTimeout(t);
  }, []);
  const bodyId = useId();
  const write = (next: Prefs) => { setPrefs(next); savePersisted(OTHER_CAPTIONS_KEY, next); };
  const { open, by } = prefs;

  // Not under ["images", datasetId]: see `similarCaptionsScope` for why.
  const q = useQuery({
    queryKey: similarCaptionsKey(datasetId, imageId, by),
    queryFn: ({ signal }) => imagesApi.similarCaptions(imageId, by, 8, signal),
    enabled: open && settled,
  });

  const haveTags = new Set(splitTags(captionText).map(normTag));
  const currentOk = canTakeTags(captionText);

  const copy = async (text: string) => {
    if (await copyText(text)) toast.success("Caption copied");
    else toast.error("Could not copy the caption");
  };

  return (
    <div className="border-t border-line pt-3">
      <button
        type="button"
        className="flex items-center justify-between w-full text-sm font-medium text-fg hover:text-fg-strong transition-colors"
        onClick={() => write({ ...prefs, open: !open })}
        aria-expanded={open}
        aria-controls={bodyId}
        data-testid="other-captions-toggle"
      >
        <span className="flex items-center gap-2">
          <Library size={14} className="text-accent-text" /> Other captions
        </span>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>

      {open && (
        <div className="mt-3 space-y-3" id={bodyId}>
          <ModeSwitch by={by} onSelect={(m) => write({ ...prefs, by: m })} />

          {q.isPending ? (
            <p className="text-xs text-fg-mute" aria-live="polite">Looking…</p>
          ) : q.isError ? (
            <p className="text-xs text-fg-mute" role="alert">
              Could not load other captions: {apiErrorDetail(q.error, "try again")}
            </p>
          ) : q.data && !q.data.available ? (
            <p className="text-xs text-fg-mute" aria-live="polite" data-testid="other-captions-hint">{q.data.reason ?? "Not available for this image."}</p>
          ) : q.data && q.data.items.length === 0 ? (
            <p className="text-xs text-fg-mute" aria-live="polite" data-testid="other-captions-hint">No other captioned images match.</p>
          ) : (
            <ul className="space-y-2">
              {q.data?.items.map((it) => {
                const tagLike = looksLikeTags(it.caption_text);
                // A prose caption's comma parts are sentence fragments, not tags.
                const missing = tagLike ? missingTags(captionText, it.caption_text) : [];
                // Count against the live draft, as the highlights do, when both are tags.
                const liveShared =
                  by === "tags" && tagLike && canTakeTags(captionText)
                    ? splitTags(it.caption_text).filter((p) => haveTags.has(normTag(p))).length
                    : null;
                const chip = chipText(by, it, liveShared);
                return (
                  <li
                    key={it.id}
                    className="rounded border border-line p-2 space-y-1.5"
                    data-testid="other-captions-row"
                  >
                    <div className="flex gap-2">
                      <img
                        src={imagesApi.thumbnailUrlVersioned(it.id, it.updated_at)}
                        alt=""
                        loading="lazy"
                        className="shrink-0 rounded object-cover"
                        style={{ width: 52, height: 52 }}
                        onError={(e) => { e.currentTarget.style.visibility = "hidden"; }}
                      />
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="flex items-center justify-between gap-2 text-[11px]">
                          <span className="truncate text-fg" title={it.filename}>{it.filename}</span>
                          {chip && <span className="shrink-0 text-fg-mute" data-testid="other-captions-chip">{chip}</span>}
                        </div>
                        <p className="text-xs text-fg-mute line-clamp-2 break-words" title={it.caption_text}>
                          {tagLike ? <Highlighted text={it.caption_text} have={haveTags} /> : it.caption_text}
                        </p>
                      </div>
                    </div>
                    {/* Under the whole card, not beside the thumbnail: the caption column is
                        too narrow for three buttons at the panel's usual width. */}
                    <div className="flex flex-wrap gap-1">
                      <button
                        type="button"
                        className="btn-ghost btn-sm flex items-center gap-1"
                        onClick={() => copy(it.caption_text)}
                        aria-label={`Copy the caption of ${it.filename}`}
                      >
                        <Copy size={12} /> Copy
                      </button>
                      {missing.length > 0 && currentOk && (
                        <button
                          type="button"
                          className="btn-ghost btn-sm flex items-center gap-1"
                          onClick={() => onSetDraft(appendTags(captionText, missing))}
                          title={`Add the ${missing.length === 1 ? "tag" : `${missing.length} tags`} this caption has and yours lacks: ${missing.join(", ")}. Nothing is saved until you press Save.`}
                          aria-label={`Add ${missing.length} missing ${missing.length === 1 ? "tag" : "tags"} from ${it.filename}`}
                          data-testid="other-captions-add-missing"
                        >
                          <Plus size={12} /> Add {missing.length} {missing.length === 1 ? "tag" : "tags"}
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn-ghost btn-sm flex items-center gap-1"
                        onClick={() => onOpen(it.id)}
                        aria-label={`Open ${it.filename}`}
                        data-testid="other-captions-open"
                      >
                        <ExternalLink size={12} /> Open
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
