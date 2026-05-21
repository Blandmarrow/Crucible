export const FLAG_OPTIONS = [
  { key: "is_blurry",     label: "Blurry" },
  { key: "is_noisy",      label: "Noisy" },
  { key: "is_uniform",    label: "Near-uniform" },
  { key: "has_watermark", label: "Watermarked" },
  { key: "is_duplicate",  label: "Duplicate" },
] as const;

export type FlagKey = (typeof FLAG_OPTIONS)[number]["key"];
