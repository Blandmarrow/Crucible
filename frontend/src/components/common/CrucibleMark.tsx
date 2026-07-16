/* The Crucible mark: a 5x5 contact sheet that curates down to a C.
 *
 * This is a transcription of the designer's exports in docs/images/ — keep the
 * two in step; `python scripts/check_mark.py` diffs them. The cm-keep and
 * cm-drop animation keyframes live in index.css. See docs/dev/frontend-core.md. */

// The cells forming the C. The rest are the unresolved grid: dropped by the
// animation, drawn faint when static.
const KEPT = new Set([
  "8,8", "18,8", "28,8", "38,8",
  "8,18",
  "8,28",
  "8,38",
  "8,48", "18,48", "28,48", "38,48",
]);

// The arms brighten toward the C's opening, so it reads as directional rather
// than a flat silhouette.
const BRIGHT = new Set(["28,8", "38,8", "28,48", "38,48"]);

// 8-unit cells on a 10-unit pitch, inset 8 in a 64-unit viewBox.
const COORDS = [8, 18, 28, 38, 48];

interface Props {
  size?: number;
  animated?: boolean;
  className?: string;
  // When set, the mark is announced as an image with this label. Both current
  // callsites are decorative (a "Crucible" wordmark or "Restarting…" text sits
  // right beside it), so the mark defaults to aria-hidden and only stands-alone
  // callers should pass a label.
  label?: string;
}

export default function CrucibleMark({ size = 22, animated = false, className, label }: Props) {
  const cells = [];
  for (const y of COORDS) {
    for (const x of COORDS) {
      const key = `${x},${y}`;
      const kept = KEPT.has(key);
      // Alternating A/B keyframes stagger the flicker so the grid reads as
      // many independent samples rather than one pulsing block.
      const variant = (COORDS.indexOf(x) + COORDS.indexOf(y)) % 2 === 0 ? "a" : "b";
      // Written out in full: Tailwind only keeps an @layer components rule whose
      // selector it finds verbatim in the source, so a `cm-keep-${variant}`
      // template would compile fine and then be purged from the bundle.
      const animClass = kept
        ? (variant === "a" ? "cm-keep-a" : "cm-keep-b")
        : (variant === "a" ? "cm-drop-a" : "cm-drop-b");
      cells.push(
        <rect
          key={key}
          className={animated ? animClass : undefined}
          x={x}
          y={y}
          width="8"
          height="8"
          rx="2"
          fill={kept ? (BRIGHT.has(key) ? "var(--accent-2)" : "var(--accent)") : "var(--accent-dim)"}
          // Static: the dropped cells sit at the animation's resolved opacity.
          opacity={kept || animated ? undefined : 0.09}
        />,
      );
    }
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      className={className}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {cells}
    </svg>
  );
}
