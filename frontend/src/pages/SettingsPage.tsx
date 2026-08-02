import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../utils/apiError";
import { settingsApi, type Thresholds, type SecretKey, type SecretsUpdate } from "../api/settings";
import { comfyApi } from "../api/comfy";
import { providersApi, type ProviderOut, type ProviderCreate } from "../api/providers";
import { captioningApi } from "../api/captioning";
import {
  CONFIRM_DEFAULT_KEY, BRANCH_SNAPSHOT_KEY, GALLERY_PAGE_SIZE_KEY, SUBFOLDER_RENAME_KEY,
  GALLERY_DEFAULT_SORT_KEY, GALLERY_DEFAULT_CAPTION_KEY, GALLERY_DEFAULT_QUALITY_KEY,
  CAPTION_DEFAULT_MODEL_KEY, CAPTION_DEFAULT_STYLE_KEY, CAPTION_DEFAULT_SCOPE_KEY,
  CAPTION_DEFAULT_DELIMITER_KEY, CAPTION_DEFAULT_STRIP_REFS_KEY, CAPTION_DEFAULT_RENAME_KEY,
  CAPTION_DEFAULT_SAVE_BACKUP_KEY, CAPTIONING_WORKFLOW_KEY, CAPTIONING_FILTERS_PREFIX,
  GALLERY_CHECKBOX_SIZE_MIN, GALLERY_CHECKBOX_SIZE_MAX,
  getGalleryPageSize, getGalleryDefaultSort,
} from "../constants/storage";
import { useUiPrefsStore } from "../store/uiPrefsStore";
import GalleryCheckbox from "../components/gallery/GalleryCheckbox";
import { clearPersisted } from "../utils/persistentState";
import { SORT_OPTIONS } from "../constants/galleryOptions";
import RadioGroup from "../components/common/RadioGroup";
import ConfirmDialog from "../components/common/ConfirmDialog";
import DirPickerModal from "../components/common/DirPickerModal";
import ModelPicker from "../components/providers/ModelPicker";
import SecretField from "../components/settings/SecretField";

type ModelOption = { id: string; label: string; group: string };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildModelOptions(modelsData: any, providers: ProviderOut[]): ModelOption[] {
  const opts: ModelOption[] = [];
  for (const m of (modelsData?.local_models ?? []) as { id: string; name: string }[]) {
    opts.push({ id: m.id, label: m.name, group: "Local models" });
  }
  for (const m of (modelsData?.wd14_models ?? []) as { id: string; name: string }[]) {
    opts.push({ id: m.id, label: m.name, group: "Tagger" });
  }
  for (const m of (modelsData?.ollama_models ?? []) as { id: string; name: string; size_mb?: number }[]) {
    opts.push({ id: m.id, label: m.name, group: "Ollama" });
  }
  for (const p of providers) {
    opts.push({ id: `openai_compat:${p.id}`, label: p.name, group: p.is_remote ? "Cloud providers" : "Local providers" });
  }
  return opts;
}

const DEFAULTS: Thresholds = {
  blur_threshold: 100,
  noise_threshold: 15,
  uniformity_threshold: 12,
  duplicate_threshold: 8,
  watermark_threshold: 0.6,
  nsfw_threshold: 0.5,
  gdino_threshold: 0.35,
  sam3_threshold: 0.5,
  versioning_mode: "off",
  auto_rescan_on_open: false,
  comfyui_url: "",
  comfy_workflow_dir: "",
};

interface ThresholdField {
  key: keyof Thresholds;
  label: string;
  description: string;
  step: string;
  min: string;
  max?: string;
}

const FIELDS: ThresholdField[] = [
  {
    key: "blur_threshold",
    label: "Blur threshold",
    description: "Laplacian variance — images below this value are flagged as blurry. Higher = stricter.",
    step: "1",
    min: "0",
  },
  {
    key: "noise_threshold",
    label: "Noise threshold",
    description: "Smooth-region std dev — images above this value are flagged as noisy. Lower = stricter.",
    step: "0.1",
    min: "0",
  },
  {
    key: "uniformity_threshold",
    label: "Uniformity threshold",
    description: "Grayscale std dev — images below this value are flagged as near-uniform. Higher = stricter.",
    step: "0.1",
    min: "0",
  },
  {
    key: "duplicate_threshold",
    label: "Duplicate threshold",
    description: "pHash Hamming distance — images within this distance are considered duplicates. Lower = stricter.",
    step: "1",
    min: "1",
  },
  {
    key: "watermark_threshold",
    label: "Watermark threshold",
    description: "CLIP zero-shot probability (0–1) — images at or above this score are flagged as watermarked. Lower = stricter.",
    step: "0.01",
    min: "0.01",
    max: "1",
  },
  {
    key: "nsfw_threshold",
    label: "NSFW threshold",
    description: "Marqo classifier probability (0–1) — images at or above this score are flagged as NSFW. Lower = stricter.",
    step: "0.01",
    min: "0.01",
    max: "1",
  },
  {
    key: "gdino_threshold",
    label: "DINO box confidence",
    description: "Grounding DINO minimum confidence (0–1) for a detected box to be passed to SAM2. Lower = more detections (noisier); higher = fewer but more precise boxes.",
    step: "0.01",
    min: "0.01",
    max: "1",
  },
  {
    key: "sam3_threshold",
    label: "SAM 3 confidence",
    description: "SAM 3 minimum confidence (0–1) for a segmented instance to be kept. Lower = more masks (noisier); higher = fewer but more precise masks.",
    step: "0.01",
    min: "0.01",
    max: "1",
  },
];

export default function SettingsPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState<Thresholds>(DEFAULTS);
  const [confirmDefault, setConfirmDefault] = useState<"cancel" | "confirm">(
    () => (localStorage.getItem(CONFIRM_DEFAULT_KEY) === "confirm" ? "confirm" : "cancel")
  );
  const [branchSnapshot, setBranchSnapshot] = useState<"ask" | "auto">(
    () => (localStorage.getItem(BRANCH_SNAPSHOT_KEY) === "auto" ? "auto" : "ask")
  );
  const [pageSize, setPageSize] = useState<number>(getGalleryPageSize);
  const [subfolderRename, setSubfolderRename] = useState<"on" | "off">(
    () => (localStorage.getItem(SUBFOLDER_RENAME_KEY) === "off" ? "off" : "on")
  );

  // Gallery defaults
  const [galleryDefaultSort, setGalleryDefaultSort] = useState(getGalleryDefaultSort);
  const [galleryDefaultCaption, setGalleryDefaultCaption] = useState(
    () => localStorage.getItem(GALLERY_DEFAULT_CAPTION_KEY) ?? "all"
  );
  const [galleryDefaultQuality, setGalleryDefaultQuality] = useState(
    () => localStorage.getItem(GALLERY_DEFAULT_QUALITY_KEY) ?? ""
  );
  // Store-backed, not local state: a gallery pane mounted alongside this one must
  // re-render its cards the moment the toggle flips.
  const galleryLicenseBadge = useUiPrefsStore((s) => s.galleryLicenseBadge);
  const setGalleryLicenseBadge = useUiPrefsStore((s) => s.setGalleryLicenseBadge);

  // Captioning defaults
  const [captionDefaultModel, setCaptionDefaultModel] = useState(
    () => localStorage.getItem(CAPTION_DEFAULT_MODEL_KEY) ?? ""
  );
  const [captionDefaultStyle, setCaptionDefaultStyle] = useState(
    () => localStorage.getItem(CAPTION_DEFAULT_STYLE_KEY) ?? "detailed"
  );
  const [captionDefaultScope, setCaptionDefaultScope] = useState(
    () => localStorage.getItem(CAPTION_DEFAULT_SCOPE_KEY) ?? "uncaptioned"
  );
  const [captionDefaultDelimiter, setCaptionDefaultDelimiter] = useState(
    () => localStorage.getItem(CAPTION_DEFAULT_DELIMITER_KEY) ?? "overwrite"
  );
  const [captionDefaultStripRefs, setCaptionDefaultStripRefs] = useState(
    () => localStorage.getItem(CAPTION_DEFAULT_STRIP_REFS_KEY) !== "false"
  );
  const [captionDefaultRename, setCaptionDefaultRename] = useState(
    () => localStorage.getItem(CAPTION_DEFAULT_RENAME_KEY) === "true"
  );
  const [captionDefaultSaveBackup, setCaptionDefaultSaveBackup] = useState(
    () => localStorage.getItem(CAPTION_DEFAULT_SAVE_BACKUP_KEY) === "true"
  );

  const { data: thresholds, isLoading } = useQuery({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
    staleTime: 60_000,
  });

  useEffect(() => {
    if (thresholds) setForm(thresholds);
  }, [thresholds]);

  const mutation = useMutation({
    mutationFn: settingsApi.updateThresholds,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "thresholds"] });
      toast.success("Thresholds saved");
    },
    onError: () => toast.error("Failed to save thresholds"),
  });

  function handleSave() {
    if (!thresholds) return;
    const changed: Partial<Thresholds> = {};
    for (const field of FIELDS) {
      if (form[field.key] !== thresholds[field.key]) {
        (changed as Record<string, unknown>)[field.key] = form[field.key];
      }
    }
    if (form.versioning_mode !== thresholds.versioning_mode) {
      changed.versioning_mode = form.versioning_mode;
    }
    if (form.auto_rescan_on_open !== thresholds.auto_rescan_on_open) {
      changed.auto_rescan_on_open = form.auto_rescan_on_open;
    }
    if (Object.keys(changed).length === 0) {
      toast("No changes to save");
      return;
    }
    mutation.mutate(changed);
  }

  function handleReset() {
    setForm(DEFAULTS);
  }

  const isChanged =
    thresholds &&
    (FIELDS.some((f) => form[f.key] !== thresholds[f.key]) ||
      form.versioning_mode !== thresholds.versioning_mode ||
      form.auto_rescan_on_open !== thresholds.auto_rescan_on_open);

  const [activeTab, setActiveTab] = useState<"gallery" | "captioning" | "ui" | "quality" | "versioning" | "providers" | "comfyui" | "secrets">("gallery");

  // Gallery checkbox size lives in uiPrefsStore (not local state) so dragging the
  // slider re-renders gallery cards live, including a GalleryPage in another pane.
  const checkboxSize = useUiPrefsStore((s) => s.galleryCheckboxSize);
  const setGalleryCheckboxSize = useUiPrefsStore((s) => s.setGalleryCheckboxSize);

  // ComfyUI connection test
  const [pingResult, setPingResult] = useState<{ ok: boolean; error?: string } | null>(null);
  const [pinging, setPinging] = useState(false);
  const [showWorkflowDirPicker, setShowWorkflowDirPicker] = useState(false);
  async function testComfyConnection() {
    setPinging(true);
    setPingResult(null);
    try {
      setPingResult(await comfyApi.ping(form.comfyui_url || undefined));
    } catch {
      setPingResult({ ok: false, error: "Request failed" });
    } finally {
      setPinging(false);
    }
  }

  // Captioning models (loaded lazily when tab is first opened)
  const { data: captioningModels } = useQuery({
    queryKey: ["captioning-models"],
    queryFn: captioningApi.models,
    staleTime: Infinity,
    enabled: activeTab === "captioning",
  });

  // Secrets (API Keys tab). Its own query key — never ["settings","thresholds"], which six
  // other screens cache — and gated on the tab so secrets never go over the wire for anyone
  // who does not open it, following the ["captioning-models"] lazy-tab pattern above.
  const { data: secrets } = useQuery({
    queryKey: ["settings", "secrets"],
    queryFn: settingsApi.getSecrets,
    enabled: activeTab === "secrets",
  });

  const secretsMutation = useMutation({
    mutationFn: (body: SecretsUpdate) => settingsApi.updateSecrets(body),
    // Invalidate rather than write the response into the cache: the fresh GET is also a
    // client-side guard against ever holding a value that came back from a write.
    onSuccess: (data, body) => {
      qc.invalidateQueries({ queryKey: ["settings", "secrets"] });
      const key = Object.keys(body)[0] as SecretKey | undefined;
      if (key && body[key] === "") {
        // The toast comes from the *response* — only it knows whether clearing the override
        // revealed an inherited .env value or left the secret unset entirely.
        toast.success(data[key].source === "env" ? "Cleared — using the .env value" : "Cleared");
      } else {
        toast.success("Saved");
      }
    },
    onError: (e: unknown) => toast.error(apiErrorDetail(e, "Failed to save key")),
  });

  // Providers state

  const { data: providers = [], refetch: refetchProviders } = useQuery({
    queryKey: ["providers"],
    queryFn: providersApi.list,
    staleTime: 30_000,
  });
  const modelOpts = buildModelOptions(captioningModels, providers);

  const [providerForm, setProviderForm] = useState<ProviderCreate & { id?: string }>({ name: "", base_url: "", api_key: "", default_model: "", max_image_px: 1024, max_tokens: 2048 });
  const [showProviderForm, setShowProviderForm] = useState(false);
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null);
  const [deletingProvider, setDeletingProvider] = useState<ProviderOut | null>(null);

  const createProviderMutation = useMutation({
    mutationFn: (body: ProviderCreate) => providersApi.create(body),
    onSuccess: () => { toast.success("Provider added"); refetchProviders(); qc.invalidateQueries({ queryKey: ["captioning-models"] }); setShowProviderForm(false); setEditingProviderId(null); },
    onError: (e: unknown) => toast.error(apiErrorDetail(e, "Failed to save provider")),
  });
  const updateProviderMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof providersApi.update>[1] }) =>
      providersApi.update(id, body),
    onSuccess: () => { toast.success("Provider updated"); refetchProviders(); qc.invalidateQueries({ queryKey: ["captioning-models"] }); setShowProviderForm(false); setEditingProviderId(null); },
    onError: () => toast.error("Failed to update provider"),
  });
  const deleteProviderMutation = useMutation({
    mutationFn: (id: string) => providersApi.delete(id),
    onSuccess: () => { toast.success("Provider deleted"); refetchProviders(); qc.invalidateQueries({ queryKey: ["captioning-models"] }); setDeletingProvider(null); },
    onError: () => toast.error("Failed to delete provider"),
  });

  function openAddProvider() {
    setProviderForm({ name: "", base_url: "http://localhost:1234/v1", api_key: "", default_model: "", max_image_px: 1024, max_tokens: 2048 });
    setEditingProviderId(null);
    setShowProviderForm(true);
  }

  function openEditProvider(p: ProviderOut) {
    setProviderForm({ name: p.name, base_url: p.base_url, api_key: "", default_model: p.default_model, max_image_px: p.max_image_px, max_tokens: p.max_tokens });
    setEditingProviderId(p.id);
    setShowProviderForm(true);
  }

  function handleSaveProvider() {
    if (!providerForm.name.trim() || !providerForm.base_url.trim()) { toast.error("Name and Base URL are required"); return; }
    if (editingProviderId) {
      updateProviderMutation.mutate({ id: editingProviderId, body: {
        name: providerForm.name,
        base_url: providerForm.base_url,
        ...(providerForm.api_key ? { api_key: providerForm.api_key } : {}),
        default_model: providerForm.default_model,
        max_image_px: providerForm.max_image_px,
        max_tokens: providerForm.max_tokens,
      }});
    } else {
      createProviderMutation.mutate(providerForm);
    }
  }

  return (
    <div style={{ padding: "28px 32px", maxWidth: 640 }}>
      <h1 style={{ fontSize: 18, fontWeight: 600, marginBottom: 4 }}>Settings</h1>
      <p style={{ color: "var(--fg-mute)", fontSize: 13, marginBottom: 20 }}>
        Global configuration for this Crucible instance.
      </p>

      <div className="tabs">
        <button className={`tab${activeTab === "gallery" ? " active" : ""}`} onClick={() => setActiveTab("gallery")}>Gallery</button>
        <button className={`tab${activeTab === "captioning" ? " active" : ""}`} onClick={() => setActiveTab("captioning")}>Captioning</button>
        <button className={`tab${activeTab === "ui" ? " active" : ""}`} onClick={() => setActiveTab("ui")}>UI Behavior</button>
        <button className={`tab${activeTab === "quality" ? " active" : ""}`} onClick={() => setActiveTab("quality")}>Quality Thresholds</button>
        <button className={`tab${activeTab === "versioning" ? " active" : ""}`} onClick={() => setActiveTab("versioning")}>Versioning</button>
        <button className={`tab${activeTab === "providers" ? " active" : ""}`} onClick={() => setActiveTab("providers")}>LLM Providers</button>
        <button className={`tab${activeTab === "comfyui" ? " active" : ""}`} onClick={() => setActiveTab("comfyui")}>ComfyUI</button>
        <button className={`tab${activeTab === "secrets" ? " active" : ""}`} onClick={() => setActiveTab("secrets")}>API Keys</button>
      </div>

      {activeTab === "secrets" && (
        <div className="panel">
          <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <p style={{ fontSize: 12.5, color: "var(--fg-mute)", margin: 0 }}>
              Keys saved here override the matching values in <code style={{ fontSize: 11 }}>.env</code>;
              clearing one goes back to the <code style={{ fontSize: 11 }}>.env</code> value. They are
              stored unencrypted in the local database, the same as LLM provider keys.
            </p>

            {!secrets && <div style={{ fontSize: 12.5, color: "var(--fg-dim)" }}>Loading…</div>}

            {secrets && (
              <>
                <SecretField
                  label="HuggingFace token"
                  help={
                    <>
                      Needed to download gated models such as PaliGemma-2. Create one at{" "}
                      <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>
                        huggingface.co/settings/tokens
                      </a>
                      . A change applies to the next model download — a download already running keeps the old token.
                    </>
                  }
                  secret={secrets.hf_token}
                  envVar="HF_TOKEN"
                  busy={secretsMutation.isPending}
                  onSave={(v) => secretsMutation.mutate({ hf_token: v })}
                  onClear={() => secretsMutation.mutate({ hf_token: "" })}
                />
                <SecretField
                  label="Gelbooru API key"
                  help="Raises the rate limit for Gelbooru tag lookups on the Booru page. Both this and the user ID are required — with either missing, lookups stay anonymous."
                  secret={secrets.gelbooru_api_key}
                  envVar="GELBOORU_API_KEY"
                  busy={secretsMutation.isPending}
                  onSave={(v) => secretsMutation.mutate({ gelbooru_api_key: v })}
                  onClear={() => secretsMutation.mutate({ gelbooru_api_key: "" })}
                />
                <SecretField
                  label="Gelbooru user ID"
                  help="The numeric user ID that goes with the API key above, from your Gelbooru account options page."
                  secret={secrets.gelbooru_user_id}
                  envVar="GELBOORU_USER_ID"
                  busy={secretsMutation.isPending}
                  onSave={(v) => secretsMutation.mutate({ gelbooru_user_id: v })}
                  onClear={() => secretsMutation.mutate({ gelbooru_user_id: "" })}
                />
              </>
            )}
          </div>
        </div>
      )}

      {activeTab === "comfyui" && (
        <div className="panel">
          <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 6 }}>Server URL</div>
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
                Base URL of your ComfyUI server (default port 8188). Used by the per-dataset ComfyUI
                generation page to queue workflows and import the results.
              </p>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input
                  className="input"
                  type="text"
                  placeholder="http://127.0.0.1:8188"
                  value={form.comfyui_url}
                  onChange={(e) => { setForm({ ...form, comfyui_url: e.target.value }); setPingResult(null); }}
                  style={{ flex: 1 }}
                />
                <button className="btn ghost" onClick={testComfyConnection} disabled={pinging}>
                  {pinging ? "Testing…" : "Test connection"}
                </button>
                <button
                  className="btn primary"
                  onClick={() => mutation.mutate({ comfyui_url: form.comfyui_url.trim() })}
                  disabled={!thresholds || form.comfyui_url.trim() === thresholds.comfyui_url}
                >
                  Save
                </button>
              </div>
              {pingResult && (
                <p style={{ fontSize: 12, marginTop: 8, color: pingResult.ok ? "var(--good)" : "var(--bad)" }}>
                  {pingResult.ok ? "✓ Connected to ComfyUI" : `✗ ${pingResult.error ?? "Connection failed"}`}
                </p>
              )}
            </div>
            <div>
              <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 6 }}>Workflow folder</div>
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
                Default folder scanned for exported API-format workflow .json files by the
                &ldquo;Scan folder&rdquo; button on the ComfyUI page. Must be a path on the machine
                running Crucible.
              </p>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input
                  className="input"
                  type="text"
                  placeholder="e.g. C:\ComfyUI\user\default\workflows\api"
                  value={form.comfy_workflow_dir}
                  onChange={(e) => setForm({ ...form, comfy_workflow_dir: e.target.value })}
                  style={{ flex: 1 }}
                />
                <button className="btn ghost" onClick={() => setShowWorkflowDirPicker(true)}>Browse…</button>
                <button
                  className="btn primary"
                  onClick={() => mutation.mutate({ comfy_workflow_dir: form.comfy_workflow_dir.trim() })}
                  disabled={!thresholds || form.comfy_workflow_dir.trim() === thresholds.comfy_workflow_dir}
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showWorkflowDirPicker && (
        <DirPickerModal
          title="Select workflow folder"
          confirmLabel="Use folder"
          initialPath={form.comfy_workflow_dir}
          onConfirm={(path) => { setForm({ ...form, comfy_workflow_dir: path }); setShowWorkflowDirPicker(false); }}
          onCancel={() => setShowWorkflowDirPicker(false)}
        />
      )}

      {activeTab === "gallery" && (
        <div className="panel">
          <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div>
              <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 6 }}>Images per page</div>
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
                Number of images loaded per page in the gallery. Lower values reduce memory usage with large high-resolution datasets.
              </p>
              <select
                className="select"
                value={pageSize}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  setPageSize(v);
                  localStorage.setItem(GALLERY_PAGE_SIZE_KEY, String(v));
                  toast.success("Gallery page size saved");
                }}
                style={{ width: 120 }}
              >
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
                <option value={200}>200</option>
              </select>
            </div>

            <div>
              <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 6 }}>Selection checkbox size</div>
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
                Size of the selection checkbox on gallery thumbnails. Increase it if the checkbox is
                hard to hit. Applies immediately.
              </p>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <input
                  type="range"
                  min={GALLERY_CHECKBOX_SIZE_MIN}
                  max={GALLERY_CHECKBOX_SIZE_MAX}
                  step={1}
                  value={checkboxSize}
                  onChange={(e) => setGalleryCheckboxSize(parseInt(e.target.value, 10))}
                  style={{ width: 180 }}
                />
                <span className="mono" style={{ fontSize: 12, minWidth: 44 }}>{checkboxSize}px</span>
                {/* Live preview — the actual gallery component, not a copy of it. */}
                <GalleryCheckbox size={checkboxSize} selected />
              </div>
            </div>

            <div>
              <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 10 }}>Subfolder rename on move</div>
              <RadioGroup
                name="subfolder_rename"
                options={[
                  { value: "on", label: "Rename to subfolder name (default)", desc: "Images are renamed to the subfolder slug when moved (e.g. moving to \"portraits\" renames files to portraits_001.jpg, portraits_002.jpg, …)." },
                  { value: "off", label: "Keep original filenames", desc: "Files keep their current names when moved to a subfolder. Only the subfolder metadata is updated." },
                ]}
                value={subfolderRename}
                onChange={(v) => {
                  setSubfolderRename(v as "on" | "off");
                  localStorage.setItem(SUBFOLDER_RENAME_KEY, v);
                  toast.success("Preference saved");
                }}
              />
            </div>

            <div>
              <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 6 }}>License badge on cards</div>
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
                Deliberately not one of the "Gallery defaults" below: this is a global display
                setting that applies immediately to every open gallery, and the gallery toolbar's
                reset button does not clear it.
              </p>
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5 }}>
                <input
                  type="checkbox"
                  className="checkbox"
                  checked={galleryLicenseBadge}
                  onChange={(e) => {
                    setGalleryLicenseBadge(e.target.checked);
                    toast.success("Preference saved");
                  }}
                />
                Show each image's effective source license on its gallery card
              </label>
            </div>

            <div>
              <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 4 }}>Gallery defaults</div>
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 12px" }}>
                Applied the first time you open a dataset's gallery, before anything has been remembered for it.
                After that, your filter choices are remembered per-dataset (even across restarts) and take
                precedence — use the reset button in the gallery toolbar to clear them and fall back to these
                defaults again.
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <label style={{ fontSize: 12.5, minWidth: 140 }}>Default sort</label>
                  <select
                    className="select"
                    value={galleryDefaultSort}
                    onChange={(e) => {
                      const v = parseInt(e.target.value, 10);
                      setGalleryDefaultSort(v);
                      localStorage.setItem(GALLERY_DEFAULT_SORT_KEY, String(v));
                      toast.success("Preference saved");
                    }}
                    style={{ flex: 1 }}
                  >
                    {SORT_OPTIONS.map((opt, i) => (
                      <option key={i} value={i}>{opt.label}</option>
                    ))}
                  </select>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <label style={{ fontSize: 12.5, minWidth: 140 }}>Default caption filter</label>
                  <select
                    className="select"
                    value={galleryDefaultCaption}
                    onChange={(e) => {
                      setGalleryDefaultCaption(e.target.value);
                      localStorage.setItem(GALLERY_DEFAULT_CAPTION_KEY, e.target.value);
                      toast.success("Preference saved");
                    }}
                    style={{ flex: 1 }}
                  >
                    <option value="all">All images</option>
                    <option value="captioned">Captioned only</option>
                    <option value="uncaptioned">Uncaptioned only</option>
                  </select>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <label style={{ fontSize: 12.5, minWidth: 140 }}>Default quality filter</label>
                  <select
                    className="select"
                    value={galleryDefaultQuality}
                    onChange={(e) => {
                      setGalleryDefaultQuality(e.target.value);
                      localStorage.setItem(GALLERY_DEFAULT_QUALITY_KEY, e.target.value);
                      toast.success("Preference saved");
                    }}
                    style={{ flex: 1 }}
                  >
                    <option value="">None</option>
                    <option value="is_blurry">Blurry</option>
                    <option value="is_noisy">Noisy</option>
                    <option value="is_uniform">Near-uniform</option>
                    <option value="has_watermark">Watermarked</option>
                    <option value="is_duplicate">Duplicate</option>
                  </select>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === "captioning" && (
          <div className="panel">
            <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
                These fallback values apply only the first time you visit the Captioning page, or after resetting
                your remembered configuration there. Once you've used the page, your model, prompt, style, and
                other settings are remembered automatically (even across restarts) and take precedence over these
                defaults.
              </p>

              <div>
                <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 4 }}>Default model</div>
                <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
                  Pre-selected when opening the Captioning page. Falls back to no selection if the model is not available.
                </p>
                <select
                  className="select"
                  value={captionDefaultModel}
                  onChange={(e) => {
                    setCaptionDefaultModel(e.target.value);
                    localStorage.setItem(CAPTION_DEFAULT_MODEL_KEY, e.target.value);
                    toast.success("Preference saved");
                  }}
                  style={{ width: "100%" }}
                >
                  <option value="">No default — select manually</option>
                  {["Local models", "Tagger", "Ollama", "Local providers", "Cloud providers"].map((group) => {
                    const items = modelOpts.filter((o) => o.group === group);
                    if (items.length === 0) return null;
                    return (
                      <optgroup key={group} label={group}>
                        {items.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                      </optgroup>
                    );
                  })}
                  {captioningModels === undefined && captionDefaultModel && (
                    <option value={captionDefaultModel}>{captionDefaultModel} (loading…)</option>
                  )}
                </select>
              </div>

              <div>
                <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 4 }}>Default caption style</div>
                <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
                  Applied when opening the Captioning page. If the default model does not support this style, the first compatible style for that model will be used instead.
                </p>
                <select
                  className="select"
                  value={captionDefaultStyle}
                  onChange={(e) => {
                    setCaptionDefaultStyle(e.target.value);
                    localStorage.setItem(CAPTION_DEFAULT_STYLE_KEY, e.target.value);
                    toast.success("Preference saved");
                  }}
                  style={{ width: 200 }}
                >
                  <option value="detailed">Detailed</option>
                  <option value="short">Short</option>
                  <option value="tags">Tags</option>
                  <option value="promptgen">PromptGen</option>
                  <option value="booru">Booru</option>
                </select>
              </div>

              <div>
                <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 10 }}>Default scope</div>
                <RadioGroup
                  name="caption_default_scope"
                  options={[
                    { value: "uncaptioned", label: "Uncaptioned images only (default)", desc: "Only process images that don't have a caption yet." },
                    { value: "all", label: "All images", desc: "Process every image, overwriting or merging with existing captions depending on the delimiter mode." },
                  ]}
                  value={captionDefaultScope}
                  onChange={(v) => {
                    setCaptionDefaultScope(v);
                    localStorage.setItem(CAPTION_DEFAULT_SCOPE_KEY, v);
                    toast.success("Preference saved");
                  }}
                />
              </div>

              <div>
                <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 4 }}>Default delimiter mode</div>
                <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
                  How new captions are merged with existing ones when scope is set to "All images".
                </p>
                <select
                  className="select"
                  value={captionDefaultDelimiter}
                  onChange={(e) => {
                    setCaptionDefaultDelimiter(e.target.value);
                    localStorage.setItem(CAPTION_DEFAULT_DELIMITER_KEY, e.target.value);
                    toast.success("Preference saved");
                  }}
                  style={{ width: 200 }}
                >
                  <option value="overwrite">Overwrite</option>
                  <option value="append">Append</option>
                  <option value="prepend">Prepend</option>
                </select>
              </div>

              <div>
                <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 10 }}>Default options</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {[
                    { state: captionDefaultStripRefs, setter: setCaptionDefaultStripRefs, key: CAPTION_DEFAULT_STRIP_REFS_KEY, label: "Strip refusals", desc: "Remove common AI refusal phrases from generated captions." },
                    { state: captionDefaultRename, setter: setCaptionDefaultRename, key: CAPTION_DEFAULT_RENAME_KEY, label: "Rename on caption", desc: "After captioning, rename each image file to the subfolder slug (e.g. portraits_001.jpg)." },
                    { state: captionDefaultSaveBackup, setter: setCaptionDefaultSaveBackup, key: CAPTION_DEFAULT_SAVE_BACKUP_KEY, label: "Save backup", desc: "Back up each image's existing .txt sidecar to .txt.bak before overwriting." },
                  ].map(({ state, setter, key, label, desc }) => (
                    <label key={key} style={{ display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer" }}>
                      <input
                        type="checkbox"
                        className="checkbox"
                        checked={state}
                        onChange={(e) => {
                          setter(e.target.checked);
                          localStorage.setItem(key, String(e.target.checked));
                          toast.success("Preference saved");
                        }}
                        style={{ marginTop: 2, flexShrink: 0 }}
                      />
                      <div>
                        <div style={{ fontSize: 13 }}>{label}</div>
                        <div style={{ fontSize: 12, color: "var(--fg-mute)" }}>{desc}</div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <button
                  className="btn ghost sm"
                  onClick={() => {
                    clearPersisted(CAPTIONING_WORKFLOW_KEY);
                    const prefix = CAPTIONING_FILTERS_PREFIX + "-";
                    const toRemove: string[] = [];
                    for (let i = 0; i < localStorage.length; i++) {
                      const k = localStorage.key(i);
                      if (k?.startsWith(prefix)) toRemove.push(k);
                    }
                    toRemove.forEach(k => { try { localStorage.removeItem(k); } catch {} });
                    toast.success("Remembered captioning configuration cleared — defaults above will apply next visit.");
                  }}
                >
                  Reset remembered Captioning configuration
                </button>
              </div>
            </div>
          </div>
      )}

      {activeTab === "ui" && (
        <div className="panel">
          <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 10 }}>Delete confirmation default button</div>
              <RadioGroup
                name="confirm_default"
                options={[
                  { value: "cancel", label: "Cancel (safe default)", desc: "The Cancel button is focused — pressing Enter will dismiss without deleting." },
                  { value: "confirm", label: "Confirm delete", desc: "The confirm button is focused — pressing Enter will proceed with deletion." },
                ]}
                value={confirmDefault}
                onChange={(v) => {
                  setConfirmDefault(v as "cancel" | "confirm");
                  localStorage.setItem(CONFIRM_DEFAULT_KEY, v);
                  toast.success("Preference saved");
                }}
              />
            </div>

            <div>
              <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 10 }}>Folder sync</div>
              <label style={{ display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  className="checkbox"
                  style={{ marginTop: 2 }}
                  checked={form.auto_rescan_on_open}
                  onChange={(e) => {
                    const next = e.target.checked;
                    setForm((prev) => ({ ...prev, auto_rescan_on_open: next }));
                    mutation.mutate({ auto_rescan_on_open: next });
                  }}
                />
                <div>
                  <div style={{ fontSize: 13 }}>Auto-rescan dataset on open</div>
                  <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "2px 0 0" }}>
                    When opening a dataset gallery, scan its folder on disk for new images and
                    <code> .txt</code> captions added outside the app. Off by default.
                  </p>
                </div>
              </label>
            </div>
          </div>
        </div>
      )}

      {activeTab === "quality" && (
        <div className="panel">
          <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            {isLoading ? (
              <div style={{ color: "var(--fg-mute)", fontSize: 13 }}>Loading…</div>
            ) : (
              <>
                {FIELDS.map((field) => (
                  <div key={field.key}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 4 }}>
                      <label
                        htmlFor={field.key}
                        style={{ fontWeight: 500, fontSize: 13, minWidth: 180 }}
                      >
                        {field.label}
                      </label>
                      <input
                        id={field.key}
                        className="input"
                        type="number"
                        step={field.step}
                        min={field.min}
                        max={field.max}
                        value={form[field.key] as number}
                        onChange={(e) =>
                          setForm((prev) => ({ ...prev, [field.key]: parseFloat(e.target.value) }))
                        }
                        style={{ width: 100 }}
                      />
                      {thresholds && form[field.key] !== thresholds[field.key] && (
                        <span style={{ fontSize: 11, color: "var(--fg-dim)", fontFamily: "Geist Mono, monospace" }}>
                          was {thresholds[field.key]}
                        </span>
                      )}
                    </div>
                    <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0, paddingLeft: 192 }}>
                      {field.description}
                    </p>
                  </div>
                ))}

                <p style={{
                  fontSize: 12, color: "var(--fg-dim)",
                  background: "var(--surface-2)", border: "1px solid var(--line)",
                  borderRadius: "var(--r)", padding: "8px 12px", margin: 0,
                }}>
                  Changes apply to the next quality scoring run. Existing scored images are not re-flagged automatically.
                </p>

                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    className="btn primary"
                    onClick={handleSave}
                    disabled={mutation.isPending || !isChanged}
                  >
                    {mutation.isPending ? "Saving…" : "Save"}
                  </button>
                  <button className="btn ghost" onClick={handleReset}>
                    Reset to defaults
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {activeTab === "versioning" && (
        <div className="panel">
          <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {isLoading ? (
              <div style={{ color: "var(--fg-mute)", fontSize: 13 }}>Loading…</div>
            ) : (
              <>
                <div>
                  <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 10 }}>Version control mode</div>
                  <RadioGroup
                    name="versioning_mode"
                    options={[
                      { value: "off", label: "Off", desc: "No version tracking. No disk space used." },
                      { value: "manual", label: "Manual snapshots", desc: "Snapshots only when you create them. All files are backed up at snapshot time (full point-in-time backup)." },
                      { value: "auto", label: "Automatic (copy-on-write)", desc: "Files are automatically backed up before any resize, upscale replace, or LUT replace. Lightweight snapshots; backups happen lazily on first overwrite." },
                    ]}
                    value={form.versioning_mode}
                    onChange={(v) => setForm((prev) => ({ ...prev, versioning_mode: v as "off" | "manual" | "auto" }))}
                  />
                  {thresholds && thresholds.versioning_mode !== "off" && form.versioning_mode === "off" && (
                    <p style={{ fontSize: 12, color: "var(--warn)", marginTop: 10, marginBottom: 0 }}>
                      Switching to Off preserves existing snapshots and the object store, but no new backups will be taken.
                    </p>
                  )}
                </div>

                <div>
                  <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 10 }}>Branch snapshot behavior</div>
                  <RadioGroup
                    name="branch_snapshot"
                    options={[
                      { value: "ask", label: "Ask before branching (Recommended)", desc: "Show a prompt when creating a new branch or switching branches, letting you choose whether to save a snapshot." },
                      { value: "auto", label: "Auto-create snapshots", desc: "Always create snapshots automatically when branching or switching, without asking." },
                    ]}
                    value={branchSnapshot}
                    onChange={(v) => {
                      setBranchSnapshot(v as "ask" | "auto");
                      localStorage.setItem(BRANCH_SNAPSHOT_KEY, v);
                      toast.success("Preference saved");
                    }}
                  />
                </div>

                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    className="btn primary"
                    onClick={handleSave}
                    disabled={mutation.isPending || !isChanged}
                  >
                    {mutation.isPending ? "Saving…" : "Save"}
                  </button>
                  <button className="btn ghost" onClick={handleReset}>
                    Reset to defaults
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
      {activeTab === "providers" && (
        <div className="panel">
          <div className="panel-h">
            <h3>OpenAI-Compatible Providers</h3>
            <div style={{ flex: 1 }} />
            <button className="btn ghost sm" onClick={openAddProvider}>+ Add Provider</button>
          </div>
          <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 0 }}>
            <p style={{ fontSize: 12.5, color: "var(--fg-mute)", margin: "0 0 12px" }}>
              Configure local servers (llama.cpp, LM Studio, kobold.cpp) or remote APIs (OpenAI, Groq, OpenRouter). All use the OpenAI-compatible API format.
            </p>
            <div style={{ fontSize: 12, color: "var(--fg-mute)", background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r)", padding: "10px 12px", marginBottom: 16, lineHeight: 1.7 }}>
              <div style={{ fontWeight: 500, color: "var(--fg)", marginBottom: 4, fontSize: 12 }}>Quick setup</div>
              <div><strong style={{ color: "var(--fg)" }}>LM Studio</strong> — open the Local Server tab, start the server, use <code style={{ fontSize: 11 }}>http://localhost:1234/v1</code> — no API key needed</div>
              <div><strong style={{ color: "var(--fg)" }}>llama.cpp</strong> — run <code style={{ fontSize: 11 }}>llama-server -m model.gguf --port 8080</code>, use <code style={{ fontSize: 11 }}>http://localhost:8080/v1</code> — no API key needed</div>
              <div><strong style={{ color: "var(--fg)" }}>Gemini</strong> — get a free API key at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>aistudio.google.com</a>, use <code style={{ fontSize: 11 }}>https://generativelanguage.googleapis.com/v1beta/openai</code></div>
              <div><strong style={{ color: "var(--fg)" }}>OpenAI</strong> — get an API key at <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>platform.openai.com/api-keys</a>, use <code style={{ fontSize: 11 }}>https://api.openai.com/v1</code></div>
              <div><strong style={{ color: "var(--fg)" }}>Groq</strong> — get a free API key at <a href="https://console.groq.com/keys" target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>console.groq.com/keys</a>, use <code style={{ fontSize: 11 }}>https://api.groq.com/openai/v1</code></div>
            </div>

            {providers.length === 0 && !showProviderForm && (
              <div className="empty-state" style={{ padding: "32px 20px" }}>
                <span style={{ color: "var(--fg-dim)", fontSize: 12.5 }}>No providers configured. Add one to use OpenAI-compatible models in Captioning.</span>
              </div>
            )}

            {providers.map((p) => (
              <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 500, fontSize: 13 }}>{p.name}</span>
                    <span className={`badge dot ${p.is_remote ? "warn" : "good"}`} style={{ fontSize: 10 }}>
                      {p.is_remote ? "Remote" : "Local"}
                    </span>
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--fg-mute)", marginTop: 2 }}>
                    {p.base_url}
                    {p.default_model && <span style={{ marginLeft: 8, color: "var(--fg-dim)" }}>{p.default_model}</span>}
                  </div>
                  {p.api_key_masked && p.api_key_masked !== "" && (
                    <div style={{ fontSize: 11, color: "var(--fg-dim)", fontFamily: "Geist Mono, monospace", marginTop: 2 }}>
                      key: {p.api_key_masked}
                    </div>
                  )}
                </div>
                <button className="btn ghost sm" onClick={() => openEditProvider(p)}>Edit</button>
                <button className="btn ghost sm" style={{ color: "var(--bad)" }} onClick={() => setDeletingProvider(p)}>Delete</button>
              </div>
            ))}

            {showProviderForm && (
              <div style={{ marginTop: 16, padding: "16px", background: "var(--surface-2)", borderRadius: "var(--r)", border: "1px solid var(--line)" }}>
                <h4 style={{ margin: "0 0 12px", fontSize: 13 }}>{editingProviderId ? "Edit Provider" : "New Provider"}</h4>
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <div className="form-row" style={{ gap: 8 }}>
                    <label style={{ fontSize: 12.5, minWidth: 120 }}>Name</label>
                    <input className="input" placeholder="LM Studio" value={providerForm.name} onChange={(e) => setProviderForm((f) => ({ ...f, name: e.target.value }))} style={{ flex: 1 }} />
                  </div>
                  <div className="form-row" style={{ gap: 8 }}>
                    <label style={{ fontSize: 12.5, minWidth: 120 }}>Base URL</label>
                    <input className="input" placeholder="http://localhost:1234/v1" value={providerForm.base_url} onChange={(e) => setProviderForm((f) => ({ ...f, base_url: e.target.value }))} style={{ flex: 1 }} />
                  </div>
                  {providerForm.base_url && !/localhost|127\.0\.0\.1/.test(providerForm.base_url) && (
                    <p style={{ fontSize: 11.5, color: "var(--warn)", margin: "0 0 4px" }}>
                      Remote URL — images will be sent to this server during captioning.
                    </p>
                  )}
                  <div className="form-row" style={{ gap: 8 }}>
                    <label style={{ fontSize: 12.5, minWidth: 120 }}>API Key</label>
                    <input
                      className="input" type="password"
                      placeholder={editingProviderId ? "Leave blank to keep existing key" : "sk-… (or leave blank for local servers)"}
                      value={providerForm.api_key}
                      onChange={(e) => setProviderForm((f) => ({ ...f, api_key: e.target.value }))}
                      style={{ flex: 1 }}
                    />
                  </div>
                  <div className="form-row" style={{ gap: 8 }}>
                    <label style={{ fontSize: 12.5, minWidth: 120 }}>Default model</label>
                    <ModelPicker
                      value={providerForm.default_model ?? ""}
                      onChange={(v) => setProviderForm((f) => ({ ...f, default_model: v }))}
                      providerId={editingProviderId ?? undefined}
                      baseUrl={providerForm.base_url}
                      placeholder="e.g. gpt-4o-mini or llava:latest"
                    />
                  </div>
                  <div className="form-row" style={{ gap: 8 }}>
                    <label style={{ fontSize: 12.5, minWidth: 120 }}>Max image size</label>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <input
                        type="range" min={256} max={2048} step={128}
                        value={providerForm.max_image_px}
                        onChange={(e) => setProviderForm((f) => ({ ...f, max_image_px: parseInt(e.target.value) }))}
                        style={{ width: 140 }}
                      />
                      <span className="mono" style={{ fontSize: 12, minWidth: 60 }}>{providerForm.max_image_px}px</span>
                    </div>
                  </div>
                  <div className="form-row" style={{ gap: 8 }}>
                    <div style={{ minWidth: 120 }}>
                      <label style={{ fontSize: 12.5 }}>Max tokens</label>
                      <p style={{ fontSize: 11, color: "var(--fg-mute)", margin: "2px 0 0" }}>Increase for reasoning models (Gemma 4, QwQ, DeepSeek-R1) which use extra tokens for thinking.</p>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <input
                        type="range" min={256} max={16384} step={256}
                        value={providerForm.max_tokens ?? 2048}
                        onChange={(e) => setProviderForm((f) => ({ ...f, max_tokens: parseInt(e.target.value) }))}
                        style={{ width: 140 }}
                      />
                      <span className="mono" style={{ fontSize: 12, minWidth: 48 }}>{providerForm.max_tokens ?? 2048}</span>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                    <button className="btn primary sm" onClick={handleSaveProvider} disabled={createProviderMutation.isPending || updateProviderMutation.isPending}>
                      {(createProviderMutation.isPending || updateProviderMutation.isPending) ? "Saving…" : editingProviderId ? "Update" : "Add Provider"}
                    </button>
                    <button className="btn ghost sm" onClick={() => { setShowProviderForm(false); setEditingProviderId(null); }}>Cancel</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {deletingProvider && (
        <ConfirmDialog
          title={`Delete "${deletingProvider.name}"?`}
          message="This provider will be removed from Crucible. Any captioning jobs using this provider will fail."
          confirmLabel="Delete"
          danger
          onConfirm={() => deleteProviderMutation.mutate(deletingProvider.id)}
          onCancel={() => setDeletingProvider(null)}
        />
      )}
    </div>
  );
}
