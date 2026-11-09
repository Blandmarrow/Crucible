import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import toast from "react-hot-toast";
import {
  appDataApi,
  type PromptGenPreset,
  type PromptGenPresetCreate,
  type PromptPreset,
  type PromptPresetCreate,
} from "../api/appData";
import { runLegacyImport } from "../utils/legacyImport";

const PRESETS_QUERY_KEY = ["app-data", "presets"];
const PROMPT_GEN_PRESETS_QUERY_KEY = ["app-data", "prompt-gen-presets"];

interface ServerPresetsSource<T, C> {
  queryKey: readonly unknown[];
  list: () => Promise<T[]>;
  create: (body: C) => Promise<unknown>;
  remove: (id: string) => Promise<unknown>;
}

/** A list of saved presets kept on the server, shared by every browser that opens this
 *  install. `save` resolves to whether it worked, so a caller clears its form and
 *  reports success only then; failures are reported here. */
function useServerPresets<T, C>({ queryKey, list, create, remove }: ServerPresetsSource<T, C>) {
  const qc = useQueryClient();

  const { data: presets = [], isPending, isError } = useQuery({ queryKey, queryFn: list });

  const invalidate = () => qc.invalidateQueries({ queryKey });

  const saveMutation = useMutation({ mutationFn: create, onSuccess: invalidate });
  const removeMutation = useMutation({
    mutationFn: remove,
    onSuccess: invalidate,
    onError: (err) => {
      invalidate();
      // Already gone, as from a second click before the list refreshed: what was asked for.
      if (axios.isAxiosError(err) && err.response?.status === 404) return;
      toast.error("Failed to delete preset");
    },
  });

  async function save(preset: C): Promise<boolean> {
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

/** Saved caption prompt presets. */
export function usePromptPresets() {
  return useServerPresets<PromptPreset, PromptPresetCreate>({
    queryKey: PRESETS_QUERY_KEY,
    list: async () => {
      // Caption presets once lived in browser storage; carry them over first.
      await runLegacyImport();
      return appDataApi.listPresets();
    },
    create: appDataApi.createPreset,
    remove: appDataApi.deletePreset,
  });
}

/** Saved "Instructions" texts for the ComfyUI Generate prompts window. Never stored in
 *  the browser, so there is no legacy import to wait on. */
export function usePromptGenPresets() {
  return useServerPresets<PromptGenPreset, PromptGenPresetCreate>({
    queryKey: PROMPT_GEN_PRESETS_QUERY_KEY,
    list: appDataApi.listPromptGenPresets,
    create: appDataApi.createPromptGenPreset,
    remove: appDataApi.deletePromptGenPreset,
  });
}
