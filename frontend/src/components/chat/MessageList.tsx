import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import type { ChatMessage } from "../../api/chats";
import type { Finding } from "../../api/health";
import type { ChatStream } from "../../store/chatStore";
import FindingCard from "../health/FindingCard";
import ChatMarkdown from "./ChatMarkdown";

/** What a reply that did not finish normally says under its text. */
const ERROR_TEXT: Record<string, string> = {
  timeout: "The provider went quiet, so Crucible stopped waiting.",
  interrupted: "Crucible stopped before the reply finished.",
  empty: "The model sent an empty reply.",
  transient: "The provider could not answer just now. Try again.",
  rejected: "The provider refused the request.",
  fatal: "The request failed.",
};

/** One row as shown: a stored message, or the reply being written. */
interface Row {
  key: string;
  role: "user" | "assistant";
  content: string;
  message: ChatMessage | null;
  live: boolean;
}

/** The stored messages with the live reply laid over them. The stream's copy wins for
 *  its own row while it runs; its user message shows until the stored copy arrives. */
function mergeRows(messages: ChatMessage[], stream: ChatStream | undefined): Row[] {
  const rows: Row[] = messages.map((m) => ({ key: `m${m.id}`, role: m.role, content: m.content, message: m, live: false }));
  if (!stream) return rows;
  if (stream.userText !== null && !(stream.userMessageId && messages.some((m) => m.id === stream.userMessageId))) {
    rows.push({ key: "live-user", role: "user", content: stream.userText, message: null, live: false });
  }
  const i = stream.messageId === null ? -1 : rows.findIndex((r) => r.message?.id === stream.messageId);
  const live: Row = { key: i >= 0 ? rows[i].key : "live-reply", role: "assistant", content: stream.text, message: i >= 0 ? rows[i].message : null, live: stream.phase !== "ended" };
  if (i >= 0) rows[i] = live;
  else rows.push(live);
  return rows;
}

export default function MessageList({ messages, stream, findings, onShowFinding, onOpenGallery, onFix }: {
  messages: ChatMessage[];
  stream: ChatStream | undefined;
  findings: Map<string, Finding>;
  onShowFinding: (f: Finding) => void;
  onOpenGallery: (f: Finding) => void;
  onFix: (f: Finding) => void;
}) {
  const rows = useMemo(() => mergeRows(messages, stream), [messages, stream]);
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // Follow the reply while the reader is at the bottom; leave them alone once they
  // scroll up to read something earlier.
  useLayoutEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [rows]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onScroll = () => { pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const lastReply = [...rows].reverse().find((r) => r.role === "assistant")?.key;

  return (
    <div className="chat-messages" ref={box} aria-live="polite" aria-busy={!!stream && stream.phase !== "ended"} data-testid="chat-messages">
      {rows.length === 0 && (
        <div style={{ color: "var(--fg-mute)", fontSize: 13, margin: "auto 0", textAlign: "center" }}>
          Ask about this dataset. The analyst sees its findings, and whatever else this chat's
          privacy settings allow (“What gets sent”).
        </div>
      )}
      {rows.map((r) =>
        r.role === "user" ? (
          <div key={r.key} className="chat-msg user" data-testid="chat-user-message">{r.content}</div>
        ) : (
          <Reply
            key={r.key}
            row={r}
            stream={r.live || (stream && r.message?.id === stream.messageId) ? stream : undefined}
            findings={findings}
            stripOpen={r.key === lastReply}
            onShowFinding={onShowFinding}
            onOpenGallery={onOpenGallery}
            onFix={onFix}
          />
        ),
      )}
    </div>
  );
}

function Reply({ row, stream, findings, stripOpen, onShowFinding, onOpenGallery, onFix }: {
  row: Row;
  stream: ChatStream | undefined;
  findings: Map<string, Finding>;
  stripOpen: boolean;
  onShowFinding: (f: Finding) => void;
  onOpenGallery: (f: Finding) => void;
  onFix: (f: Finding) => void;
}) {
  const m = row.message;
  const writing = row.live || (!stream && m?.status === "partial");
  // Ids the current report no longer knows are left out, not shown as dead cards.
  const related = (m?.related_findings ?? []).map((id) => findings.get(id)).filter((f): f is Finding => !!f);

  return (
    <div className="chat-msg assistant" data-testid="chat-reply" data-status={writing ? "writing" : m?.status}>
      {row.content ? (
        <ChatMarkdown text={row.content} findings={findings} />
      ) : writing ? (
        <span style={{ color: "var(--fg-mute)", fontSize: 12.5 }}>
          {stream?.phase === "connecting" ? "Sending…" : "Waiting for the first words…"}
        </span>
      ) : null}
      {writing && row.content && <span className="chat-cursor" aria-hidden />}

      {!writing && m && <Ending message={m} />}

      {!writing && related.length > 0 && (
        <details open={stripOpen} style={{ marginTop: 10 }}>
          <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--fg-mute)" }}>
            Related findings ({related.length})
          </summary>
          <ul aria-label="Related findings" style={{ listStyle: "none", margin: "8px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {related.map((f) => (
              <FindingCard
                key={f.id}
                finding={f}
                compact
                testId={`related-${f.id}`}
                onShow={() => onShowFinding(f)}
                onOpenGallery={() => onOpenGallery(f)}
                onFix={() => onFix(f)}
              />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** How the reply ended, and what it cost. */
function Ending({ message: m }: { message: ChatMessage }) {
  const tokens = m.tokens_in !== null || m.tokens_out !== null
    ? `${(m.tokens_in ?? 0).toLocaleString()} in · ${(m.tokens_out ?? 0).toLocaleString()} out${m.tokens_estimated ? " (estimated)" : ""}`
    : null;
  return (
    <>
      {m.status === "cancelled" && (
        <div style={{ fontSize: 12, color: "var(--fg-mute)", marginTop: 6 }} data-testid="chat-stopped">Stopped. What arrived is kept.</div>
      )}
      {m.status === "error" && (
        <div role="alert" style={{ fontSize: 12.5, color: "var(--bad)", marginTop: 6 }} data-testid="chat-error">
          {m.error_detail || ERROR_TEXT[m.error_kind ?? ""] || "The reply failed."}
        </div>
      )}
      {m.status === "complete" && m.error_kind === "length" && (
        <div style={{ fontSize: 12, color: "var(--warn)", marginTop: 6 }}>
          {m.error_detail || "Cut off at the provider's Max tokens."}
        </div>
      )}
      {(m.model || tokens) && (
        <div className="chat-msg-meta">
          {m.model && <span className="mono">{m.model}</span>}
          {tokens && <span>{tokens} tokens</span>}
        </div>
      )}
    </>
  );
}
