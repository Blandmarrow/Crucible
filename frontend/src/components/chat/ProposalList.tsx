import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { chatKey, chatPreviewKey, chatsApi, NOTE_SECTION_LABELS, type Proposal } from "../../api/chats";
import { APP_NOTES_KEY, datasetNotesKey } from "../../api/notes";
import { apiErrorDetail } from "../../utils/apiError";

const OP_LABEL: Record<Proposal["op"], string> = { add: "Add to", edit: "Change in", remove: "Remove from" };
const DONE_LABEL: Record<Proposal["op"], string> = { add: "Added", edit: "Changed", remove: "Removed" };

/** A reply's suggested changes to the notes, each waiting for the user. Nothing
 *  changes until Accept; Undo reverses an accepted change, or brings back a rejected
 *  suggestion. See docs/dev/analyst/proposals.md. */
export default function ProposalList({ chatId, datasetId, proposals, liveNoteIds }: {
  chatId: string;
  datasetId: string;
  proposals: Proposal[];
  /** Ids of the notes that exist now, or null while they load: an edit or removal
   *  whose note has gone is shown as out of date before the server says so. */
  liveNoteIds: Set<string> | null;
}) {
  if (!proposals.length) return null;
  return (
    <section aria-label="Suggested notes" style={{ marginTop: 10 }}>
      <div style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 6 }}>
        Suggested notes. Nothing changes until you accept.
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        {proposals.map((p) => (
          <ProposalRow key={p.id} chatId={chatId} datasetId={datasetId} proposal={p} liveNoteIds={liveNoteIds} />
        ))}
      </ul>
    </section>
  );
}

function ProposalRow({ chatId, datasetId, proposal: p, liveNoteIds }: {
  chatId: string;
  datasetId: string;
  proposal: Proposal;
  liveNoteIds: Set<string> | null;
}) {
  const qc = useQueryClient();
  const act = useMutation({
    mutationFn: (action: "accept" | "reject" | "undo") => chatsApi.actOnProposal(chatId, p.id, action),
    onSettled: () => Promise.all([
      qc.invalidateQueries({ queryKey: chatKey(chatId) }),
      qc.invalidateQueries({ queryKey: p.section === "about_me" ? APP_NOTES_KEY : datasetNotesKey(datasetId) }),
      // Removing a dismissal note brings its finding back, and the notes are context.
      qc.invalidateQueries({ queryKey: ["dataset-health", datasetId] }),
      qc.invalidateQueries({ queryKey: chatPreviewKey(chatId) }),
    ]),
    onError: (err) => toast.error(apiErrorDetail(err, "Could not apply the suggestion")),
  });

  const gone = p.status === "pending" && p.op !== "add" && !!liveNoteIds && !!p.target_item_id && !liveNoteIds.has(p.target_item_id);
  const stale = p.status === "stale" || gone;
  const muted = p.status === "rejected" || stale;

  return (
    <li
      className="card"
      data-testid="proposal"
      data-status={stale ? "stale" : p.status}
      style={{ padding: "8px 12px", display: "flex", gap: 10, alignItems: "flex-start", opacity: muted ? 0.7 : 1 }}
    >
      <div style={{ flex: 1, minWidth: 0, fontSize: 12.5 }}>
        <div style={{ color: "var(--fg-mute)", marginBottom: 3 }}>
          {OP_LABEL[p.op]} <strong style={{ color: "var(--fg)" }}>{NOTE_SECTION_LABELS[p.section]}</strong>
        </div>
        {p.op !== "add" && p.target_text !== null && (
          <div style={{ color: "var(--bad)", textDecoration: p.op === "remove" ? "line-through" : undefined, overflowWrap: "anywhere" }}>
            <span aria-hidden>− </span>{p.target_text}
          </div>
        )}
        {p.op !== "remove" && p.new_text && (
          <div style={{ color: "var(--good)", overflowWrap: "anywhere" }}>
            <span aria-hidden>+ </span>{p.new_text}
          </div>
        )}
        {stale && (
          <div style={{ fontSize: 11.5, color: "var(--warn)", marginTop: 3 }}>
            Out of date: that note has been removed since.
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
        {p.status === "pending" && !stale && (
          <>
            <button className="btn sm primary" disabled={act.isPending} onClick={() => act.mutate("accept")}>Accept</button>
            <button className="btn sm ghost" disabled={act.isPending} onClick={() => act.mutate("reject")}>Reject</button>
          </>
        )}
        {(p.status === "accepted" || p.status === "rejected") && (
          <>
            <span style={{ fontSize: 12, color: p.status === "accepted" ? "var(--good)" : "var(--fg-dim)" }}>
              {p.status === "accepted" ? DONE_LABEL[p.op] : "Rejected"}
            </span>
            <button className="btn sm ghost" disabled={act.isPending} onClick={() => act.mutate("undo")}>Undo</button>
          </>
        )}
      </div>
    </li>
  );
}
