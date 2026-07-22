import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { LICENSE_OPTIONS, licenseInfo } from "../../constants/licenses";
import type { DatasetProvenance } from "../../api/datasets";

interface Props {
  value: DatasetProvenance;
  onChange: (next: DatasetProvenance) => void;
  /** Start expanded — used when the values are already set and worth showing. */
  defaultOpen?: boolean;
  /** Explains what these values apply to; differs between create/edit and import. */
  note?: string;
}

export const EMPTY_PROVENANCE: DatasetProvenance = {
  source_name: "", source_url: "", license: "", attribution: "",
};

const TEXT_FIELDS = [
  { key: "source_name", label: "Source name", placeholder: "e.g. Danbooru, Unsplash, Client X" },
  { key: "source_url", label: "Source URL", placeholder: "https://…" },
  { key: "attribution", label: "Attribution", placeholder: "e.g. Photo by Jane Doe" },
] as const;

/**
 * Collapsible source/license inputs, shared by the dataset create/edit modals
 * and the folder-import dialog. All four fields are optional — an empty field
 * simply records nothing.
 */
export default function ProvenanceFields({ value, onChange, defaultOpen, note }: Props) {
  const hasAny = Object.values(value).some(Boolean);
  const [open, setOpen] = useState(defaultOpen ?? hasAny);

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200 transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Source &amp; license
        {!open && hasAny && (
          <span className="ml-1 text-gray-500">
            ({[licenseInfo(value.license).label, value.source_name].filter(Boolean).join(" · ")})
          </span>
        )}
      </button>

      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
          {note && <p className="text-xs text-gray-500">{note}</p>}
          <div>
            <label className="label">License</label>
            <select
              className="select"
              style={{ width: "100%" }}
              value={value.license}
              onChange={(e) => onChange({ ...value, license: e.target.value })}
            >
              <option value="">Not recorded</option>
              {LICENSE_OPTIONS.map((l) => (
                <option key={l.id} value={l.id}>{l.label}</option>
              ))}
              {/* Keep an existing other:<free text> value selectable. */}
              {value.license.startsWith("other:") && (
                <option value={value.license}>{licenseInfo(value.license).label}</option>
              )}
            </select>
          </div>
          {TEXT_FIELDS.map(({ key, label, placeholder }) => (
            <div key={key}>
              <label className="label">
                {label}{" "}
                <span style={{ fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 }}>(optional)</span>
              </label>
              <input
                className="input"
                placeholder={placeholder}
                value={value[key]}
                onChange={(e) => onChange({ ...value, [key]: e.target.value })}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
