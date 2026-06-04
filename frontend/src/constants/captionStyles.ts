export const STYLE_LABELS: Record<string, string[]> = {
  florence2: ["short", "detailed", "tags"],
  florence2_promptgen: ["short", "detailed", "promptgen"],
  paligemma2: ["short", "detailed", "tags", "booru"],
  joycaption: [
    "descriptive", "casual", "straightforward", "sd_prompt", "midjourney",
    "danbooru", "e621", "rule34", "booru_like", "art_critic", "product", "social_media",
  ],
};

export function modelType(model: string): string | null {
  if (model.startsWith("ollama:")) return "ollama";
  if (model === "paligemma2") return "paligemma2";
  if (model === "florence2_promptgen") return "florence2_promptgen";
  if (model.startsWith("florence2")) return "florence2";
  if (model.startsWith("joycaption")) return "joycaption";
  return null;
}
