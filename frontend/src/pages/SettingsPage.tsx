import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { settingsApi, type Thresholds } from "../api/settings";
import { providersApi, type ProviderOut, type ProviderCreate } from "../api/providers";
import { CONFIRM_DEFAULT_KEY, BRANCH_SNAPSHOT_KEY, GALLERY_PAGE_SIZE_KEY, SUBFOLDER_RENAME_KEY, getGalleryPageSize } from "../constants/storage";
import RadioGroup from "../components/common/RadioGroup";
import ConfirmDialog from "../components/common/ConfirmDialog";
import ModelPicker from "../components/providers/ModelPicker";

const DEFAULTS: Thresholds = {
  blur_threshold: 100,
  noise_threshold: 15,
  uniformity_threshold: 12,
  duplicate_threshold: 8,
  watermark_threshold: 0.6,
  versioning_mode: "off",
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
    if (Object.keys(changed).length === 0) {
      toast("No changes to save", { icon: "ℹ️" });
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
      form.versioning_mode !== thresholds.versioning_mode);

  const [activeTab, setActiveTab] = useState<"gallery" | "ui" | "quality" | "versioning" | "providers">("gallery");

  // Providers state
  const { data: providers = [], refetch: refetchProviders } = useQuery({
    queryKey: ["providers"],
    queryFn: providersApi.list,
    staleTime: 30_000,
  });
  const [providerForm, setProviderForm] = useState<ProviderCreate & { id?: string }>({ name: "", base_url: "", api_key: "", default_model: "", max_image_px: 1024, max_tokens: 2048 });
  const [showProviderForm, setShowProviderForm] = useState(false);
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null);
  const [deletingProvider, setDeletingProvider] = useState<ProviderOut | null>(null);

  const createProviderMutation = useMutation({
    mutationFn: (body: ProviderCreate) => providersApi.create(body),
    onSuccess: () => { toast.success("Provider added"); refetchProviders(); qc.invalidateQueries({ queryKey: ["captioning-models"] }); setShowProviderForm(false); setEditingProviderId(null); },
    onError: (e: unknown) => toast.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "Failed to save provider"),
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
        <button className={`tab${activeTab === "ui" ? " active" : ""}`} onClick={() => setActiveTab("ui")}>UI Behavior</button>
        <button className={`tab${activeTab === "quality" ? " active" : ""}`} onClick={() => setActiveTab("quality")}>Quality Thresholds</button>
        <button className={`tab${activeTab === "versioning" ? " active" : ""}`} onClick={() => setActiveTab("versioning")}>Versioning</button>
        <button className={`tab${activeTab === "providers" ? " active" : ""}`} onClick={() => setActiveTab("providers")}>LLM Providers</button>
      </div>

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
                        value={form[field.key]}
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
