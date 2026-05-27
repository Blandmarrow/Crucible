import { create } from "zustand";

export interface UploadProgress {
  datasetId: string;
  done: number;
  total: number;
  errors: number;
}

interface UploadStore {
  progress: UploadProgress | null;
  setProgress: (p: UploadProgress | null) => void;
}

export const useUploadStore = create<UploadStore>((set) => ({
  progress: null,
  setProgress: (progress) => set({ progress }),
}));
