import { licenseInfo } from "../../constants/licenses";

/**
 * Small colored pill for an effective license value.
 *
 * The single source of the badge's shape. Four call sites (gallery card, dataset
 * card, Stats licenses table, image detail panel) each rendered this by hand and
 * had already drifted apart on padding and font size. `className` carries only
 * per-site layout concerns — sizing belongs here.
 */
export default function LicenseBadge({
  value,
  title,
  className = "",
}: {
  value: string | null | undefined;
  title?: string;
  className?: string;
}) {
  const info = licenseInfo(value);
  return (
    <span
      className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${info.badge} ${className}`}
      title={title ?? info.label}
    >
      {info.label}
    </span>
  );
}
