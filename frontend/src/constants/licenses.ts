// Mirrors backend/licenses.py — the backend is the authority on which ids exist
// and what each permits (GET /api/v1/licenses serves it); this file adds the
// UI-only concerns (badge color, display order). Keep the id list in sync.

export interface LicenseOption {
  id: string;
  label: string;
  allowsCommercial: boolean | null; // null = unknown / unverifiable
  requiresAttribution: boolean;
  shareAlike: boolean;
  url?: string;
  /** Tailwind classes for the badge — see index.css badge utilities. */
  badge: string;
}

export const LICENSE_OPTIONS: LicenseOption[] = [
  { id: "unknown", label: "Unknown", allowsCommercial: null, requiresAttribution: false, shareAlike: false, badge: "bg-gray-600/30 text-gray-300" },
  { id: "owned", label: "Owned / self-created", allowsCommercial: true, requiresAttribution: false, shareAlike: false, badge: "bg-green-600/30 text-green-300" },
  { id: "public-domain", label: "Public domain", allowsCommercial: true, requiresAttribution: false, shareAlike: false, badge: "bg-green-600/30 text-green-300" },
  { id: "CC0-1.0", label: "CC0 1.0 (no rights reserved)", allowsCommercial: true, requiresAttribution: false, shareAlike: false, url: "https://creativecommons.org/publicdomain/zero/1.0/", badge: "bg-green-600/30 text-green-300" },
  { id: "CC-BY-4.0", label: "CC BY 4.0", allowsCommercial: true, requiresAttribution: true, shareAlike: false, url: "https://creativecommons.org/licenses/by/4.0/", badge: "bg-blue-600/30 text-blue-300" },
  { id: "CC-BY-SA-4.0", label: "CC BY-SA 4.0", allowsCommercial: true, requiresAttribution: true, shareAlike: true, url: "https://creativecommons.org/licenses/by-sa/4.0/", badge: "bg-blue-600/30 text-blue-300" },
  { id: "CC-BY-NC-4.0", label: "CC BY-NC 4.0", allowsCommercial: false, requiresAttribution: true, shareAlike: false, url: "https://creativecommons.org/licenses/by-nc/4.0/", badge: "bg-amber-600/30 text-amber-300" },
  { id: "CC-BY-NC-SA-4.0", label: "CC BY-NC-SA 4.0", allowsCommercial: false, requiresAttribution: true, shareAlike: true, url: "https://creativecommons.org/licenses/by-nc-sa/4.0/", badge: "bg-amber-600/30 text-amber-300" },
  { id: "CC-BY-ND-4.0", label: "CC BY-ND 4.0", allowsCommercial: true, requiresAttribution: true, shareAlike: false, url: "https://creativecommons.org/licenses/by-nd/4.0/", badge: "bg-blue-600/30 text-blue-300" },
  { id: "licensed-commercial", label: "Licensed for commercial use", allowsCommercial: true, requiresAttribution: false, shareAlike: false, badge: "bg-green-600/30 text-green-300" },
  { id: "research-only", label: "Research / non-commercial only", allowsCommercial: false, requiresAttribution: true, shareAlike: false, badge: "bg-amber-600/30 text-amber-300" },
  { id: "synthetic", label: "Synthetic (AI-generated)", allowsCommercial: true, requiresAttribution: false, shareAlike: false, badge: "bg-purple-600/30 text-purple-300" },
];

export const OTHER_PREFIX = "other:";

const BY_ID = new Map(LICENSE_OPTIONS.map((l) => [l.id, l]));

/** Descriptor for a stored license value; unknown-shaped fallback for "" and `other:`. */
export function licenseInfo(value: string | null | undefined): LicenseOption {
  const v = (value ?? "").trim();
  if (!v) return { id: "", label: "No license", allowsCommercial: null, requiresAttribution: false, shareAlike: false, badge: "bg-gray-700/40 text-gray-400" };
  const known = BY_ID.get(v);
  if (known) return known;
  if (v.toLowerCase().startsWith(OTHER_PREFIX)) {
    return { id: v, label: v.slice(OTHER_PREFIX.length).trim() || "Other", allowsCommercial: null, requiresAttribution: false, shareAlike: false, badge: "bg-gray-600/30 text-gray-300" };
  }
  return BY_ID.get("unknown")!;
}

export function licenseLabel(value: string | null | undefined): string {
  return licenseInfo(value).label;
}

/**
 * Sentinel meaning "clear this field so the image inherits the dataset default".
 * Must match INHERIT_SENTINEL in backend/schemas/image.py — a bare "" cannot
 * distinguish "leave unchanged" from "clear to inherit".
 */
export const INHERIT_SENTINEL = "__inherit__";
