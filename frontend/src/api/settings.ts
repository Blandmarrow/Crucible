import client from "./client";

export interface Thresholds {
  blur_threshold: number;
  noise_threshold: number;
  uniformity_threshold: number;
  duplicate_threshold: number;
  watermark_threshold: number;
  nsfw_threshold: number;
  gdino_threshold: number;
  sam3_threshold: number;
  versioning_mode: "off" | "manual" | "auto";
  auto_rescan_on_open: boolean;
  comfyui_url: string;
  comfy_workflow_dir: string;
}

/** One secret as the server reports it: never the plaintext, only a mask and its origin. */
export interface Secret {
  masked: string;
  /** "db" = saved in Settings, "env" = inherited from .env, "unset" = no value anywhere. */
  source: "db" | "env" | "unset";
}

export interface Secrets {
  hf_token: Secret;
  gelbooru_api_key: Secret;
  gelbooru_user_id: Secret;
}

/**
 * Deliberately NOT Partial<Secrets> — an update carries plaintext strings, a read carries
 * {masked, source} objects. The asymmetry makes `updateSecrets(secrets)` a compile error, so
 * the mask-echo mistake is caught by `npm run build` as well as by the server's 422.
 * Omit a field to leave it unchanged; send "" to clear the override and fall back to .env.
 */
export interface SecretsUpdate {
  hf_token?: string;
  gelbooru_api_key?: string;
  gelbooru_user_id?: string;
}

export type SecretKey = keyof Secrets;

export const settingsApi = {
  getThresholds: () =>
    client.get<Thresholds>("/settings/thresholds").then((r) => r.data),
  updateThresholds: (data: Partial<Thresholds>) =>
    client.patch<Thresholds>("/settings/thresholds", data).then((r) => r.data),
  getSecrets: () => client.get<Secrets>("/settings/secrets").then((r) => r.data),
  updateSecrets: (data: SecretsUpdate) =>
    client.patch<Secrets>("/settings/secrets", data).then((r) => r.data),
};
