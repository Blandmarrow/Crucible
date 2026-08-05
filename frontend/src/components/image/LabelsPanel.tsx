import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Plus, X } from "lucide-react";

import { labelsApi } from "../../api/labels";
import { invalidateLabelScope } from "../../constants/queryKeys";
import { useLabels } from "../../hooks/useLabels";
import { apiErrorDetail } from "../../utils/apiError";

interface Props {
  imageId: string;
  datasetId: string;
  /** Label ids currently on this image, from `GET /images/{id}`. */
  labelIds: string[];
}

/**
 * The image detail view's label block: what is attached, and a row to attach more.
 *
 * Mounted with `key={image.id}` by the page, like `ProvenancePanel`, so the
 * "add" popover never leaks a pending choice across an arrow-key navigation.
 *
 * There is no free-text input here on purpose — the vocabulary is managed in
 * Settings, so this panel only ever offers labels that already exist.
 */
export default function LabelsPanel({ imageId, datasetId, labelIds }: Props) {
  const qc = useQueryClient();
  const { labels, byId } = useLabels();
  const [adding, setAdding] = useState(false);

  const assign = useMutation({
    mutationFn: (body: { add?: string[]; remove?: string[] }) =>
      labelsApi.assign({ image_ids: [imageId], ...body }),
    onSuccess: () => invalidateLabelScope(qc, datasetId),
    onError: (err) => toast.error(apiErrorDetail(err, "Could not update labels")),
  });

  const attached = labelIds.map((id) => byId.get(id)).filter((l) => l !== undefined);
  const available = labels.filter((l) => !labelIds.includes(l.id));

  return (
    <div className="p-4 border-t border-gray-800 space-y-2" role="group" aria-label="Labels">
      <h3 className="font-medium text-sm text-gray-300 uppercase tracking-wide">Labels</h3>

      {labels.length === 0 && (
        <p className="text-xs text-gray-500">
          No labels defined yet — create them in Settings → Labels.
        </p>
      )}

      {labels.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {attached.map((label) => (
            <span
              key={label.id}
              className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs"
              style={{ background: `${label.color}22`, border: `1px solid ${label.color}` }}
            >
              <span
                aria-hidden
                style={{ width: 7, height: 7, borderRadius: "50%", background: label.color }}
              />
              {label.name}
              {label.hotkey && <span className="text-gray-500">·{label.hotkey}</span>}
              <button
                type="button"
                aria-label={`Remove label ${label.name}`}
                disabled={assign.isPending}
                onClick={() => assign.mutate({ remove: [label.id] })}
                className="opacity-60 hover:opacity-100"
              >
                <X size={11} />
              </button>
            </span>
          ))}

          {attached.length === 0 && (
            <span className="text-xs text-gray-500">None</span>
          )}

          {available.length > 0 && (
            <button
              type="button"
              className="btn-ghost btn-sm"
              aria-label="Add label"
              aria-expanded={adding}
              onClick={() => setAdding((a) => !a)}
            >
              <Plus size={12} />
            </button>
          )}
        </div>
      )}

      {adding && available.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {available.map((label) => (
            <button
              key={label.id}
              type="button"
              className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs border border-gray-700 hover:border-gray-500"
              disabled={assign.isPending}
              onClick={() => {
                setAdding(false);
                assign.mutate({ add: [label.id] });
              }}
            >
              <span
                aria-hidden
                style={{ width: 7, height: 7, borderRadius: "50%", background: label.color }}
              />
              {label.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
