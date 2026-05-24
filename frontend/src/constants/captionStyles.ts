export const STYLE_LABELS: Record<string, string[]> = {
  florence2: ["short", "detailed", "tags", "dense", "promptgen"],
  paligemma2: ["short", "detailed", "tags", "booru"],
};

export function modelType(model: string): string | null {
  if (model.startsWith("ollama:")) return "ollama";
  if (model === "paligemma2") return "paligemma2";
  if (model.startsWith("florence2")) return "florence2";
  return null;
}
