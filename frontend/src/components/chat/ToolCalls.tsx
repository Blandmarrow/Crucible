import type { ToolCall } from "../../api/chats";

/** A running call, as the store keeps it until its result arrives. */
export interface RunningCall {
  call_id: string;
  name: string;
  arguments: string;
}

function parseArgs(raw: string): Record<string, unknown> {
  try {
    const v: unknown = JSON.parse(raw || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** The model's own text, as a short quote: it is shown, never trusted. */
function quoted(v: unknown): string | null {
  if (typeof v !== "string" || !v.trim()) return null;
  const s = v.trim();
  return s.length > 60 ? `${s.slice(0, 59)}…` : s;
}

/** A call in words: what the analyst looked up. An unknown tool shows its name. */
function describeCall(name: string, raw: string): string {
  const a = parseArgs(raw);
  switch (name) {
    case "list_snapshots":
      return "Listed the snapshots";
    case "get_finding": {
      const id = quoted(a.finding_id);
      return id ? `Read the finding ${id}` : "Read a finding";
    }
    case "sample_captions": {
      const id = quoted(a.finding_id);
      if (id) return `Read captions for ${id}`;
      return a.filter ? "Read captions matching a filter" : "Read sample captions";
    }
    case "tag_stats": {
      const text = quoted(a.contains);
      return text ? `Counted tags containing “${text}”` : "Counted tags";
    }
    case "compare_snapshot":
      return "Compared with a snapshot";
    default:
      return `Called ${quoted(name) ?? "a tool"}`;
  }
}

/** What a reply looked up, above its text (M12 phase 3, docs/dev/analyst/chat-ui.md
 *  § Tool calls). Each stored call opens to the exact text its result sent the model,
 *  so the transparency of "What gets sent" reaches the tools too. */
export default function ToolCalls({ calls, running }: { calls: ToolCall[]; running: RunningCall[] }) {
  if (!calls.length && !running.length) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, margin: "0 0 8px" }} data-testid="chat-tool-calls">
      {calls.map((c) => (
        <details key={c.id} data-testid="chat-tool-call" data-tool={c.name} data-status={c.status}>
          <summary style={{ cursor: "pointer", fontSize: 12, color: c.status === "error" ? "var(--warn)" : "var(--fg-mute)" }}>
            {describeCall(c.name, c.arguments)}
            {c.status === "error" ? " (failed)" : ""}
          </summary>
          <div style={{ fontSize: 11.5, color: "var(--fg-dim)", margin: "4px 0 2px" }}>
            Sent to the model: {c.chars.toLocaleString()} characters
            {c.categories?.length ? `, carrying ${c.categories.join(", ").replace(/_/g, " ")}` : ""}.
          </div>
          <pre className="mono" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 11.5, margin: 0, padding: "6px 8px", background: "var(--surface-2)", borderRadius: 6, color: "var(--fg)", maxHeight: 260, overflowY: "auto" }}>
            {c.result_text}
          </pre>
        </details>
      ))}
      {running.map((r, i) => (
        <div key={`running-${i}-${r.call_id}`} style={{ fontSize: 12, color: "var(--fg-mute)" }} data-testid="chat-tool-running">
          {describeCall(r.name, r.arguments)}…
        </div>
      ))}
    </div>
  );
}
