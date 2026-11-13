import type { CaptioningModels } from "../api/captioning";
import type { ProviderOut } from "../api/providers";

/**
 * Which captioning backend a model id dispatches to — a hand-mirror of
 * `_caption_backend` in `backend/routers/captioning.py`, same six prefixes in the
 * same order.
 *
 * `POST /captioning/run` and `/pipeline` both run this through a pydantic
 * `@field_validator`, so an id matching nothing here is a **422 for the whole
 * request** — in a pipeline that means step 1 never runs either. This exists so
 * the frontend can refuse the Run *before* sending it, naming the offending
 * step. Keep in step with the backend function; `test_captioning_model_registry.py`
 * holds the parity test.
 *
 * Deliberately NOT `modelType` from `captionStyles.ts`, and the two must stay
 * separate: `modelType` answers "which STYLE_LABELS vocabulary does this model
 * offer", so it returns null for `wd14:` and `openai_compat:` (neither has a
 * style list) — both of which are perfectly runnable. Gating Run on `modelType`
 * would disable the two most-used backends.
 */
export function captionBackend(model: string): string | null {
  if (model.startsWith("florence2")) return "florence2";
  if (model === "paligemma2") return "paligemma2";
  if (model.startsWith("joycaption_")) return "joycaption_";
  if (model.startsWith("ollama:")) return "ollama:";
  if (model.startsWith("openai_compat:")) return "openai_compat:";
  if (model.startsWith("wd14:")) return "wd14:";
  return null;
}

/**
 * Whether a model reads a custom prompt: the backends whose `caption_image` takes
 * `custom_prompt` (JoyCaption, Ollama, OpenAI-compatible). Florence-2, PaliGemma-2
 * and WD14 ignore it, so a prompt box, a `{previous_caption}` toggle or the detail
 * page's Fix mode offered for them would be a silent no-op.
 *
 * Built on `captionBackend`, never on `modelType` — see the note above.
 */
export function takesPrompt(model: string): boolean {
  const backend = captionBackend(model);
  return backend === "joycaption_" || backend === "ollama:" || backend === "openai_compat:";
}

/** The placeholder the backend fills with the image's caption (`_resolve_prompt`). */
export const PREVIOUS_CAPTION = "{previous_caption}";

/**
 * The instruction an "Include … caption" toggle sends when the prompt box is empty. A
 * custom prompt *replaces* the style preset in every prompt-taking backend, so the bare
 * placeholder would send the caption alone, with no instruction at all.
 */
const REFINE_PROMPT = [
  "Here is an existing caption for this image:",
  "",
  PREVIOUS_CAPTION,
  "",
  "Write an improved caption for the image, keeping what the existing caption gets right. " +
    "Reply with the caption only.",
].join("\n");

/**
 * `prompt` with `{previous_caption}` added when `on` and the prompt does not already
 * place it — the Captioning page's "Include … caption" toggles, step 1 and later steps,
 * and the Runs page's fill from that page. An empty prompt becomes `REFINE_PROMPT`.
 */
export function withPreviousCaption(prompt: string, on: boolean): string {
  if (!on || prompt.includes(PREVIOUS_CAPTION)) return prompt;
  return prompt.trim() ? `${prompt}\n\n${PREVIOUS_CAPTION}` : REFINE_PROMPT;
}

/**
 * The detail page's Fix mode prompt (#105): a fixed template around the user's note.
 * The backend fills `{previous_caption}` from the stored caption, which Fix saves first
 * when the draft is dirty, so the model sees what the user sees.
 */
export function buildFixPrompt(note: string): string {
  return [
    "Here is the current caption for this image:",
    "",
    PREVIOUS_CAPTION,
    "",
    "A person checked it against the image and says it needs this correction:",
    "",
    note.trim(),
    "",
    "Rewrite the caption so it fixes this. Keep everything else that is accurate, and " +
      "keep the same format, style and length. Reply with the caption only.",
  ].join("\n");
}

export type CaptionModelOption = { id: string; label: string; group: string };

/**
 * Every model id `GET /captioning/models` currently offers, in picker order.
 *
 * `providers` is passed separately because the Settings page reads it from its own
 * `["providers"]` query; callers holding only the captioning payload pass
 * `data?.openai_compat_models ?? []`.
 *
 * Membership here is **not** a validity predicate — an empty Ollama group only
 * means the daemon is down (the backend swallows the error and returns `[]`), and
 * `providers` may still be resolving. Use `captionBackend` to decide whether a
 * request would be refused; use this list only for informational wording.
 */
export function captionModelOptions(
  data: CaptioningModels | undefined,
  providers: ProviderOut[],
): CaptionModelOption[] {
  const opts: CaptionModelOption[] = [];
  for (const m of data?.local_models ?? []) {
    opts.push({ id: m.id, label: m.name, group: "Local models" });
  }
  for (const m of data?.wd14_models ?? []) {
    opts.push({ id: m.id, label: m.name, group: "Tagger" });
  }
  for (const m of data?.ollama_models ?? []) {
    opts.push({ id: m.id, label: m.name, group: "Ollama" });
  }
  for (const p of providers) {
    opts.push({
      id: `openai_compat:${p.id}`,
      label: p.name,
      group: p.is_remote ? "Cloud providers" : "Local providers",
    });
  }
  return opts;
}

/** The ids of `captionModelOptions`, for membership checks. */
export function captionModelIds(
  data: CaptioningModels | undefined,
  providers: ProviderOut[],
): string[] {
  return captionModelOptions(data, providers).map((o) => o.id);
}
