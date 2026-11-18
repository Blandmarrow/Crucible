import type { FilterSpec } from "../api/health";

/**
 * A gallery deep link to "the images this filter selects" (M12): a `FilterSpec` plus
 * the label its chip shows. Carried as `?filter=<encoded>` in the routed gallery and
 * as `PaneView.filter` inside a pane, applied once on arrival and then consumed. See
 * `docs/dev/gallery/state.md` § The `?filter=` deep link.
 */
export interface FilterLink {
  label: string;
  spec: FilterSpec;
}

type Kind = "bool" | "int" | "num" | "str" | "flag" | "scoreField";

/** Every key a link may carry, and what its value must be. Closed: an unknown key or a
 *  wrong value rejects the whole link rather than half-applying it, since an ignored
 *  key would show more images than the link promised. Mirrors
 *  `backend/schemas/health.py::FilterSpec`. */
export const FILTER_SPEC_KINDS: Record<keyof FilterSpec, Kind> = {
  captioned: "bool",
  search: "str",
  caption_tokens_min: "int",
  caption_tokens_max: "int",
  short_side_max: "int",
  quality_flag: "flag",
  score_field: "scoreField",
  score_is_null: "bool",
  max_score_lt: "num",
  scores_stale: "bool",
  license_missing: "bool",
};

/** `utils.ALLOWED_FLAG_KEYS` and `routers/images.py::_ALLOWED_SCORE_FIELDS`: a value
 *  outside them is a 400 from `GET /images/`, which would leave the gallery reading
 *  "No images found" with the chip still on. Checked here so such a link is refused. */
export const QUALITY_FLAG_KEYS: readonly string[] = [
  "is_blurry", "is_noisy", "is_uniform", "has_watermark", "is_duplicate", "is_nsfw", "has_ai_artifacts", "gate_rejected",
];
const SCORE_FIELD_KEYS: readonly string[] = [
  "aesthetic_score", "blur_score", "noise_score", "uniformity_score", "watermark_score",
  "color_score", "saturation_score", "luminance_score", "style_similarity_score",
];

/** Keys that a toolbar control holds once a link is applied, so a chip never carries
 *  them (`license_missing: false` excepted: the licence select cannot say "has one"). */
const CONTROL_KEYS = new Set(["captioned", "search", "quality_flag"]);

const LABEL_MAX = 200;
// A bound well past any real count or pixel size, and inside SQLite's integer range.
const INT_MAX = 1_000_000_000;

function valid(kind: Kind, v: unknown): boolean {
  switch (kind) {
    case "bool": return typeof v === "boolean";
    case "int": return typeof v === "number" && Number.isInteger(v) && Math.abs(v) <= INT_MAX;
    case "num": return typeof v === "number" && Number.isFinite(v);
    case "str": return typeof v === "string" && v.length <= 500;
    case "flag": return typeof v === "string" && QUALITY_FLAG_KEYS.includes(v);
    case "scoreField": return typeof v === "string" && SCORE_FIELD_KEYS.includes(v);
  }
}

/** A link's spec and label, checked; null when anything about it is wrong.
 *
 *  `asChip` is for a chip read back from the `gallery-state-*` blob: it must not hold
 *  a key a toolbar control owns, since it is spread last into the request and would
 *  override the control silently. */
export function sanitizeFilterLink(raw: unknown, { asChip = false } = {}): FilterLink | null {
  if (!raw || typeof raw !== "object") return null;
  const { label, spec } = raw as { label?: unknown; spec?: unknown };
  if (typeof label !== "string" || !label.trim() || label.length > LABEL_MAX) return null;
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) return null;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(spec)) {
    if (v === null || v === undefined) continue;
    // Own keys only: `__proto__` or `constructor` must not find a kind on the prototype.
    if (!Object.hasOwn(FILTER_SPEC_KINDS, k)) return null;
    if (!valid(FILTER_SPEC_KINDS[k as keyof FilterSpec], v)) return null;
    if (asChip && (CONTROL_KEYS.has(k) || (k === "license_missing" && v === true))) return null;
    out[k] = v;
  }
  if (Object.keys(out).length === 0) return null;
  return { label, spec: out as FilterSpec };
}

// base64url over UTF-8, so a label in any script survives the URL.
function toB64Url(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromB64Url(s: string): string {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  const bin = atob(b64);
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

export function encodeFilterLink(link: FilterLink): string {
  const spec = Object.fromEntries(Object.entries(link.spec).filter(([, v]) => v !== null && v !== undefined));
  return toB64Url(JSON.stringify({ label: link.label, spec }));
}

export function decodeFilterLink(encoded: string): FilterLink | null {
  try {
    return sanitizeFilterLink(JSON.parse(fromB64Url(encoded)));
  } catch {
    return null;
  }
}
