import { useId, useState } from "react";
import ModalShell from "../common/ModalShell";
import {
  PRIVACY_CATEGORIES,
  PRIVACY_LABELS,
  type Chat,
  type ContextPreview,
  type Privacy,
  type PrivacyCategory,
} from "../../api/chats";

/** What this chat may send, and exactly what the next send would send. The preview is
 *  `POST /chats/{id}/preview-context`, which calls the same `build_context` as a real
 *  send, so what is shown here cannot drift from what goes out. */
export default function ContextDialog({ chat, preview, loading, busy, providerPrivacy, providerName, onPrivacy, onClose }: {
  chat: Chat;
  preview: ContextPreview | undefined;
  loading: boolean;
  busy: boolean;
  /** What the provider allows, for showing a pending return to its defaults. */
  providerPrivacy: Record<string, boolean> | null;
  providerName: string | null;
  /** A new override for the chat, or null to follow the provider again; `done` runs
   *  once the save has settled, whatever its outcome. */
  onPrivacy: (p: Privacy | null, done: () => void) => void;
  onClose: () => void;
}) {
  const uid = useId();
  // The choice being saved (null: back to the provider's), shown from the click until
  // the save and its refetches land. State, not the mutation's variables: React puts
  // a controlled checkbox back at the end of the click, before those arrive.
  const [pendingPrivacy, setPending] = useState<Privacy | null | undefined>(undefined);
  const choose = (p: Privacy | null) => {
    setPending(p);
    onPrivacy(p, () => setPending(undefined));
  };
  const effective: Partial<Record<string, boolean>> | undefined =
    pendingPrivacy === undefined ? preview?.privacy : pendingPrivacy ?? providerPrivacy ?? undefined;
  const source = pendingPrivacy === undefined ? preview?.manifest.privacy_source : pendingPrivacy ? "chat" : "provider";

  const toggle = (cat: PrivacyCategory, on: boolean) => {
    if (!effective) return;
    // The whole set, so the chat's choice stays as shown even if the provider's
    // defaults change later. `images` is reserved and left out.
    const next: Privacy = {};
    for (const c of PRIVACY_CATEGORIES) if (c !== "images") next[c] = c === cat ? on : !!effective[c];
    choose(next);
  };

  return (
    <ModalShell label="What gets sent" onClose={onClose} closeOnBackdrop style={{ width: "min(880px, 94vw)", maxHeight: "88vh", display: "flex", flexDirection: "column" }}>
      <h3>What gets sent</h3>
      <p style={{ margin: "0 0 12px" }}>
        {providerName ? <>Sent to <strong>{providerName}</strong>. </> : null}
        {source === "chat"
          ? "This chat has its own choice."
          : "This chat follows the provider's defaults (Settings → Analyst)."}{" "}
        Tags, captions and file names are marked as data, never as instructions.
      </p>

      <fieldset className="cmp-fieldset" disabled={busy || !effective} style={{ marginBottom: 12 }}>
        <legend>This chat may send</legend>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: "6px 16px" }}>
          {PRIVACY_CATEGORIES.map((c) => {
            const m = preview?.manifest.categories[c];
            const reserved = c === "images";
            return (
              <label key={c} htmlFor={`${uid}-${c}`} style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12.5, color: reserved ? "var(--fg-dim)" : "var(--fg)" }}>
                <input
                  id={`${uid}-${c}`}
                  type="checkbox"
                  checked={!!effective?.[c]}
                  disabled={reserved}
                  onChange={(e) => toggle(c, e.target.checked)}
                  style={{ marginTop: 2 }}
                />
                <span>
                  {PRIVACY_LABELS[c].label}
                  <span style={{ display: "block", fontSize: 11, color: "var(--fg-dim)" }}>
                    {PRIVACY_LABELS[c].help}
                    {m?.sent ? ` · ${m.items.toLocaleString()} item${m.items === 1 ? "" : "s"}, ${m.chars.toLocaleString()} chars` : ""}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
        {source === "chat" && (
          <button className="btn sm ghost" style={{ marginTop: 8, alignSelf: "flex-start" }} onClick={() => choose(null)}>
            Follow the provider's defaults again
          </button>
        )}
      </fieldset>

      {preview && (
        <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 8px" }} data-testid="context-tools">
          {preview.manifest.tools_offered?.length ? (
            <>
              Tools it may call: <span className="mono">{preview.manifest.tools_offered.join(", ")}</span>. A
              tool's result is held to the boxes above too, and shown under the reply that asked for it.
            </>
          ) : (
            <>
              No tools: this model has not passed “Check tool support” (Settings → Analyst), or the boxes
              above allow none.
            </>
          )}
        </p>
      )}

      {preview && (
        <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 8px" }}>
          About {preview.manifest.estimated_tokens.toLocaleString()} tokens ({preview.manifest.total_chars.toLocaleString()} characters).
          History: {preview.manifest.history_messages} message{preview.manifest.history_messages === 1 ? "" : "s"}
          {preview.manifest.history_dropped ? `, ${preview.manifest.history_dropped} older left out for length` : ""}
          {preview.manifest.history_withheld
            ? `, ${preview.manifest.history_withheld} earlier repl${preview.manifest.history_withheld === 1 ? "y" : "ies"} withheld because they saw something now off`
            : ""}
          .
        </p>
      )}

      <div style={{ overflowY: "auto", flex: 1, minHeight: 120, display: "flex", flexDirection: "column", gap: 8 }} data-testid="context-preview">
        {loading && !preview && <div style={{ fontSize: 12.5, color: "var(--fg-mute)" }}>Building the context…</div>}
        {preview?.messages.map((m, i) => (
          <details key={i} open={m.role === "system"} className="card" style={{ padding: "6px 10px" }}>
            <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--fg-mute)" }}>
              {m.role === "system" ? "System message and context" : m.role === "user" ? "You" : "Analyst"} · {m.content.length.toLocaleString()} chars
            </summary>
            <pre className="mono" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 11.5, margin: "6px 0 0", color: "var(--fg)" }}>{m.content}</pre>
          </details>
        ))}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <span style={{ marginRight: "auto", fontSize: 11.5, color: "var(--fg-dim)", alignSelf: "center" }}>
          Chat {chat.title ? `“${chat.title}”` : "untitled"}
        </span>
        <button className="btn" onClick={onClose}>Close</button>
      </div>
    </ModalShell>
  );
}
