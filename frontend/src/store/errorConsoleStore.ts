import { create } from "zustand";
import { nanoid } from "./nanoid";

export interface ErrorEntry {
  id: string;
  timestamp: Date;
  type: "error" | "unhandledrejection" | "boundary";
  message: string;
  source?: string;
  line?: number;
  col?: number;
  stack?: string;
}

interface ErrorConsoleStore {
  errors: ErrorEntry[];
  isOpen: boolean;
  addError: (entry: Omit<ErrorEntry, "id" | "timestamp">) => void;
  clearErrors: () => void;
  open: () => void;
  close: () => void;
}

export function errorTypeLabel(type: ErrorEntry["type"]): string {
  if (type === "unhandledrejection") return "rejection";
  if (type === "boundary") return "render";
  return "error";
}

export function formatErrorsForCopy(errors: ErrorEntry[]): string {
  return errors
    .map((e) => {
      const lines = [
        `[${e.timestamp.toISOString()}] [${errorTypeLabel(e.type)}] ${e.message}`,
      ];
      if (e.source) lines.push(`Source: ${e.source}${e.line != null ? `:${e.line}` : ""}${e.col != null ? `:${e.col}` : ""}`);
      if (e.stack) lines.push(`Stack:\n${e.stack}`);
      return lines.join("\n");
    })
    .join("\n\n---\n\n");
}

export const useErrorConsoleStore = create<ErrorConsoleStore>((set) => ({
  errors: [],
  isOpen: false,
  addError: (entry) =>
    set((state) => ({
      errors: [...state.errors, { ...entry, id: nanoid(), timestamp: new Date() }],
      isOpen: true,
    })),
  clearErrors: () => set({ errors: [] }),
  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),
}));
