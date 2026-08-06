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
