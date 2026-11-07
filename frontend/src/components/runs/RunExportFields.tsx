import { useState } from "react";
import toast from "react-hot-toast";
import { FolderOpen } from "lucide-react";
import DirPickerModal from "../common/DirPickerModal";
import { FLAG_OPTIONS } from "../../constants/flags";
import { EXPORT_FILTERS_PREFIX, EXPORT_WORKFLOW_KEY } from "../../constants/storage";
import {
  EXPORT_FILTERS_DEFAULTS, EXPORT_WORKFLOW_DEFAULTS, FORMAT_LABELS,
  type CaptionFmt, type Format,
} from "../../constants/exportWorkflow";
import NumberField from "../common/NumberField";
import { clampExportResize, customResizeValue, EXPORT_RESIZE_MAX, EXPORT_RESIZE_MIN, type ExportForm } from "./runForms";
import { datasetScopedKey, loadPersisted } from "../../utils/persistentState";

/** The export stage's settings on the Runs page. Loss masks, license and label filters
 *  and the subfolder limit stay on the Export page for now; the form says so. */
export default function RunExportFields({ value, onChange, datasetId }: {
  value: ExportForm;
  onChange: (v: ExportForm) => void;
  datasetId: string;
}) {
  const set = <K extends keyof ExportForm>(k: K, v: ExportForm[K]) => onChange({ ...value, [k]: v });
  const [dirPicker, setDirPicker] = useState(false);

  function fillFromExportPage() {
    // The Export page keeps its settings in this browser: the workflow under one key and
    // the filters per dataset.
    const w = loadPersisted(EXPORT_WORKFLOW_KEY, EXPORT_WORKFLOW_DEFAULTS);
    const f = loadPersisted(datasetScopedKey(EXPORT_FILTERS_PREFIX, datasetId), EXPORT_FILTERS_DEFAULTS);
    onChange({
      format: w.format,
      captionFmt: w.captionFmt,
      outputDir: w.outputDir || value.outputDir,
      nRepeats: w.nRepeats,
      conceptToken: w.conceptToken,
      outputImgFmt: w.outputImgFmt,
      resizeTo: w.resizeTo,
      customResize: w.customResize,
      customResizeVal: w.customResizeVal,
      stripMetadata: w.stripMetadata,
      captionsOnly: w.captionsOnly,
      filterAesthetic: f.filterAesthetic,
      aestheticMin: f.aestheticMin,
      filterCaptioned: f.filterCaptioned,
      excludeFlags: f.excludeFlags,
      filterStyleSim: f.filterStyleSim,
      styleSimMin: f.styleSimMin,
    });
    toast.success("Filled from the Export page");
  }

  return (
    <>
      <div className="form-row">
        <div className="lbl-col">
          <h4>Export format</h4>
          <p>Exports the whole dataset through these settings. Loss masks, license and label filters stay on the Export page.</p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <button className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={fillFromExportPage}>
            Fill from the Export page
          </button>
          <div className="row-flex" style={{ flexWrap: "wrap" }}>
            {(["kohya", "aitoolkit", "plain"] as Format[]).map((k) => (
              <button key={k} className={`btn sm${value.format === k ? " primary" : ""}`} onClick={() => set("format", k)}>{FORMAT_LABELS[k]}</button>
            ))}
          </div>
        </div>
      </div>

      <div className="form-row">
        <div className="lbl-col"><h4>Output directory</h4><p>An absolute path.</p></div>
        <div className="row-flex" style={{ gap: 6 }}>
          <input className="input" style={{ flex: 1 }} placeholder="Output folder (absolute path)" value={value.outputDir}
            onChange={(e) => set("outputDir", e.target.value)} />
          <button className="btn sm ghost" onClick={() => setDirPicker(true)} title="Browse"><FolderOpen size={14} /></button>
        </div>
        {dirPicker && (
          <DirPickerModal initialPath={value.outputDir}
            onConfirm={(p) => { set("outputDir", p); setDirPicker(false); }}
            onCancel={() => setDirPicker(false)} />
        )}
      </div>

      {value.format !== "plain" && (
        <div className="form-row">
          <div className="lbl-col"><h4>Concept</h4><p>{value.format === "kohya" ? "Repeats and the concept token in the folder name." : "The concept name."}</p></div>
          <div className="row-flex" style={{ gap: 8 }}>
            {value.format === "kohya" && (
              <label className="row-flex" style={{ gap: 6 }}>
                <span style={{ fontSize: 12.5 }}>Repeats</span>
                <input type="number" className="input" min={1} max={1000} value={value.nRepeats} style={{ width: 70 }}
                  onChange={(e) => set("nRepeats", Math.max(1, Math.min(1000, Number(e.target.value) || 1)))} />
              </label>
            )}
            <input className="input" value={value.conceptToken} maxLength={200} onChange={(e) => set("conceptToken", e.target.value)} />
          </div>
        </div>
      )}

      {value.format !== "plain" && (
        <div className="form-row">
          <div className="lbl-col"><h4>Caption file</h4><p>How captions are written to disk.</p></div>
          <div className="row-flex">
            {([["txt", ".txt sidecar"], ["caption", ".caption sidecar"], ["jsonl", "JSONL manifest"]] as [CaptionFmt, string][]).map(([v, label]) => (
              <button key={v} className={`btn sm${value.captionFmt === v ? " primary" : ""}`} onClick={() => set("captionFmt", v)}>{label}</button>
            ))}
          </div>
        </div>
      )}

      <div className="form-row">
        <div className="lbl-col"><h4>Filters</h4><p>Leave out images that don't meet the criteria.</p></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
          <label className="row-flex" style={{ gap: 8 }}>
            <input type="checkbox" className="checkbox" checked={value.filterAesthetic} onChange={(e) => set("filterAesthetic", e.target.checked)} />
            <span style={{ fontSize: 12.5 }}>Aesthetic ≥</span>
            <input type="number" className="input" step={0.5} min={1} max={10} value={value.aestheticMin}
              onChange={(e) => set("aestheticMin", Number(e.target.value))} disabled={!value.filterAesthetic} style={{ width: 64, textAlign: "center" }} />
          </label>
          <label className="row-flex" style={{ gap: 8 }}>
            <input type="checkbox" className="checkbox" checked={value.filterCaptioned} onChange={(e) => set("filterCaptioned", e.target.checked)} />
            <span style={{ fontSize: 12.5 }}>Has caption</span>
          </label>
          <label className="row-flex" style={{ gap: 8 }}>
            <input type="checkbox" className="checkbox" checked={value.filterStyleSim} onChange={(e) => set("filterStyleSim", e.target.checked)} />
            <span style={{ fontSize: 12.5 }}>Style similarity ≥</span>
            <input type="number" className="input" step={0.05} min={0} max={1} value={value.styleSimMin}
              onChange={(e) => set("styleSimMin", Number(e.target.value))} disabled={!value.filterStyleSim} style={{ width: 64, textAlign: "center" }} />
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {FLAG_OPTIONS.map((opt) => (
              <label key={opt.key} className="row-flex" style={{ gap: 6, fontSize: 12.5 }}>
                <input type="checkbox" className="checkbox" checked={value.excludeFlags.includes(opt.key)}
                  onChange={(e) => set("excludeFlags", e.target.checked
                    ? [...value.excludeFlags, opt.key]
                    : value.excludeFlags.filter((k) => k !== opt.key))} />
                <span>Leave out {opt.label.toLowerCase()}</span>
              </label>
            ))}
          </div>
        </div>
      </div>

      <div className={`form-row${value.captionsOnly ? " disabled" : ""}`}>
        <div className="lbl-col"><h4>Resize on export</h4><p>Scales the longest side down. No cropping, no upscaling; originals are never modified.</p></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="row-flex">
            {[512, 768, 1024].map((r) => (
              <button key={r} className={`btn sm${!value.customResize && value.resizeTo === r ? " primary" : ""}`}
                onClick={() => onChange({ ...value, resizeTo: r, customResize: false })}>{r}</button>
            ))}
            <button className={`btn sm${value.customResize ? " primary" : ""}`} onClick={() => onChange({ ...value, customResize: true, customResizeVal: value.customResizeVal || "1024" })}>Custom</button>
            <button className={`btn sm${!value.customResize && value.resizeTo === null ? " primary" : ""}`}
              onClick={() => onChange({ ...value, resizeTo: null, customResize: false })}>No resize</button>
          </div>
          {value.customResize && (
            <NumberField className="input" min={EXPORT_RESIZE_MIN} max={EXPORT_RESIZE_MAX} aria-label="Custom resize"
              placeholder="e.g. 1536" style={{ width: 110 }}
              value={customResizeValue(value)} clamp={clampExportResize}
              onCommit={(n) => set("customResizeVal", String(n))} />
          )}
        </div>
      </div>

      <div className="form-row">
        <div className="lbl-col"><h4>Image format</h4><p>Convert images when copying to the export folder.</p></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="row-flex">
            {[["original", "Keep original"], ["png", "Force PNG"], ["jpeg", "Force JPEG"]].map(([v, label]) => (
              <button key={v} className={`btn sm${value.outputImgFmt === v ? " primary" : ""}`} onClick={() => set("outputImgFmt", v)}>{label}</button>
            ))}
          </div>
          <label className="row-flex" style={{ gap: 8 }}>
            <input type="checkbox" className="checkbox" checked={value.stripMetadata} onChange={(e) => set("stripMetadata", e.target.checked)} />
            <span style={{ fontSize: 12.5 }}>Strip generation metadata</span>
          </label>
          <label className="row-flex" style={{ gap: 8 }}>
            <input type="checkbox" className="checkbox" checked={value.captionsOnly} onChange={(e) => set("captionsOnly", e.target.checked)} />
            <span style={{ fontSize: 12.5 }}>Captions only (no image files)</span>
          </label>
        </div>
      </div>
    </>
  );
}
