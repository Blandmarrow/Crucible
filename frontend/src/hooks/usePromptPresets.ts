import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import toast from "react-hot-toast";
import { appDataApi, type PromptPresetCreate } from "../api/appData";
import { runLegacyImport } from "../utils/legacyImport";

const PRESETS_QUERY_KEY = ["app-data", "presets"];

/** Saved caption prompt presets, shared by every browser that opens this install.
 *  `save` resolves to whether it worked, so a caller clears its form and reports
 *  success only then; failures are reported here. */
export function usePromptPresets() {
  const qc = useQueryClient();

  const { data: presets = [], isPending, isError } = useQuery({
    queryKey: PRESETS_QUERY_KEY,
    queryFn: async () => {
      await runLegacyImport();
      return appDataApi.listPresets();
    },
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: PRESETS_QUERY_KEY });

  const saveMutation = useMutation({ mutationFn: appDataApi.createPreset, onSuccess: invalidate });
  const removeMutation = useMutation({
    mutationFn: appDataApi.deletePreset,
    onSuccess: invalidate,
    onError: (err) => {
      invalidate();
      // Already gone, as from a second click before the list refreshed: what was asked for.
      if (axios.isAxiosError(err) && err.response?.status === 404) return;
      toast.error("Failed to delete preset");
    },
  });

  async function save(preset: PromptPresetCreate): Promise<boolean> {
    // Enter can repeat before the first request returns and clears the caller's form.
    if (saveMutation.isPending) return false;
    try {
      await saveMutation.mutateAsync(preset);
      return true;
    } catch {
      toast.error("Failed to save preset");
      return false;
    }
  }

  // Kept apart from "none saved", because that's what would convince someone they've lost them.
  const emptyMessage = isPending
    ? "Loading presets…"
    : isError
      ? "Couldn't load presets."
      : "No presets saved yet.";

  return { presets, emptyMessage, save, remove: removeMutation.mutate };
}
