export const PROVIDER_PRESETS: Record<string, string[]> = {
  "generativelanguage.googleapis.com": [
    "gemini-2.5-pro-preview-05-06",
    "gemini-2.5-flash-preview-04-17",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
  ],
  "api.groq.com": [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
  ],
  "api.openai.com": [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "o1",
    "o1-mini",
    "o3-mini",
  ],
  "api.together.xyz": [
    "meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo",
    "meta-llama/Llama-3.2-90B-Vision-Instruct-Turbo",
  ],
};

export function getPresetsForUrl(baseUrl: string): string[] {
  if (!baseUrl) return [];
  try {
    const host = new URL(baseUrl).hostname.toLowerCase();
    for (const [key, models] of Object.entries(PROVIDER_PRESETS)) {
      if (host.includes(key)) return models;
    }
  } catch {
    // invalid URL while typing
  }
  return [];
}
