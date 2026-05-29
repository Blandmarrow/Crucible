import { useState, useEffect, useMemo } from "react";
import { providersApi } from "../../api/providers";
import { getPresetsForUrl } from "../../constants/providerPresets";

interface ModelPickerProps {
  value: string;
  onChange: (v: string) => void;
  providerId?: string;
  baseUrl?: string;
  placeholder?: string;
}

const CUSTOM_SENTINEL = "__custom__";

export default function ModelPicker({ value, onChange, providerId, baseUrl, placeholder }: ModelPickerProps) {
  const [fetchedModels, setFetchedModels] = useState<string[]>([]);
  const [isFetching, setIsFetching] = useState(false);
  const [fetchError, setFetchError] = useState(false);
  const [showCustom, setShowCustom] = useState(false);

  const presets = useMemo(() => getPresetsForUrl(baseUrl ?? ""), [baseUrl]);

  const allModels = useMemo(() => {
    const seen = new Set<string>();
    return [...presets, ...fetchedModels].filter((id) => {
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }, [presets, fetchedModels]);

  // When baseUrl changes, clear fetched models
  useEffect(() => {
    setFetchedModels([]);
    setFetchError(false);
  }, [baseUrl]);

  // Reset custom mode when value becomes a known model (e.g. parent switches provider)
  useEffect(() => {
    if (allModels.includes(value)) setShowCustom(false);
  }, [value, allModels]);

  // Auto-fetch on mount when we have a provider ID
  useEffect(() => {
    if (!providerId) return;
    let cancelled = false;
    setIsFetching(true);
    setFetchError(false);
    providersApi.fetchModels(providerId).then((models) => {
      if (cancelled) return;
      if (models.length > 0) setFetchedModels(models);
      // silence empty-result — presets still show
    }).catch(() => {
      // silence errors on auto-fetch — user can retry manually
    }).finally(() => {
      if (!cancelled) setIsFetching(false);
    });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId]);

  const isCustom = allModels.length > 0 && value !== "" && !allModels.includes(value);

  async function handleFetch() {
    if (!providerId) return;
    setIsFetching(true);
    setFetchError(false);
    try {
      const models = await providersApi.fetchModels(providerId);
      if (models.length === 0) {
        setFetchError(true);
      } else {
        setFetchedModels(models);
        setFetchError(false);
      }
    } catch {
      setFetchError(true);
    } finally {
      setIsFetching(false);
    }
  }

  function handleSelectChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const v = e.target.value;
    if (v === CUSTOM_SENTINEL) {
      setShowCustom(true);
    } else {
      setShowCustom(false);
      onChange(v);
    }
  }

  // No known models — plain text input
  if (allModels.length === 0) {
    return (
      <div style={{ display: "flex", gap: 6, flex: 1, minWidth: 0 }}>
        <input
          className="input"
          placeholder={placeholder ?? "Model name"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          style={{ flex: 1, minWidth: 0 }}
        />
        {providerId && (
          <button
            className="btn ghost sm"
            title="Fetch available models from provider"
            onClick={handleFetch}
            disabled={isFetching}
            style={{ flexShrink: 0, fontSize: 13 }}
          >
            {isFetching ? "…" : "↻"}
          </button>
        )}
        {fetchError && (
          <span style={{ fontSize: 11, color: "var(--fg-mute)", alignSelf: "center", whiteSpace: "nowrap" }}>
            Could not reach provider
          </span>
        )}
      </div>
    );
  }

  // Known models — show select + optional custom input (value used directly, no sync state needed)
  const selectValue = (isCustom || showCustom) ? CUSTOM_SENTINEL : (value || "");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
      <div style={{ display: "flex", gap: 6, minWidth: 0 }}>
        <select
          className="select"
          value={selectValue}
          onChange={handleSelectChange}
          style={{ flex: 1, minWidth: 0, overflow: "hidden" }}
        >
          {!value && <option value="">— select model —</option>}
          {allModels.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
          <option value={CUSTOM_SENTINEL}>Custom…</option>
        </select>
        {providerId && (
          <button
            className="btn ghost sm"
            title="Refresh model list from provider"
            onClick={handleFetch}
            disabled={isFetching}
            style={{ flexShrink: 0, fontSize: 13 }}
          >
            {isFetching ? "…" : "↻"}
          </button>
        )}
      </div>
      {(isCustom || showCustom) && (
        <input
          className="input"
          placeholder={placeholder ?? "Enter model name"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          style={{ flex: 1, minWidth: 0 }}
          autoFocus
        />
      )}
      {fetchError && (
        <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>Could not reach provider</span>
      )}
    </div>
  );
}
