import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, Pencil, ScrollText, X } from "lucide-react";
import toast from "react-hot-toast";

import { imagesApi } from "../../api/images";
import { OTHER_PREFIX, isBlankLicense, licenseInfo } from "../../constants/licenses";
import { invalidateProvenanceScope } from "../../constants/queryKeys";
import { safeExternalUrl } from "../../utils/url";
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

/** The editable columns of an image, "" where the row is NULL (i.e. inheriting). */
function draftOf(image: ImageDetail) {
  return {
    source_name: image.source_name ?? "",
    source_url: image.source_url ?? "",
    license: image.license ?? "",
    attribution: image.attribution ?? "",
  };
}

export default function ProvenancePanel({ image }: Props) {
  const [open, setOpen] = useState(true);
  const [metaOpen, setMetaOpen] = useState(false);
  const qc = useQueryClient();

  const resolved = image.provenance;
  const inherited = new Set(resolved?.inherited ?? []);

  // The draft is seeded when the editor opens and never re-synced from props while
  // it is open. A sync effect here would discard whatever the user had typed every
  // time a background refetch produced a new `image` object (a job finishing, a
  // window focus) — and it is unnecessary, because the only moment the draft needs
  // to match the row is the moment editing starts.
  //
  // Editing is tracked by image id rather than a boolean so navigating to another
  // image closes the editor instead of showing the previous image's draft.
  const [editingId, setEditingId] = useState<string | null>(null);
  const editing = editingId === image.id;
  const [draft, setDraft] = useState(() => draftOf(image));

  const startEditing = () => {
    setDraft(draftOf(image));
    setEditingId(image.id);
  };
  const setEditing = (on: boolean) => setEditingId(on ? image.id : null);

  const save = useMutation({
    // An empty draft field means "clear the override so this field inherits" —
    // "" carries exactly that, and JSON null (an omitted key) means "unchanged".
    mutationFn: () =>
      imagesApi.setProvenance(image.id, {
        source_name: draft.source_name,
        source_url: draft.source_url,
        license: draft.license,
        attribution: draft.attribution,
      }),
    onSuccess: () => {
      setEditing(false);
      invalidateProvenanceScope(qc);
    },
    onError: () => toast.error("Saving source/license failed"),
  });

  // "Other (free text)…" picked but nothing typed. That sends a bare `other:`,
  // which normalises to "" server-side and *clears* the license — the opposite of
  // what picking a license means. Clearing stays available through the explicit
  // "Inherit from dataset" option, which is what it is for.
  const blankOther =
    draft.license.toLowerCase().startsWith(OTHER_PREFIX) && isBlankLicense(draft.license);

  const sourceMeta = resolved?.source_meta ?? image.source_meta;
  const hasAnyProvenance = ["license", "source_name", "source_url", "attribution"].some(
    (k) => resolved?.[k as keyof typeof resolved],
  );

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
                  onClick={startEditing}
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
                      {/* A source URL is scraped/EXIF data — only http(s) becomes
                          a link, anything else renders as inert text. */}
                      {key === "source_url" && safeExternalUrl(value) ? (
                        <a
                          href={safeExternalUrl(value)}
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

              {/* Only when *nothing* is recorded — the earlier check looked at
                  license/source_name alone and so printed "No source recorded"
                  directly above a populated Attribution or URL row. */}
              {!hasAnyProvenance && (
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
                  className="select w-full mt-0.5"
                  inputClassName="input w-full mt-0.5"
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
              {blankOther && (
                <p className="text-amber-400/90 text-[10px]">
                  Type the license name, or pick “Inherit from dataset” to clear it.
                </p>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => save.mutate()}
                  disabled={save.isPending || blankOther}
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
