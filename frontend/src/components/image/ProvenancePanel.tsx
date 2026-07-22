import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, Pencil, ScrollText, X } from "lucide-react";
import toast from "react-hot-toast";

import { imagesApi } from "../../api/images";
import { INHERIT_SENTINEL, licenseInfo } from "../../constants/licenses";
import type { ImageDetail } from "../../types";
import LicenseSelect from "../common/LicenseSelect";

interface Props {
  image: ImageDetail;
}

/** Small colored pill for an effective license value. */
export function LicenseBadge({ value, title }: { value: string | null | undefined; title?: string }) {
  const info = licenseInfo(value);
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${info.badge}`} title={title ?? info.label}>
      {info.label}
    </span>
  );
}

const FIELDS = [
  { key: "source_name", label: "Source" },
  { key: "source_url", label: "URL" },
  { key: "attribution", label: "Attribution" },
] as const;

export default function ProvenancePanel({ image }: Props) {
  const [open, setOpen] = useState(true);
  const [editing, setEditing] = useState(false);
  const [metaOpen, setMetaOpen] = useState(false);
  const qc = useQueryClient();

  const resolved = image.provenance;
  const inherited = new Set(resolved?.inherited ?? []);

  // Draft holds raw values: "" means "inherit", matching how the row is stored.
  const [draft, setDraft] = useState({
    source_name: image.source_name ?? "",
    source_url: image.source_url ?? "",
    license: image.license ?? "",
    attribution: image.attribution ?? "",
  });

  useEffect(() => {
    setDraft({
      source_name: image.source_name ?? "",
      source_url: image.source_url ?? "",
      license: image.license ?? "",
      attribution: image.attribution ?? "",
    });
  }, [image.id, image.source_name, image.source_url, image.license, image.attribution]);

  const save = useMutation({
    // An empty draft field means "clear the override" — send the sentinel, not
    // "", so the backend can tell it apart from "leave unchanged".
    mutationFn: () =>
      imagesApi.setProvenance(image.id, {
        source_name: draft.source_name || INHERIT_SENTINEL,
        source_url: draft.source_url || INHERIT_SENTINEL,
        license: draft.license || INHERIT_SENTINEL,
        attribution: draft.attribution || INHERIT_SENTINEL,
      }),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["image", image.id] });
      qc.invalidateQueries({ queryKey: ["images"] });
      qc.invalidateQueries({ queryKey: ["dataset-stats"] });
    },
    onError: () => toast.error("Saving source/license failed"),
  });

  const sourceMeta = resolved?.source_meta ?? image.source_meta;

  return (
    <div className="border-t border-gray-700/50 mt-2 pt-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 w-full text-left text-xs font-medium text-gray-300 uppercase tracking-wide hover:text-white transition-colors"
      >
        <ScrollText size={12} className="text-accent" />
        SOURCE &amp; LICENSE
        <span className="ml-auto">{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 text-xs">
          {!editing && (
            <>
              <div className="flex items-center gap-2">
                <LicenseBadge value={resolved?.license ?? image.license} />
                {inherited.has("license") && (
                  <span className="text-gray-500 text-[10px]" title="No license set on this image; showing the dataset default">
                    inherited from dataset
                  </span>
                )}
                <button
                  onClick={() => setEditing(true)}
                  className="icon-btn ml-auto"
                  title="Edit source & license"
                  style={{ width: 20, height: 20 }}
                >
                  <Pencil size={12} />
                </button>
              </div>

              <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                {FIELDS.map(({ key, label }) => {
                  const value = resolved?.[key] ?? "";
                  if (!value) return null;
                  return (
                    <div key={key} className="contents">
                      <span className="text-gray-500">
                        {label}
                        {inherited.has(key) && <span className="text-gray-600"> (inherited)</span>}
                      </span>
                      {key === "source_url" ? (
                        <a
                          href={value}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-accent hover:underline truncate flex items-center gap-1"
                          title={value}
                        >
                          <span className="truncate">{value}</span>
                          <ExternalLink size={10} className="shrink-0" />
                        </a>
                      ) : (
                        <span className="truncate" title={value}>{value}</span>
                      )}
                    </div>
                  );
                })}
              </div>

              {!resolved?.license && !resolved?.source_name && (
                <p className="text-gray-500">
                  No source recorded. Set a dataset default, or edit this image.
                </p>
              )}
            </>
          )}

          {editing && (
            <div className="space-y-2">
              <label className="block">
                <span className="text-gray-500">License</span>
                <LicenseSelect
                  value={draft.license}
                  onChange={(license) => setDraft({ ...draft, license })}
                  emptyLabel="Inherit from dataset"
                  className="input w-full mt-0.5"
                />
              </label>
              {FIELDS.map(({ key, label }) => (
                <label key={key} className="block">
                  <span className="text-gray-500">{label}</span>
                  <input
                    value={draft[key]}
                    onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                    placeholder={resolved?.[key] ? `Inherited: ${resolved[key]}` : "Inherit from dataset"}
                    className="input w-full mt-0.5"
                  />
                </label>
              ))}
              <p className="text-gray-500 text-[10px]">
                Leave a field empty to inherit the dataset default.
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => save.mutate()}
                  disabled={save.isPending}
                  className="btn btn-primary btn-sm"
                >
                  {save.isPending ? "Saving…" : "Save"}
                </button>
                <button onClick={() => setEditing(false)} className="btn btn-sm">
                  <X size={12} /> Cancel
                </button>
              </div>
            </div>
          )}

          {sourceMeta && Object.keys(sourceMeta).length > 0 && (
            <div>
              <button
                onClick={() => setMetaOpen(!metaOpen)}
                className="flex items-center gap-1 text-gray-500 hover:text-gray-300 transition-colors"
              >
                {metaOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                Scrape metadata
              </button>
              {metaOpen && (
                <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 bg-surface-2 rounded p-2 max-h-48 overflow-auto">
                  {Object.entries(sourceMeta).map(([k, v]) => (
                    <div key={k} className="contents">
                      <span className="text-gray-500 truncate" title={k}>{k}</span>
                      <span className="text-gray-300 break-words">
                        {typeof v === "object" ? JSON.stringify(v) : String(v)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
