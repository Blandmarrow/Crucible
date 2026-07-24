import { useId, useState } from "react";
import { ScrollText } from "lucide-react";

import { isBlankLicense } from "../../constants/licenses";
import type { ProvenanceEdit } from "../../api/images";
import { useModalBehavior } from "../../hooks/useModalBehavior";
import LicenseSelect from "../common/LicenseSelect";

interface Props {
  count: number;
  isPending: boolean;
  onConfirm: (edit: ProvenanceEdit) => void;
  onClose: () => void;
  /** Rendered under the title when the selection spans multiple datasets. */
  sourceInfo?: React.ReactNode;
  /** Free-text licenses in use in the *current* dataset (hooks/useCustomLicenses).
   *  A selection can span datasets; `sourceInfo` is what says so. */
  customLicenses?: string[];
}

/** Per-field mode: only "set" and "inherit" send anything to the server. */
type Mode = "keep" | "set" | "inherit";

const MODES: Mode[] = ["keep", "set", "inherit"];

const MODE_LABEL: Record<Mode, string> = { keep: "Keep", set: "Set", inherit: "Inherit" };
const MODE_TITLE: Record<Mode, string> = {
  keep: "Leave this field as it is",
  set: "Set this field on every selected image",
  inherit: "Clear this field so it inherits the dataset default",
};

const TEXT_FIELDS = [
  { key: "source_name", label: "Source name", placeholder: "e.g. Danbooru, Unsplash, Client X" },
  { key: "source_url", label: "Source URL", placeholder: "https://…" },
  { key: "attribution", label: "Attribution", placeholder: "e.g. Photo by Jane Doe" },
] as const;

const BlankSetHint = () => (
  <p className="text-[10px] text-amber-400">
    Choose a value, or switch to <strong>Inherit</strong> to clear this field.
  </p>
);

/**
 * Keep/Set/Inherit segmented control for one field.
 *
 * Defined at module scope, not inside the modal body: a component declared during
 * render is a new type on every render, so React unmounts and remounts all four
 * toggle groups on each keystroke — which drops keyboard focus to `<body>`.
 */
function ModeToggle({
  field, label, mode, onSelect,
}: { field: string; label: string; mode: Mode; onSelect: (m: Mode) => void }) {
  return (
    <div className="flex gap-1 text-[10px]" role="group" aria-label={`${label} mode`}>
      {MODES.map((m) => (
        <button
          key={m}
          type="button"
          aria-pressed={mode === m}
          onClick={() => onSelect(m)}
          className={`px-1.5 py-0.5 rounded ${mode === m ? "bg-accent text-white" : "bg-surface-2 text-gray-400 hover:text-gray-200"}`}
          title={MODE_TITLE[m]}
          data-field={field}
        >
          {MODE_LABEL[m]}
        </button>
      ))}
    </div>
  );
}

export default function SetProvenanceModal({
  count, isPending, onConfirm, onClose, sourceInfo, customLicenses,
}: Props) {
  const uid = useId();
  const { overlayProps, panelProps } = useModalBehavior({ onClose, label: "Set source and license" });
  const [modes, setModes] = useState<Record<string, Mode>>({
    license: "keep", source_name: "keep", source_url: "keep", attribution: "keep",
  });
  const [values, setValues] = useState<Record<string, string>>({
    license: "", source_name: "", source_url: "", attribution: "",
  });

  const build = (): ProvenanceEdit => {
    const edit: ProvenanceEdit = {};
    for (const key of ["license", "source_name", "source_url", "attribution"] as const) {
      const mode = modes[key];
      if (mode === "keep") continue;         // omit → leave unchanged
      edit[key] = mode === "inherit" ? "" : values[key];   // "" → clear to inherit
    }
    return edit;
  };

  const nothingToDo = Object.values(modes).every((m) => m === "keep");
  // A blank "Set" would reach the backend as "" and be stored as NULL — i.e. it
  // would silently clear the field across the whole selection, which is what
  // "Inherit" is for. Block it rather than let a forgotten dropdown wipe data.
  // A pending `other:` with an empty body counts as blank too — it normalises
  // back to "" server-side, so "Set" would clear the license across the selection.
  const isBlankSet = (field: string) =>
    modes[field] === "set" &&
    (field === "license" ? isBlankLicense(values[field]) : !values[field].trim());
  const hasBlankSet = Object.keys(modes).some(isBlankSet);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" {...overlayProps}>
      <div className="card p-5 w-full max-w-md space-y-3 max-h-[90vh] overflow-auto" {...panelProps}>
        <h4 className="font-medium flex items-center gap-2">
          <ScrollText size={15} /> Set Source &amp; License — {count} Image{count !== 1 ? "s" : ""}
        </h4>
        {sourceInfo}

        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <label className="label !mb-0" htmlFor={`${uid}-license`}>License</label>
            <ModeToggle
              field="license"
              label="License"
              mode={modes.license}
              onSelect={(m) => setModes({ ...modes, license: m })}
            />
          </div>
          <LicenseSelect
            id={`${uid}-license`}
            value={values.license}
            onChange={(license) => setValues({ ...values, license })}
            emptyLabel="— choose —"
            disabled={modes.license !== "set"}
            className="select w-full disabled:opacity-40"
            inputClassName="input w-full disabled:opacity-40"
            customOptions={customLicenses}
          />
          {isBlankSet("license") && <BlankSetHint />}
        </div>

        {TEXT_FIELDS.map(({ key, label, placeholder }) => (
          <div key={key} className="space-y-1">
            <div className="flex items-center justify-between">
              <label className="label !mb-0" htmlFor={`${uid}-${key}`}>{label}</label>
              <ModeToggle
                field={key}
                label={label}
                mode={modes[key]}
                onSelect={(m) => setModes({ ...modes, [key]: m })}
              />
            </div>
            <input
              id={`${uid}-${key}`}
              value={values[key]}
              onChange={(e) => setValues({ ...values, [key]: e.target.value })}
              disabled={modes[key] !== "set"}
              placeholder={placeholder}
              className="input w-full disabled:opacity-40"
            />
            {isBlankSet(key) && <BlankSetHint />}
          </div>
        ))}

        <p className="text-xs text-gray-500">
          <strong>Keep</strong> leaves the field untouched. <strong>Inherit</strong> clears it so
          the image follows its dataset's default — editing that default then updates every
          non-overridden image.
        </p>

        <div className="flex justify-end gap-2 pt-1">
          <button className="btn btn-sm" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-primary btn-sm"
            disabled={isPending || nothingToDo || hasBlankSet}
            onClick={() => onConfirm(build())}
          >
            {isPending ? "Applying…" : "Apply"}
          </button>
        </div>
      </div>
    </div>
  );
}
