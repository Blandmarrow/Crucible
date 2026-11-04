import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { captioningApi } from "../../api/captioning";
import { providersApi } from "../../api/providers";
import { appDataApi } from "../../api/appData";
import { captionBackend, captionModelOptions } from "../../constants/captionModels";
import { STYLE_LABELS, modelType } from "../../constants/captionStyles";
import { CAPTIONING_WORKFLOW_DEFAULTS } from "../../constants/captioningWorkflow";
import { captionModelId, type CaptionForm } from "./runForms";
import { FLAG_OPTIONS } from "../../constants/flags";
import { usePromptPresets } from "../../hooks/usePromptPresets";
import DelimiterControls from "../caption/DelimiterControls";
import ResolutionPicker from "../caption/ResolutionPicker";
import ModelPicker from "../providers/ModelPicker";

const OPTION_ROWS: { key: keyof CaptionForm; label: string }[] = [
  { key: "stripRefusals", label: "Strip refusal phrases & identity guesses" },
  { key: "stripThinking", label: "Strip thinking blocks (<think>…</think> and reasoning preambles)" },
  { key: "stripUnderscores", label: "Normalise underscores in prose (word_word → word word)" },
  { key: "stripHedges", label: "Strip hedging phrases (\"It appears to be…\" → \"…\")" },
  { key: "dedupeTags", label: "Merge redundant tags (drop \"tail\" when \"long tail\" present; tag styles only)" },
  { key: "saveBackup", label: "Save backup of previous caption to .caption.bak" },
  { key: "renameOnCaption", label: "Rename files using subfolder name and increment" },
];

/** The caption stage's settings on the Runs page: the Captioning page's fields, minus
 *  scope (the run captions only its own images) and the pipeline steps. */
export default function RunCaptionFields({ value, onChange }: { value: CaptionForm; onChange: (v: CaptionForm) => void }) {
  const set = <K extends keyof CaptionForm>(k: K, v: CaptionForm[K]) => onChange({ ...value, [k]: v });
  const [filling, setFilling] = useState(false);

  const { data: modelsData } = useQuery({ queryKey: ["captioning-models"], queryFn: captioningApi.models });
  const { data: providers = [] } = useQuery({ queryKey: ["providers"], queryFn: providersApi.list, staleTime: 30_000 });
  const { presets, emptyMessage: presetsEmptyMessage } = usePromptPresets();

  const options = useMemo(() => captionModelOptions(modelsData, providers), [modelsData, providers]);
  const groups = useMemo(() => {
    const out = new Map<string, typeof options>();
    for (const o of options) out.set(o.group, [...(out.get(o.group) ?? []), o]);
    return [...out.entries()];
  }, [options]);

  const isWd14 = value.base.startsWith("wd14:");
  const selectedProvider = value.base.startsWith("openai_compat:")
    ? providers.find((p) => value.base === `openai_compat:${p.id}`)
    : undefined;
  const type = modelType(captionModelId(value));
  const styles = isWd14 ? [] : (type ? (STYLE_LABELS[type] ?? []) : []);

  function chooseModel(base: string) {
    const p = providers.find((pr) => base === `openai_compat:${pr.id}`);
    const nextType = modelType(base);
    const nextStyles = nextType ? (STYLE_LABELS[nextType] ?? []) : [];
    onChange({
      ...value,
      base,
      providerModel: p?.default_model ?? "",
      style: nextStyles.length === 0 || nextStyles.includes(value.style) ? value.style : nextStyles[0],
    });
  }

  async function fillFromCaptioningPage() {
    setFilling(true);
    try {
      const saved = await appDataApi.getCaptioningWorkflow();
      if (!saved) {
        toast("The Captioning page has nothing saved yet");
        return;
      }
      const w = { ...CAPTIONING_WORKFLOW_DEFAULTS, ...saved };
      // Model, prompt and post-processing only. The page's scope (uncaptioned / all) does
      // not apply to a run, which captions its own images, so `overwrite` stays as it is.
      onChange({
        ...value,
        base: w.selectedModel,
        providerModel: w.providerModelInput,
        style: w.style,
        customPrompt: w.customPrompt,
        wd14Threshold: w.wd14Threshold,
        targetWidth: w.targetWidth,
        targetHeight: w.targetHeight,
        delimiterMode: w.delimiterMode,
        delimiterParts: w.delimiterParts,
        stripRefusals: w.stripRefusals,
        stripThinking: w.stripThinking,
        stripUnderscores: w.stripUnderscores,
        stripHedges: w.stripHedges,
        dedupeTags: w.dedupeTags,
        saveBackup: w.saveBackup,
        renameOnCaption: w.renameOnCaption,
      });
      toast.success("Filled from the Captioning page");
    } catch {
      toast.error("Could not read the Captioning page's settings");
    } finally {
      setFilling(false);
    }
  }

  const model = captionModelId(value);
  return (
    <>
      <div className="form-row">
        <div className="lbl-col">
          <h4>Caption model</h4>
          <p>Only this run's own images are captioned.</p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <button className="btn ghost sm" style={{ alignSelf: "flex-start" }} disabled={filling} onClick={fillFromCaptioningPage}>
            Fill from the Captioning page
          </button>
          <select className="select" value={value.base} onChange={(e) => chooseModel(e.target.value)}>
            <option value="">— choose a model —</option>
            {groups.map(([group, opts]) => (
              <optgroup key={group} label={group}>
                {opts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </optgroup>
            ))}
            {value.base && !options.some((o) => o.id === value.base) && <option value={value.base}>{value.base}</option>}
          </select>
          {selectedProvider && (
            <ModelPicker
              key={selectedProvider.id}
              value={value.providerModel}
              onChange={(v) => set("providerModel", v)}
              providerId={selectedProvider.id}
              baseUrl={selectedProvider.base_url}
              placeholder={`Model name (default: ${selectedProvider.default_model || "none"})`}
            />
          )}
          {model && captionBackend(model) === null && (
            <span style={{ color: "var(--bad)", fontSize: 11.5 }}>“{model}” is not a captioning model.</span>
          )}
        </div>
      </div>

      {isWd14 && (
        <div className="form-row">
          <div className="lbl-col"><h4>Tag threshold</h4><p>Minimum confidence (0–1) for a tag to be included.</p></div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <input type="range" min={0} max={1} step={0.05} value={value.wd14Threshold}
              onChange={(e) => set("wd14Threshold", parseFloat(e.target.value))} style={{ flex: 1 }} />
            <span className="mono" style={{ fontSize: 13, minWidth: 36 }}>{value.wd14Threshold.toFixed(2)}</span>
          </div>
        </div>
      )}

      {styles.length > 0 && (
        <div className="form-row">
          <div className="lbl-col"><h4>Style</h4><p>Output format for the generated caption.</p></div>
          <div className="row-flex" style={{ flexWrap: "wrap" }}>
            {styles.map((s) => (
              <button key={s} className={`btn sm${value.style === s ? " primary" : ""}`} onClick={() => set("style", s)}>{s}</button>
            ))}
          </div>
        </div>
      )}

      {!isWd14 && (
        <div className="form-row">
          <div className="lbl-col"><h4>Prompt</h4><p>Instructions for the model. Used by Ollama and OpenAI-compatible models.</p></div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <textarea className="input" style={{ height: 90 }} value={value.customPrompt}
              onChange={(e) => set("customPrompt", e.target.value)} placeholder="Leave blank to use the style preset prompt…" />
            {presets.length === 0 ? (
              <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>{presetsEmptyMessage}</span>
            ) : (
              <div className="row-flex" style={{ flexWrap: "wrap", gap: 6 }}>
                <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>Presets:</span>
                {presets.map((p) => (
                  <button key={p.id} className="btn ghost sm" title={p.prompt}
                    onClick={() => onChange({ ...value, customPrompt: p.prompt, style: p.style })}>
                    {p.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="form-row">
        <div className="lbl-col"><h4>Target resolution</h4><p>Center-crop &amp; resize before inference.</p></div>
        <ResolutionPicker targetWidth={value.targetWidth} targetHeight={value.targetHeight}
          onChange={(w, h) => onChange({ ...value, targetWidth: w, targetHeight: h })} />
      </div>

      <div className="form-row">
        <div className="lbl-col"><h4>Existing captions</h4><p>The run's images already hold their prompt as a caption.</p></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <label className="row-flex" style={{ gap: 8 }}>
            <input type="checkbox" className="checkbox" checked={value.overwrite} onChange={(e) => set("overwrite", e.target.checked)} />
            <span style={{ fontSize: 12.5 }}>Caption images that already have a caption</span>
          </label>
          <DelimiterControls mode={value.delimiterMode} delimiterParts={value.delimiterParts}
            onChange={(m, parts) => onChange({ ...value, delimiterMode: m, delimiterParts: parts })} />
        </div>
      </div>

      <div className="form-row">
        <div className="lbl-col"><h4>Quality filters</h4><p>Skip images below quality criteria.</p></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <label style={{ fontSize: 12.5, color: "var(--fg-mute)" }}>Min aesthetic score</label>
            <input type="number" className="input" style={{ width: 80 }} placeholder="None" min={0} max={10} step={0.5}
              value={value.minAesthetic} onChange={(e) => set("minAesthetic", e.target.value)} />
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {FLAG_OPTIONS.map((opt) => (
              <label key={opt.key} className="row-flex" style={{ gap: 6, fontSize: 12.5 }}>
                <input type="checkbox" className="checkbox" checked={value.excludeFlags.includes(opt.key)}
                  onChange={(e) => set("excludeFlags", e.target.checked
                    ? [...value.excludeFlags, opt.key]
                    : value.excludeFlags.filter((k) => k !== opt.key))} />
                <span>Skip {opt.label.toLowerCase()}</span>
              </label>
            ))}
          </div>
        </div>
      </div>

      <div className="form-row">
        <div className="lbl-col"><h4>Options</h4><p>Post-processing applied to each caption.</p></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {OPTION_ROWS.map((o) => (
            <label key={o.key} className="row-flex" style={{ gap: 8 }}>
              <input type="checkbox" className="checkbox" checked={value[o.key] as boolean}
                onChange={(e) => set(o.key, e.target.checked as CaptionForm[typeof o.key])} />
              <span style={{ fontSize: 12.5 }}>{o.label}</span>
            </label>
          ))}
        </div>
      </div>
    </>
  );
}
