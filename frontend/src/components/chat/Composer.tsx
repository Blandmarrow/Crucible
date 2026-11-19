import { useId, useState } from "react";
import type { Budget } from "../../api/chats";

/** The message box: Enter sends, Shift+Enter starts a new line. While a reply is
 *  being written, Send becomes Stop. The line under it is the budget for a billed
 *  provider, or the token estimate for a free one. */
export default function Composer({ draft, onDraft, onSend, onStop, writing, stopping, canStop, canSend, blockedReason, budget, estimate }: {
  draft: string;
  onDraft: (v: string) => void;
  /** Resolves true when the message was accepted, so the box can clear. */
  onSend: (text: string) => Promise<boolean>;
  onStop: () => void;
  writing: boolean;
  stopping: boolean;
  /** False until the reply has a row to stop (its `start` event). */
  canStop: boolean;
  canSend: boolean;
  /** Why Send is off, shown in place of the budget line. */
  blockedReason: string | null;
  budget: Budget | undefined;
  estimate: number | undefined;
}) {
  const id = useId();
  const [sending, setSending] = useState(false);
  const empty = !draft.trim();
  const overBudget = !!budget?.would_exceed;
  const disabled = writing || sending || empty || !canSend || overBudget;

  const send = async () => {
    if (disabled) return;
    setSending(true);
    try {
      if (await onSend(draft)) onDraft("");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="chat-composer">
      <label htmlFor={id} className="sr-only">Message</label>
      <textarea
        id={id}
        className="input"
        rows={3}
        maxLength={20_000}
        placeholder="Ask about this dataset…"
        value={draft}
        onChange={(e) => onDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            void send();
          }
        }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <BudgetLine budget={budget} estimate={estimate} blockedReason={blockedReason} />
        {writing ? (
          <button className="btn" onClick={onStop} disabled={stopping || !canStop} style={{ marginLeft: "auto" }}>
            {stopping ? "Stopping…" : "Stop"}
          </button>
        ) : (
          <button className="btn primary" onClick={() => void send()} disabled={disabled} style={{ marginLeft: "auto" }}>
            Send
          </button>
        )}
      </div>
    </div>
  );
}

function BudgetLine({ budget, estimate, blockedReason }: { budget: Budget | undefined; estimate: number | undefined; blockedReason: string | null }) {
  const style = { fontSize: 11.5, color: "var(--fg-dim)", minWidth: 0 } as const;
  if (blockedReason) return <span style={{ ...style, color: "var(--warn)" }}>{blockedReason}</span>;
  if (!budget) return <span style={style}>{estimate ? `About ${estimate.toLocaleString()} tokens of context` : ""}</span>;
  if (!budget.billed) {
    return <span style={style}>About {(estimate ?? 0).toLocaleString()} tokens of context · a free provider, so no budget applies</span>;
  }
  const line = `Up to ${budget.estimate.toLocaleString()} tokens · this chat ${budget.chat_used.toLocaleString()} of ${budget.chat_budget.toLocaleString()} · today ${budget.day_used.toLocaleString()} of ${budget.day_budget.toLocaleString()}`;
  if (budget.would_exceed) {
    return (
      <span role="alert" style={{ ...style, color: "var(--bad)" }} data-testid="chat-over-budget">
        This send could pass a budget ({line}). Raise it in Settings → Analyst, or start a new chat.
      </span>
    );
  }
  return <span style={style} title="A billed provider: the most this send could use, and where the budgets stand">{line}</span>;
}
