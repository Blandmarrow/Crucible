import type { Finding } from "../../api/health";
import { SEVERITY_CLASS, SEVERITY_LABEL } from "./severity";

const UNIT_LABEL: Record<Finding["unit"], [string, string]> = {
  images: ["image", "images"],
  tags: ["tag", "tags"],
  groups: ["group", "groups"],
};

/** One finding as a card, shared by the Health page and the analyst chat's strip. Its
 *  buttons come from the finding's own data, never from the page: "Show images" and
 *  "Open in gallery" only with a `filter_spec`, the fix button only with a `fix`
 *  (`docs/dev/analyst/ui.md` § The finding row). `compact` drops the evidence line,
 *  for the chat, where the reply already explains it. */
export default function FindingCard({ finding: f, onShow, onOpenGallery, onFix, onDismiss, compact = false, testId }: {
  finding: Finding;
  onShow: () => void;
  onOpenGallery: () => void;
  onFix: () => void;
  /** Absent: no Dismiss button. */
  onDismiss?: () => void;
  compact?: boolean;
  testId?: string;
}) {
  const [one, many] = UNIT_LABEL[f.unit];
  const delta = f.was === null ? null : f.count - f.was;
  return (
    <li
      className="card"
      data-testid={testId ?? `finding-${f.id}`}
      style={{ padding: compact ? "8px 12px" : "10px 14px", display: "flex", flexDirection: "column", gap: 6 }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span className={`badge dot ${SEVERITY_CLASS[f.severity]}`}>{SEVERITY_LABEL[f.severity]}</span>
        <strong style={{ fontSize: compact ? 13 : 13.5 }}>{f.title}</strong>
        {f.total > 0 && (
          <span className="mono" style={{ fontSize: 12, color: "var(--fg-mute)" }}>
            {f.count.toLocaleString()} of {f.total.toLocaleString()} {f.total === 1 ? one : many}
          </span>
        )}
        {delta !== null && delta !== 0 && (
          <span style={{ fontSize: 11.5, color: delta > 0 ? "var(--bad)" : "var(--good)" }} title="Against the snapshot">
            was {f.was!.toLocaleString()}
          </span>
        )}
        {f.dismissal && compact && (
          <span style={{ fontSize: 11.5, color: "var(--fg-dim)" }}>dismissed</span>
        )}
        <span style={{ marginLeft: "auto", display: "flex", gap: 6, flexWrap: "wrap" }}>
          {f.filter_spec && (
            <>
              <button className="btn sm" onClick={onShow} aria-label={`Show images: ${f.title}`}>Show images</button>
              <button className="btn sm ghost" onClick={onOpenGallery} aria-label={`Open in gallery: ${f.title}`}>Open in gallery</button>
            </>
          )}
          {f.fix && <button className="btn sm" onClick={onFix}>{f.fix.label}</button>}
          {onDismiss && (
            <button className="btn sm ghost" onClick={onDismiss} aria-label={`Dismiss: ${f.title}`}>Dismiss…</button>
          )}
        </span>
      </div>
      {!compact && f.resurfaced && f.dismissal && (
        <div style={{ fontSize: 12, color: "var(--warn)" }}>
          Back after a dismissal (“{f.dismissal.reason}”): it was {f.dismissal.count_then ?? "?"} then and has more than doubled.
        </div>
      )}
      {!compact && <Numbers numbers={f.numbers} />}
    </li>
  );
}

const HUMAN_KEYS: Record<string, string> = {
  min_images: "minimum",
  under_tokens: "under (tokens)",
  token_limit: "token limit",
  target_short_side: "target short side (px)",
  target_from: "target from",
  smallest_short_side: "smallest short side (px)",
  bucket_share_under: "bucket share under",
  buckets_used: "buckets used",
  duplicate_copies: "copies",
  below_score: "below score",
  iqr_k: "IQR factor",
  tag_style: "tag-style",
};

function formatValue(v: unknown): string {
  if (Array.isArray(v)) {
    return v
      .map((x) => (x && typeof x === "object" && "tag" in x ? `${(x as { tag: string }).tag} (${(x as { count: number }).count})` : String(x)))
      .join(", ");
  }
  if (v && typeof v === "object") return Object.entries(v).map(([k, n]) => `${k}: ${n}`).join(", ");
  return String(v);
}

/** The evidence behind a finding: its thresholds and the values found. A `hint`
 *  is shown as plain guidance rather than a value. */
function Numbers({ numbers }: { numbers: Record<string, unknown> }) {
  const entries = Object.entries(numbers).filter(([k, v]) => k !== "hint" && v !== null && v !== undefined);
  const hint = typeof numbers.hint === "string" ? numbers.hint : null;
  if (!entries.length && !hint) return null;
  return (
    <div style={{ fontSize: 11.5, color: "var(--fg-dim)", display: "flex", gap: 14, flexWrap: "wrap" }}>
      {entries.map(([k, v]) => (
        <span key={k}>
          <span style={{ color: "var(--fg-mute)" }}>{HUMAN_KEYS[k] ?? k.replace(/_/g, " ")}:</span> {formatValue(v)}
        </span>
      ))}
      {hint && <em>{hint}</em>}
    </div>
  );
}
