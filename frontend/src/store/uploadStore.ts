import { create } from "zustand";

/** The live counters behind the upload bar, while one upload gesture runs.
 *
 *  `errors` and `skipped` are kept apart for the same reason `UploadTally` in
 *  `utils/uploadToast.ts` keeps them apart: `errors` counts thrown requests,
 *  `skipped` counts files the server answered 201 for and declined to store (an
 *  unsupported type, say). Folding the two turns a dropped `.txt` into an amber
 *  "1 failed" bar, which reads as a broken upload rather than an ignored file. */
export interface UploadProgress {
  datasetId: string;
  done: number;
  total: number;
  errors: number;
  skipped: number;
}

interface UploadStore {
  progress: UploadProgress | null;
  setProgress: (p: UploadProgress | null) => void;
}

export const useUploadStore = create<UploadStore>((set) => ({
  progress: null,
  setProgress: (progress) => set({ progress }),
}));
