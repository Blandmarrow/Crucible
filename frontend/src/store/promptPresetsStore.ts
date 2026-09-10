import { create } from "zustand";
import { persist } from "zustand/middleware";
import { nanoid } from "./nanoid";

export interface PromptPreset {
  id: string;
  name: string;
  model: string;
  style: string;
  prompt: string;
}

interface PresetsStore {
  presets: PromptPreset[];
  save: (p: Omit<PromptPreset, "id">) => void;
  remove: (id: string) => void;
  update: (id: string, p: Partial<Omit<PromptPreset, "id">>) => void;
}

export const usePresetsStore = create<PresetsStore>()(
  persist(
    (set) => ({
      presets: [],
      save: (p) =>
        set((s) => ({
          presets: [
            ...s.presets,
            // `nanoid()`, not `crypto.randomUUID()`: the latter is `[SecureContext]`
            // and is absent on `http://0.0.0.0:8000` and over LAN, where saving a
            // preset threw "crypto.randomUUID is not a function" and the two callers
            // with no toast simply did nothing (issue #93). Ids here are opaque and
            // never parsed, so existing presets keep their UUIDs — no migration.
            // Three id generators now coexist (this one, `errorConsoleStore`/`paneStore`
            // via the same helper, and `CaptioningPage::makeStepId`); folding them into
            // one `utils/id.ts` is a pure move filed separately, kept out of a one-line fix.
            { ...p, id: nanoid() },
          ],
        })),
      remove: (id) =>
        set((s) => ({ presets: s.presets.filter((p) => p.id !== id) })),
      update: (id, patch) =>
        set((s) => ({
          presets: s.presets.map((p) => (p.id === id ? { ...p, ...patch } : p)),
        })),
    }),
    { name: "caption-prompt-presets" }
  )
);
