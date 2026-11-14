// One colour per detection label, shared by the overlay drawn on the image
// (ImageDetailPage) and the chips that list the same labels (DetectionsPanel),
// so a label's box and its chip always match. The same in both themes: the
// boxes sit on the image, and the chips tint these behind theme-coloured text.
export const BBOX_COLORS = ["#f87171","#fb923c","#facc15","#4ade80","#34d399","#22d3ee","#818cf8","#c084fc","#f472b6","#94a3b8"];

/** A stable colour for a label: the same label always hashes to the same entry. */
export function labelColor(label: string): string {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) & 0xffffffff;
  return BBOX_COLORS[Math.abs(h) % BBOX_COLORS.length];
}
