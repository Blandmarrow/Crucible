import type { HealthSummaryStored } from "../../types";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * A dataset's health at a glance, from `Dataset.health_summary` — the counts the
 * last health report stored. Shows the most severe level present; hidden when the
 * report has never been built, or found errors and warnings of neither kind
 * (info findings alone are not worth a pill on every card).
 *
 * The counts can lag: they are written when a report is built (opening the
 * Health page), and only when they change, so the tooltip says when they last did.
 */
export default function HealthBadge({ summary }: { summary: HealthSummaryStored | null }) {
  if (!summary || (summary.error === 0 && summary.warn === 0)) return null;
  const level = summary.error > 0 ? "bad" : "warn";
  const text = summary.error > 0 ? plural(summary.error, "error", "errors") : plural(summary.warn, "warning", "warnings");
  const when = new Date(summary.computed_at);
  const title = [
    `Health: ${plural(summary.error, "error", "errors")}, ${plural(summary.warn, "warning", "warnings")}, ${plural(summary.info, "note", "notes")}`,
    // Written only when the counts change, so this is when they last moved.
    Number.isNaN(when.getTime()) ? null : `last changed ${when.toLocaleString()}`,
    "Open the Health page for details.",
  ].filter(Boolean).join(" — ");
  return (
    <span className={`badge dot ${level}`} title={title} style={{ flexShrink: 0 }} data-testid="health-badge">
      {text}
    </span>
  );
}
