/**
 * The gallery selection checkbox, rendered at a user-configurable size.
 *
 * Every dimension is derived from `size` so the box scales as a unit rather than
 * growing a container around a fixed-size tick. Those formulas live here and are
 * deliberately not exported: the Settings preview renders this same component, so
 * it cannot drift from what the gallery actually draws.
 *
 * Size comes from `uiPrefsStore.galleryCheckboxSize`; callers pass it in rather
 * than reading the store here, so this stays a pure presentational component.
 */
interface Props {
  /** Edge length in px (see GALLERY_CHECKBOX_SIZE_MIN/MAX in constants/storage.ts). */
  size: number;
  selected: boolean;
}

export default function GalleryCheckbox({ size, selected }: Props) {
  return (
    <div style={{
      width: size, height: size,
      background: selected ? "var(--accent)" : "rgba(7,9,11,.55)",
      border: `${size >= 26 ? 2 : 1.5}px solid ${selected ? "var(--accent)" : "rgba(255,255,255,.5)"}`,
      borderRadius: Math.max(4, Math.round(size / 4.5)),
      display: "grid", placeContent: "center",
      backdropFilter: "blur(4px)",
      flexShrink: 0,
    }}>
      {selected && (
        <svg viewBox="0 0 12 10" width={Math.round(size / 2)} height={Math.round(size / 2)} fill="none">
          <path d="M1 5l3 4L11 1" stroke="#03130d" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </div>
  );
}
