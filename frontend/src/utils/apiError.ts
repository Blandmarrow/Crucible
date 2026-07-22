/**
 * The server's own reason for rejecting a request, or `fallback`.
 *
 * FastAPI answers with two different `detail` shapes and the provenance forms hit
 * both: a plain string for an `HTTPException` (400/409), and an **array of
 * objects** for a Pydantic validation failure (422) — which is what an over-long
 * `source_url` or a license that grows past the column after normalization
 * produces. Rendering a fixed "Saving failed" toast for the second case hides the
 * one piece of information that tells the user which field to shorten.
 */
export function apiErrorDetail(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d) => {
        if (typeof d === "string") return d;
        const entry = d as { loc?: unknown[]; msg?: string };
        if (!entry?.msg) return "";
        // `loc` is ["body", "<field>"] — the trailing element is the field name.
        const field = Array.isArray(entry.loc) ? entry.loc[entry.loc.length - 1] : undefined;
        return typeof field === "string" && field !== "body"
          ? `${field}: ${entry.msg}`
          : entry.msg;
      })
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  return fallback;
}
