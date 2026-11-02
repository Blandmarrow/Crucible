import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";

import { settingsApi } from "../../api/settings";
import { useLabels } from "../../hooks/useLabels";
import { RATING_KEY_ACTIONS } from "../../hooks/useRatingKeys";
import { apiErrorDetail } from "../../utils/apiError";
import HotkeyCaptureButton from "./HotkeyCaptureButton";

/**
 * The five keep/cut rating keys — Settings → Gallery.
 *
 * Saved immediately, one key per PATCH, like `auto_rescan_on_open`. Two rating
 * actions may not share a key: the capture refuses it, and the server 422s on
 * the merged row anyway. A label hotkey on the same key is allowed but warned
 * about, because the label wins that key on the detail page (the 2026-11-02
 * ruling) — refusing it would make the order of binding decide the outcome.
 */
export default function RatingKeysPanel() {
  const qc = useQueryClient();
  const { data: thresholds } = useQuery({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
    staleTime: 60_000,
  });
  const { byHotkey } = useLabels();

  const mutation = useMutation({
    mutationFn: settingsApi.updateThresholds,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "thresholds"] });
      toast.success("Rating key saved");
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Could not save the rating key")),
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }} data-testid="rating-keys">
      {RATING_KEY_ACTIONS.map((action) => (
        <div key={action.field} style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 12.5, minWidth: 110 }}>{action.label}</span>
          <HotkeyCaptureButton
            name={action.label}
            value={thresholds?.[action.field] || null}
            disabled={!thresholds || mutation.isPending}
            ownerOf={(key) =>
              RATING_KEY_ACTIONS.find((a) => a.field !== action.field && thresholds?.[a.field] === key)
                ?.label ?? null
            }
            warnOf={(key) => {
              const label = byHotkey.get(key);
              return label ? `Label “${label.name}” wins this key on the detail page` : null;
            }}
            onChange={(key) => mutation.mutate({ [action.field]: key ?? "" })}
          />
        </div>
      ))}
    </div>
  );
}
