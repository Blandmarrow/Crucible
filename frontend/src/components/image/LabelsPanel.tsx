import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { X } from "lucide-react";

import { labelsApi } from "../../api/labels";
import LabelPicker from "../common/LabelPicker";
import { invalidateLabelScope } from "../../constants/queryKeys";
import { useLabels } from "../../hooks/useLabels";
import { apiErrorDetail } from "../../utils/apiError";

interface Props {
  imageId: string;
  /** Label ids currently on this image, from `GET /images/{id}`. */
  labelIds: string[];
}

/**
 * The image detail view's label block: what is attached, and a picker to change it.
 *
 * Mounted with `key={image.id}` by the page, like `ProvenancePanel`, so the
 * picker never leaks a pending choice across an arrow-key navigation.
 *
 * There is no free-text input here on purpose — the vocabulary is managed in
 * Settings, so this panel only ever offers labels that already exist.
 *
 * The picker is `placement="inline"`: the right rail it sits in is
 * `overflow-y: auto`, which would clip an absolutely positioned panel at the
 * rail's bottom edge. It also stays open across a toggle, so attaching four
 * labels is four clicks rather than four round trips through the trigger.
 */
export default function LabelsPanel({ imageId, labelIds }: Props) {
  const qc = useQueryClient();
  const { labels, byId } = useLabels();

  const assign = useMutation({
    mutationFn: (body: { add?: string[]; remove?: string[] }) =>
      labelsApi.assign({ image_ids: [imageId], ...body }),
    onSuccess: () => invalidateLabelScope(qc),
    onError: (err) => toast.error(apiErrorDetail(err, "Could not update labels")),
  });

  const attached = labelIds.map((id) => byId.get(id)).filter((l) => l !== undefined);

  return (
    <div className="p-4 space-y-2" style={{ borderTop: "1px solid var(--line)" }} role="group" aria-label="Labels">
      <h3 className="font-medium text-sm uppercase tracking-wide" style={{ color: "var(--fg-dim)" }}>Labels</h3>

      {labels.length === 0 && (
        <p className="text-xs" style={{ color: "var(--fg-mute)" }}>
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
              {label.hotkey && <span style={{ color: "var(--fg-mute)" }}>·{label.hotkey}</span>}
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
            <span className="text-xs" style={{ color: "var(--fg-mute)" }}>None</span>
          )}
        </div>
      )}

      {labels.length > 0 && (
        <LabelPicker
          placement="inline"
          labels={labels}
          selected={labelIds}
          disabled={assign.isPending}
          // Deliberately not "Choose labels": Playwright matches an accessible
          // name as a case-insensitive *substring*, so any inner group whose
          // name contains "labels" would make the enclosing `role="group"
          // aria-label="Labels"` ambiguous the moment the picker is open.
          ariaLabel="Label vocabulary"
          triggerAriaLabel="Add or remove labels"
          triggerContent="Labels"
          active={labelIds.length > 0}
          onToggle={(id) =>
            assign.mutate(labelIds.includes(id) ? { remove: [id] } : { add: [id] })
          }
        />
      )}
    </div>
  );
}
