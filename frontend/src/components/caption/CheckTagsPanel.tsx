import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { SearchCheck } from "lucide-react";
import { booruApi } from "../../api/booru";
import type { TagCheckResult, TagCheckStatus } from "../../types";
import { apiErrorDetail } from "../../utils/apiError";
import { replaceTag, splitTags } from "../../utils/captionTags";

const CHIP: Record<TagCheckStatus, { label: string; cls: string; title: string }> = {
  alias: { label: "alias", cls: "bg-info-bg text-info", title: "Danbooru renames this tag" },
  rare: { label: "rare", cls: "bg-warn-bg text-warn", title: "Under 100 posts, while a close tag has at least 100 times more" },
  missing: { label: "not found", cls: "bg-bad-bg text-bad", title: "Danbooru has no tag by this name, or it has no posts" },
  ok: { label: "ok", cls: "bg-good-bg text-accent-text", title: "A real Danbooru tag" },
};
const ORDER: TagCheckStatus[] = ["alias", "rare", "missing"];

const fmt = (n: number) => n.toLocaleString();

/** The suggestion line's lead-in, per status. */
function lead(r: TagCheckResult): string {
  if (r.status === "alias") return "Danbooru uses";
  if (r.status === "rare") return "Try";
  return "Closest:";
}

interface Props {
  /** The live draft-or-saved caption. Replace edits it; the check reads it. */
  captionText: string;
  /** Put text in the unsaved draft. Never saves. */
  onSetDraft: (text: string) => void;
}

/**
 * Check tags on Danbooru (#50). Runs only on a press, since it sends the caption's tags off
 * the machine: a mutation, never a query, so nothing refetches it on focus or mount.
 * Mounted keyed on the image, so a result never outlives the image it was made for.
 */
export default function CheckTagsPanel({ captionText, onSetDraft }: Props) {
  const [showOk, setShowOk] = useState(false);
  const check = useMutation({ mutationFn: (tags: string[]) => booruApi.check(tags) });

  const results = check.data?.results ?? [];
  const problems = ORDER.flatMap((s) => results.filter((r) => r.status === s));
  const fine = results.filter((r) => r.status === "ok");

  const run = () => {
    setShowOk(false);
    check.mutate(splitTags(captionText));
  };

  return (
    <div className="space-y-2" data-testid="check-tags">
      <button
        type="button"
        className="btn-ghost btn-sm w-full flex items-center justify-center gap-2"
        onClick={run}
        disabled={check.isPending}
        title="Look every tag up on Danbooru and flag renamed, rare or unknown ones. Sends this caption's tags to danbooru.donmai.us."
        data-testid="check-tags-run"
      >
        <SearchCheck size={14} /> {check.isPending ? "Checking…" : "Check tags on Danbooru"}
      </button>

      {check.isError && (
        <p className="text-xs text-bad" role="alert" data-testid="check-tags-error">
          {apiErrorDetail(check.error, "Could not reach Danbooru")}. Nothing was marked.
        </p>
      )}

      {check.isSuccess && (
        <div className="space-y-2" aria-live="polite">
          <p className="text-xs text-fg-mute" data-testid="check-tags-summary">
            {fine.length} of {results.length} {results.length === 1 ? "tag looks" : "tags look"} fine
            {problems.length > 0 && ` · ${problems.length} to review`}
          </p>
          {!check.data.suggestions_complete && (
            <p className="text-xs text-fg-mute">Some suggestions could not be fetched. Check again to retry.</p>
          )}
          {problems.length > 0 && (
            <ul className="rounded border border-line divide-y divide-line">
              {problems.map((r, i) => (
                <Row key={`${r.input}-${i}`} r={r} captionText={captionText} onSetDraft={onSetDraft} />
              ))}
            </ul>
          )}
          {fine.length > 0 && (
            <button
              type="button"
              className="text-xs text-fg-mute hover:text-fg underline-offset-2 hover:underline"
              onClick={() => setShowOk(!showOk)}
              aria-expanded={showOk}
            >
              {showOk ? "Hide" : "Show"} the {fine.length} fine {fine.length === 1 ? "tag" : "tags"}
            </button>
          )}
          {showOk && (
            <ul className="rounded border border-line divide-y divide-line">
              {fine.map((r, i) => (
                <Row key={`${r.input}-${i}`} r={r} captionText={captionText} onSetDraft={onSetDraft} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ r, captionText, onSetDraft }: { r: TagCheckResult } & Props) {
  const chip = CHIP[r.status];
  const replaced = r.suggestion ? replaceTag(captionText, r.input, r.suggestion.name) : null;
  return (
    <li className="px-2 py-1.5 space-y-1 bg-surface-2" data-testid="check-tags-row" data-status={r.status}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-mono text-fg truncate" title={r.input}>{r.input}</span>
        <span className="text-[11px] text-fg-mute tabular-nums shrink-0">{r.count > 0 ? fmt(r.count) : "—"}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-fg-mute">
        <span className={`rounded-full px-1.5 font-semibold ${chip.cls}`} title={chip.title}>{chip.label}</span>
        {r.status === "ok" && r.category && <span>{r.category}</span>}
        {r.suggestion && (
          <>
            <span>{lead(r)}</span>
            {/* Spelled the way Replace will write it into this caption. */}
            <span className="font-mono text-fg">
              {r.input.includes("_") ? r.suggestion.name : r.suggestion.name.replace(/_/g, " ")}
            </span>
            <span className="tabular-nums">{fmt(r.suggestion.count)}</span>
            <button
              type="button"
              className="btn-ghost btn-sm"
              disabled={replaced === null}
              onClick={() => replaced !== null && onSetDraft(replaced)}
              title={
                replaced === null
                  ? "This tag is no longer in the caption"
                  : `Replace "${r.input}" in the caption. Nothing is saved until you press Save.`
              }
              aria-label={`Replace ${r.input} with ${r.suggestion.name}`}
              data-testid="check-tags-replace"
            >
              Replace
            </button>
          </>
        )}
        {r.status === "missing" && !r.suggestion && <span>No close tag found</span>}
        {r.status === "rare" && !r.suggestion && <span>Close tags could not be looked up</span>}
      </div>
    </li>
  );
}
