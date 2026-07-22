import { useState } from "react";
import { ScrollText } from "lucide-react";

import { INHERIT_SENTINEL } from "../../constants/licenses";
import type { ProvenanceEdit } from "../../api/images";
import LicenseSelect, { isBlankLicense } from "../common/LicenseSelect";

interface Props {
  count: number;
  isPending: boolean;
  onConfirm: (edit: ProvenanceEdit) => void;
  onClose: () => void;
  /** Rendered under the title when the selection spans multiple datasets. */
  sourceInfo?: React.ReactNode;
}

/** Per-field mode: only "set" and "inherit" send anything to the server. */
type Mode = "keep" | "set" | "inherit";

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

export default function SetProvenanceModal({ count, isPending, onConfirm, onClose, sourceInfo }: Props) {
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
      if (mode === "keep") continue;                       // omit → leave unchanged
      edit[key] = mode === "inherit" ? INHERIT_SENTINEL : values[key];
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

  const ModeToggle = ({ field }: { field: string }) => (
    <div className="flex gap-1 text-[10px]">
      {(["keep", "set", "inherit"] as Mode[]).map((m) => (
        <button
          key={m}
          onClick={() => setModes({ ...modes, [field]: m })}
          className={`px-1.5 py-0.5 rounded ${modes[field] === m ? "bg-accent text-white" : "bg-surface-2 text-gray-400 hover:text-gray-200"}`}
          title={
            m === "keep" ? "Leave this field as it is"
              : m === "set" ? "Set this field on every selected image"
              : "Clear this field so it inherits the dataset default"
          }
        >
          {m === "keep" ? "Keep" : m === "set" ? "Set" : "Inherit"}
        </button>
      ))}
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card p-5 w-full max-w-md space-y-3 max-h-[90vh] overflow-auto">
        <h4 className="font-medium flex items-center gap-2">
          <ScrollText size={15} /> Set Source &amp; License — {count} Image{count !== 1 ? "s" : ""}
        </h4>
        {sourceInfo}

        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <label className="label !mb-0">License</label>
            <ModeToggle field="license" />
          </div>
          <LicenseSelect
            value={values.license}
            onChange={(license) => setValues({ ...values, license })}
            emptyLabel="— choose —"
            disabled={modes.license !== "set"}
            className="input w-full disabled:opacity-40"
          />
          {isBlankSet("license") && <BlankSetHint />}
        </div>

        {TEXT_FIELDS.map(({ key, label, placeholder }) => (
          <div key={key} className="space-y-1">
            <div className="flex items-center justify-between">
              <label className="label !mb-0">{label}</label>
              <ModeToggle field={key} />
            </div>
            <input
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
