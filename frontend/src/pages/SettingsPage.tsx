import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { settingsApi, type Thresholds } from "../api/settings";
import { CONFIRM_DEFAULT_KEY } from "../constants/storage";
import RadioGroup from "../components/common/RadioGroup";

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

  return (
    <div style={{ padding: "28px 32px", maxWidth: 640 }}>
      <h1 style={{ fontSize: 18, fontWeight: 600, marginBottom: 4 }}>Settings</h1>
      <p style={{ color: "var(--fg-mute)", fontSize: 13, marginBottom: 28 }}>
        Global configuration for this Crucible instance.
      </p>

      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-h" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>Quality Flag Thresholds</span>
        </div>
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

      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-h">
          <span style={{ fontWeight: 600, fontSize: 13 }}>UI Behavior</span>
        </div>
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

      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-h">
          <span style={{ fontWeight: 600, fontSize: 13 }}>Versioning</span>
        </div>
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
    </div>
  );
}
