import { useState, useEffect, useMemo, useRef } from "react";
import { useLabels } from "../hooks/useLabels";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { exportApi } from "../api/export";
import type { ExportPreviewFilters } from "../api/export";
import { datasetsApi } from "../api/datasets";
import { detectionApi } from "../api/detection";
import { jobsApi } from "../api/jobs";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import type { SubfolderInfo } from "../types";
import DirPickerModal from "../components/common/DirPickerModal";
import LabelPicker from "../components/common/LabelPicker";
import { FolderOpen } from "lucide-react";
import { FLAG_OPTIONS } from "../constants/flags";
import { LICENSE_OPTIONS, OTHER_PREFIX, isKnownLicenseValue } from "../constants/licenses";
import { aestheticModelLabel } from "../constants/aestheticModels";
import { useCustomLicenses } from "../hooks/useCustomLicenses";
import { EXPORT_WORKFLOW_KEY, EXPORT_FILTERS_PREFIX } from "../constants/storage";
import { loadPersisted, clearPersisted, datasetScopedKey } from "../utils/persistentState";
import { useDebouncedPersist } from "../hooks/useDebouncedPersist";

type Format = "kohya" | "aitoolkit" | "plain";
type CaptionFmt = "txt" | "caption" | "jsonl";
type ResizeTo = number | null;
type MaskMissing = "white" | "skip";

const FORMAT_LABELS: Record<Format, string> = {
  kohya: "kohya",
  aitoolkit: "ai-toolkit",
  plain: "plain folder",
};

interface ExportWorkflow {
  format: Format;
  captionFmt: CaptionFmt;
  outputDir: string;
  nRepeats: number;
  conceptToken: string;
  outputImgFmt: string;
  resizeTo: ResizeTo;
  customResize: boolean;
  customResizeVal: string;
  stripMetadata: boolean;
  captionsOnly: boolean;
  exportMasks: boolean;
  maskInvert: boolean;
  maskMissing: MaskMissing;
}

const EXPORT_WORKFLOW_DEFAULTS: ExportWorkflow = {
  format: "kohya",
  captionFmt: "txt",
  outputDir: "",
  nRepeats: 10,
  conceptToken: "concept",
  outputImgFmt: "original",
  resizeTo: null,
  customResize: false,
  customResizeVal: "",
  stripMetadata: false,
  captionsOnly: false,
  exportMasks: false,
  maskInvert: false,
  maskMissing: "white",
};

interface ExportFilters {
  filterAesthetic: boolean;
  aestheticMin: number;
  filterCaptioned: boolean;
  excludeFlags: string[];
  filterStyleSim: boolean;
  styleSimMin: number;
  subfolderFilterActive: boolean;
  selectedSubfolders: string[];
  maskLabels: string[];
  maskExcludeLabels: string[];
  licenseFilter: string[];
  commercialOnly: boolean;
  excludeUnlicensed: boolean;
  excludeNoDerivatives: boolean;
  labelFilter: string[];
  labelMatch: "any" | "all";
  labelMissing: boolean;
}

const EXPORT_FILTERS_DEFAULTS: ExportFilters = {
  filterAesthetic: false,
  aestheticMin: 5.0,
  filterCaptioned: true,
  excludeFlags: ["has_watermark"],
  filterStyleSim: false,
  styleSimMin: 0.5,
  subfolderFilterActive: false,
  selectedSubfolders: [],
  maskLabels: [],
  maskExcludeLabels: [],
  licenseFilter: [],
  commercialOnly: false,
  excludeUnlicensed: false,
  excludeNoDerivatives: false,
  labelFilter: [],
  labelMatch: "any",
  labelMissing: false,
};

/** The **one** encoder for the label trio, shared by the three export POST bodies
 *  and the preview query.
 *
 *  Every value here is omitted when falsy, and that is the whole point:
 *  `label_missing: false` is not "no filter" but a meaningful *"only labelled
 *  images"* in the three-endpoint filter contract (`docs/dev/image-filters.md`),
 *  so sending a plain boolean default silently dropped every unlabelled image
 *  from every export while the preview — which stripped falsy — promised the full
 *  count. Two encodings of one thing is what caused that; there is now one. */
function labelParams(
  filter: Set<string>,
  match: "any" | "all",
  missing: boolean,
): Pick<ExportPreviewFilters, "label_filter" | "label_match" | "label_missing"> {
  return {
    ...(filter.size > 0 && { label_filter: [...filter] }),
    ...(filter.size > 1 && match === "all" && { label_match: "all" as const }),
    ...(missing && { label_missing: true }),
  };
}

export default function ExportPage() {
  const datasetId = usePaneDatasetId();

  // Remembered "workflow" config — global, shared across all datasets.
  const [workflow] = useState(() => loadPersisted(EXPORT_WORKFLOW_KEY, EXPORT_WORKFLOW_DEFAULTS));
  const [format, setFormat] = useState<Format>(workflow.format);
  const [captionFmt, setCaptionFmt] = useState<CaptionFmt>(workflow.captionFmt);
  const [outputDir, setOutputDir] = useState(workflow.outputDir);
  const [nRepeats, setNRepeats] = useState(workflow.nRepeats);
  const [conceptToken, setConceptToken] = useState(workflow.conceptToken);
  const [outputImgFmt, setOutputImgFmt] = useState(workflow.outputImgFmt);
  const [resizeTo, setResizeTo] = useState<ResizeTo>(workflow.resizeTo);
  const [customResize, setCustomResize] = useState(workflow.customResize);
  const [customResizeVal, setCustomResizeVal] = useState(workflow.customResizeVal);
  const [exportMasks, setExportMasks] = useState(workflow.exportMasks);
  const [maskInvert, setMaskInvert] = useState(workflow.maskInvert);
  const [maskMissing, setMaskMissing] = useState<MaskMissing>(workflow.maskMissing);
  const [dirPickerOpen, setDirPickerOpen] = useState(false);

  // Remembered "filters" config — per-dataset.
  const [filters] = useState(() =>
    datasetId ? loadPersisted(datasetScopedKey(EXPORT_FILTERS_PREFIX, datasetId), EXPORT_FILTERS_DEFAULTS) : EXPORT_FILTERS_DEFAULTS
  );
  const [filterAesthetic, setFilterAesthetic] = useState(filters.filterAesthetic);
  const [aestheticMin, setAestheticMin] = useState(filters.aestheticMin);
  const [filterCaptioned, setFilterCaptioned] = useState(filters.filterCaptioned);
  const [excludeFlags, setExcludeFlags] = useState<Set<string>>(new Set(filters.excludeFlags));
  // Labels. Ids, not names, so a rename in Settings does not silently void a
  // saved export preset; the bounds check below drops ids whose label is gone.
  const [labelFilter, setLabelFilter] = useState<Set<string>>(new Set(filters.labelFilter));
  const [labelMatch, setLabelMatch] = useState<"any" | "all">(filters.labelMatch === "all" ? "all" : "any");
  const [labelMissing, setLabelMissing] = useState(filters.labelMissing);
  // Bounds-checked the way GalleryPage checks its own restored filter: this comes
  // back from localStorage, possibly written by a build whose vocabulary has since
  // changed, and an unknown id silently filters the export to zero images with no
  // checkbox showing why.
  const [licenseFilter, setLicenseFilter] = useState<Set<string>>(
    () => new Set(filters.licenseFilter.filter(isKnownLicenseValue)),
  );
  // Free-text licenses recorded in this dataset — without them an `other:` license
  // can be neither selected nor excluded, which is a rights gap, not a cosmetic
  // one. A restored selection that is no longer in use keeps its row, or a filter
  // would be applied with no checkbox showing it.
  const customLicenses = useCustomLicenses(datasetId);
  const licenseFilterCustoms = useMemo(() => {
    const extra = [...licenseFilter].filter(
      (l) => l.toLowerCase().startsWith(OTHER_PREFIX) && !customLicenses.includes(l),
    );
    return [...customLicenses, ...extra];
  }, [customLicenses, licenseFilter]);
  const [commercialOnly, setCommercialOnly] = useState(filters.commercialOnly);
  const [excludeUnlicensed, setExcludeUnlicensed] = useState(filters.excludeUnlicensed);
  const [excludeNoDerivatives, setExcludeNoDerivatives] = useState(filters.excludeNoDerivatives);
  const [filterStyleSim, setFilterStyleSim] = useState(filters.filterStyleSim);
  const [styleSimMin, setStyleSimMin] = useState(filters.styleSimMin);
  const [subfolderFilterActive, setSubfolderFilterActive] = useState(filters.subfolderFilterActive);
  const [selectedSubfolders, setSelectedSubfolders] = useState<Set<string>>(new Set(filters.selectedSubfolders));
  // Selected detection labels for mask export; empty = all labels.
  const [maskLabels, setMaskLabels] = useState<Set<string>>(new Set(filters.maskLabels));
  // Labels whose regions are always painted black (overrides the include selection).
  const [maskExcludeLabels, setMaskExcludeLabels] = useState<Set<string>>(new Set(filters.maskExcludeLabels));

  const [stripMetadata, setStripMetadata] = useState(workflow.stripMetadata);
  const [captionsOnly, setCaptionsOnly] = useState(workflow.captionsOnly);
  const [jobLabel, setJobLabel] = useState("");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  useJobSSE(activeJobId);
  const jobProgress = useJobStore((s) => s.activeJobs.get(activeJobId ?? ""));

  const { data: subfolders = [] } = useQuery<SubfolderInfo[]>({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  // The global label vocabulary, for the picker below. `exportLabels` is the
  // same list `useLabels` hands the gallery — one cache entry app-wide.
  const { labels: exportLabels, byId: labelsById, isLoaded: labelsLoaded } = useLabels();

  // Bounds check, mirroring `licenseFilter` above and GalleryPage's: a persisted
  // preset can name a label deleted since it was saved, and an unknown id would
  // silently narrow the export to zero images with nothing showing why — the
  // picker itself is hidden while the vocabulary is empty, so nothing on screen
  // would explain it.
  //
  // Derived during render and gated on `labelsLoaded`, not latched behind a
  // mount-once ref: the ref never fired for an empty vocabulary, never fired
  // again when a label was deleted later in the session, and did not cover the
  // dataset-switch restore below, which re-seeds this state from a different blob.
  if (labelsLoaded && [...labelFilter].some((id) => !labelsById.has(id))) {
    setLabelFilter(new Set([...labelFilter].filter((id) => labelsById.has(id))));
  }

  const { data: detectionLabels = [] } = useQuery({
    queryKey: ["detection-labels", datasetId],
    queryFn: () => detectionApi.labels(datasetId!),
    enabled: !!datasetId && exportMasks,
  });

  // Debounced filter params for preview query
  const [debouncedFilters, setDebouncedFilters] = useState<ExportPreviewFilters>({
    aesthetic_min: null,
    captioned_only: filterCaptioned,
    exclude_flags: "has_watermark",
    style_sim_min: null,
    subfolders: null,
    export_masks: false,
    mask_labels: null,
    mask_exclude_labels: null,
    mask_missing: "white",
    license_filter: null,
    commercial_only: false,
    exclude_unlicensed: false,
    exclude_no_derivatives: false,
  });

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedFilters({
        aesthetic_min: filterAesthetic ? aestheticMin : null,
        captioned_only: filterCaptioned,
        exclude_flags: [...excludeFlags].join(","),
        style_sim_min: filterStyleSim ? styleSimMin : null,
        subfolders: subfolderFilterActive ? [...selectedSubfolders] : null,
        export_masks: exportMasks && !captionsOnly,
        mask_labels: maskLabels.size > 0 ? [...maskLabels] : null,
        mask_exclude_labels: maskExcludeLabels.size > 0 ? [...maskExcludeLabels] : null,
        mask_missing: maskMissing,
        license_filter: licenseFilter.size > 0 ? [...licenseFilter] : null,
        commercial_only: commercialOnly,
        exclude_unlicensed: excludeUnlicensed,
        exclude_no_derivatives: excludeNoDerivatives,
        ...labelParams(labelFilter, labelMatch, labelMissing),
      });
    }, 350);
    // Cancel, not flush: this timer only drives the preview query, which should
    // not fire for a state the user has already navigated away from. Persistence
    // was split out below precisely because it needs the opposite semantics.
    return () => clearTimeout(t);
  }, [datasetId, filterAesthetic, aestheticMin, filterCaptioned, excludeFlags, filterStyleSim, styleSimMin, subfolderFilterActive, selectedSubfolders, exportMasks, captionsOnly, maskLabels, maskExcludeLabels, maskMissing, licenseFilter, commercialOnly, excludeUnlicensed, excludeNoDerivatives, labelFilter, labelMatch, labelMissing]);

  // Persist "filters" config — per-dataset, debounced.
  useDebouncedPersist(
    datasetId ? datasetScopedKey(EXPORT_FILTERS_PREFIX, datasetId) : null,
    {
      filterAesthetic, aestheticMin, filterCaptioned,
      excludeFlags: [...excludeFlags],
      filterStyleSim, styleSimMin,
      subfolderFilterActive,
      selectedSubfolders: [...selectedSubfolders],
      maskLabels: [...maskLabels],
      maskExcludeLabels: [...maskExcludeLabels],
      licenseFilter: [...licenseFilter],
      commercialOnly,
      excludeUnlicensed,
      excludeNoDerivatives,
      labelFilter: [...labelFilter],
      labelMatch,
      labelMissing,
    },
  );

  // Persist the "workflow" config (format/output settings) — global, debounced.
  useDebouncedPersist(EXPORT_WORKFLOW_KEY, {
    format, captionFmt, outputDir, nRepeats, conceptToken, outputImgFmt,
    resizeTo, customResize, customResizeVal, stripMetadata, captionsOnly,
    exportMasks, maskInvert, maskMissing,
  });

  // Reload the "filters" blob when datasetId changes without a remount (pane mode).
  const prevDatasetId = useRef(datasetId);
  useEffect(() => {
    if (datasetId === prevDatasetId.current) return;
    prevDatasetId.current = datasetId;
    const next = datasetId
      ? loadPersisted(datasetScopedKey(EXPORT_FILTERS_PREFIX, datasetId), EXPORT_FILTERS_DEFAULTS)
      : EXPORT_FILTERS_DEFAULTS;
    setFilterAesthetic(next.filterAesthetic);
    setAestheticMin(next.aestheticMin);
    setFilterCaptioned(next.filterCaptioned);
    setExcludeFlags(new Set(next.excludeFlags));
    setFilterStyleSim(next.filterStyleSim);
    setStyleSimMin(next.styleSimMin);
    setSubfolderFilterActive(next.subfolderFilterActive);
    setSelectedSubfolders(new Set(next.selectedSubfolders));
    setMaskLabels(new Set(next.maskLabels));
    setMaskExcludeLabels(new Set(next.maskExcludeLabels));
    setLicenseFilter(new Set(next.licenseFilter.filter(isKnownLicenseValue)));
    setCommercialOnly(next.commercialOnly);
    setExcludeUnlicensed(next.excludeUnlicensed);
    setExcludeNoDerivatives(next.excludeNoDerivatives);
    setLabelFilter(new Set(next.labelFilter));
    setLabelMatch(next.labelMatch === "all" ? "all" : "any");
    setLabelMissing(next.labelMissing);
  }, [datasetId]);

  const { data: preview } = useQuery({
    queryKey: ["export-preview", datasetId, debouncedFilters],
    queryFn: () => exportApi.preview(datasetId!, debouncedFilters),
    enabled: !!datasetId,
  });

  const buildFilters = () => ({
    caption_format: captionFmt,
    resize_to: captionsOnly ? null : (customResize ? (parseInt(customResizeVal, 10) || null) : resizeTo),
    aesthetic_min: filterAesthetic ? aestheticMin : null,
    captioned_only: filterCaptioned,
    exclude_flags: [...excludeFlags].join(","),
    style_sim_min: filterStyleSim ? styleSimMin : null,
    subfolders: subfolderFilterActive ? [...selectedSubfolders] : null,
    strip_metadata: !captionsOnly && stripMetadata,
    captions_only: captionsOnly,
    export_masks: !captionsOnly && exportMasks,
    mask_labels: maskLabels.size > 0 ? [...maskLabels] : null,
    mask_exclude_labels: maskExcludeLabels.size > 0 ? [...maskExcludeLabels] : null,
    mask_invert: maskInvert,
    mask_missing: maskMissing,
    license_filter: licenseFilter.size > 0 ? [...licenseFilter] : null,
    commercial_only: commercialOnly,
    exclude_unlicensed: excludeUnlicensed,
    exclude_no_derivatives: excludeNoDerivatives,
    ...labelParams(labelFilter, labelMatch, labelMissing),
  });

  const exportMutation = useMutation({
    mutationFn: () => {
      const filters = buildFilters();
      const label = jobLabel.trim() || undefined;
      const common = { dataset_id: datasetId!, output_dir: outputDir, output_format: outputImgFmt, label, ...filters };
      if (format === "kohya") return exportApi.kohya({ ...common, n_repeats: nRepeats, concept_token: conceptToken });
      if (format === "aitoolkit") return exportApi.aitoolkit({ ...common, concept_name: conceptToken });
      return exportApi.plain(common);
    },
    onSuccess: (data) => { setActiveJobId(data.job_id); toast.success("Export started"); },
    onError: () => toast.error("Export failed"),
  });

  const treePreview = () => {
    const base = outputDir || "output_dir";
    const masks = exportMasks && !captionsOnly;
    // Every format writes the provenance manifests at the top level.
    const manifests = "\n  CREDITS.md\n  licenses.csv";
    switch (format) {
      case "kohya":
        return `${base}/\n  ${nRepeats}_${conceptToken}/\n    image.png\n    image.txt${masks ? `\n  ${nRepeats}_${conceptToken}_mask/\n    image.png` : ""}${manifests}`;
      case "aitoolkit":
        return `${base}/\n  ${conceptToken}/\n    image.jpg\n    image.txt${masks ? `\n  ${conceptToken}_mask/\n    image.png` : ""}${manifests}`;
      case "plain":
        return `${base}/\n  images/\n    image.png${masks ? `\n  masks/\n    image.png` : ""}\n  captions.jsonl${manifests}`;
    }
  };

  const toggleFlag = (key: string) => setExcludeFlags((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const toggleLicense = (id: string) => setLicenseFilter((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const toggleMaskLabel = (label: string) => setMaskLabels((prev) => {
    const next = new Set(prev);
    if (next.has(label)) next.delete(label); else next.add(label);
    return next;
  });

  const toggleMaskExcludeLabel = (label: string) => setMaskExcludeLabels((prev) => {
    const next = new Set(prev);
    if (next.has(label)) next.delete(label); else next.add(label);
    return next;
  });

  function handleResetToDefaults() {
    clearPersisted(EXPORT_WORKFLOW_KEY);
    if (datasetId) clearPersisted(datasetScopedKey(EXPORT_FILTERS_PREFIX, datasetId));

    setFormat(EXPORT_WORKFLOW_DEFAULTS.format);
    setCaptionFmt(EXPORT_WORKFLOW_DEFAULTS.captionFmt);
    setOutputDir(EXPORT_WORKFLOW_DEFAULTS.outputDir);
    setNRepeats(EXPORT_WORKFLOW_DEFAULTS.nRepeats);
    setConceptToken(EXPORT_WORKFLOW_DEFAULTS.conceptToken);
    setOutputImgFmt(EXPORT_WORKFLOW_DEFAULTS.outputImgFmt);
    setResizeTo(EXPORT_WORKFLOW_DEFAULTS.resizeTo);
    setCustomResize(EXPORT_WORKFLOW_DEFAULTS.customResize);
    setCustomResizeVal(EXPORT_WORKFLOW_DEFAULTS.customResizeVal);
    setStripMetadata(EXPORT_WORKFLOW_DEFAULTS.stripMetadata);
    setCaptionsOnly(EXPORT_WORKFLOW_DEFAULTS.captionsOnly);
    setExportMasks(EXPORT_WORKFLOW_DEFAULTS.exportMasks);
    setMaskInvert(EXPORT_WORKFLOW_DEFAULTS.maskInvert);
    setMaskMissing(EXPORT_WORKFLOW_DEFAULTS.maskMissing);

    setFilterAesthetic(EXPORT_FILTERS_DEFAULTS.filterAesthetic);
    setAestheticMin(EXPORT_FILTERS_DEFAULTS.aestheticMin);
    setFilterCaptioned(EXPORT_FILTERS_DEFAULTS.filterCaptioned);
    setExcludeFlags(new Set(EXPORT_FILTERS_DEFAULTS.excludeFlags));
    setLicenseFilter(new Set(EXPORT_FILTERS_DEFAULTS.licenseFilter));
    setCommercialOnly(EXPORT_FILTERS_DEFAULTS.commercialOnly);
    setExcludeUnlicensed(EXPORT_FILTERS_DEFAULTS.excludeUnlicensed);
    setExcludeNoDerivatives(EXPORT_FILTERS_DEFAULTS.excludeNoDerivatives);
    setFilterStyleSim(EXPORT_FILTERS_DEFAULTS.filterStyleSim);
    setStyleSimMin(EXPORT_FILTERS_DEFAULTS.styleSimMin);
    setSubfolderFilterActive(EXPORT_FILTERS_DEFAULTS.subfolderFilterActive);
    setSelectedSubfolders(new Set(EXPORT_FILTERS_DEFAULTS.selectedSubfolders));
    setMaskLabels(new Set(EXPORT_FILTERS_DEFAULTS.maskLabels));
    setMaskExcludeLabels(new Set(EXPORT_FILTERS_DEFAULTS.maskExcludeLabels));
    // The label trio too, or the debounced persist writes the surviving state
    // straight back into the blob `clearPersisted` just removed.
    setLabelFilter(new Set(EXPORT_FILTERS_DEFAULTS.labelFilter));
    setLabelMatch(EXPORT_FILTERS_DEFAULTS.labelMatch);
    setLabelMissing(EXPORT_FILTERS_DEFAULTS.labelMissing);

    toast.success("Configuration reset to defaults");
  }

  const isRunning = exportMutation.isPending || jobProgress?.status === "running";
  const isDone = jobProgress?.status === "completed";

  // The SSE progress event carries no result_data, so read the finished job row
  // for the manifest filenames it actually wrote.
  const { data: finishedJob } = useQuery({
    queryKey: ["job", activeJobId],
    queryFn: () => jobsApi.get(activeJobId!),
    enabled: !!activeJobId && isDone,
  });
  const manifestFiles: string[] = (finishedJob?.result_data?.manifest_files as string[]) ?? [];
  const showConcept = format === "kohya" || format === "aitoolkit";


  const exclusionRows = [
    { label: "Low aesthetic", count: preview?.excluded_low_aesthetic, show: filterAesthetic },
    { label: "No caption",    count: preview?.excluded_uncaptioned,   show: filterCaptioned },
    { label: "Flagged",       count: preview?.excluded_flagged,       show: excludeFlags.size > 0 },
    { label: "Low style sim", count: preview?.excluded_style_sim,     show: filterStyleSim },
    { label: "License",       count: preview?.excluded_license,      show: commercialOnly || excludeUnlicensed || excludeNoDerivatives || licenseFilter.size > 0 },
  ].filter((r) => r.show);

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      <div className="page-h" style={{ marginBottom: 20 }}>
        <div>
          <h1>Export</h1>
          <p>Package dataset into a training-ready format.</p>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 16, alignItems: "start" }}>
        {/* Left: Configuration */}
        <div className="panel">
          <div className="panel-h">
            <h3>Configuration</h3>
            <div style={{ flex: 1 }} />
            <button className="btn ghost sm" onClick={handleResetToDefaults} title="Clear remembered configuration and revert to defaults">
              Reset to defaults
            </button>
          </div>
          <div style={{ padding: "4px 22px" }}>

            {/* Format */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Format</h4>
                <p>Training framework target.</p>
              </div>
              <div>
                <div className="row-flex" style={{ flexWrap: "wrap" }}>
                  {(["kohya", "aitoolkit", "plain"] as Format[]).map((f) => (
                    <button key={f} className={`btn sm${format === f ? " primary" : ""}`} onClick={() => setFormat(f)}>
                      {FORMAT_LABELS[f]}
                    </button>
                  ))}
                </div>
                <pre style={{ marginTop: 10, padding: "10px 12px", background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r)", fontSize: 11.5, color: "var(--fg-mute)", fontFamily: "Geist Mono, monospace", lineHeight: 1.8, overflowX: "auto", whiteSpace: "pre" }}>
                  {treePreview()}
                </pre>
              </div>
            </div>

            {/* Caption file — not shown for plain (always jsonl there) */}
            {format !== "plain" && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Caption file</h4>
                  <p>How captions are written to disk.</p>
                </div>
                <div className="row-flex">
                  {([["txt", ".txt sidecar"], ["caption", ".caption sidecar"], ["jsonl", "JSONL manifest"]] as [CaptionFmt, string][]).map(([v, label]) => (
                    <button key={v} className={`btn sm${captionFmt === v ? " primary" : ""}`} onClick={() => setCaptionFmt(v)}>{label}</button>
                  ))}
                </div>
              </div>
            )}

            {/* Filters */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Filters</h4>
                <p>Exclude images that don't meet the criteria.</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>

                {/* Aesthetic */}
                <label className="row-flex" style={{ gap: 8 }}>
                  <input type="checkbox" className="checkbox" checked={filterAesthetic} onChange={(e) => setFilterAesthetic(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>Aesthetic ≥</span>
                  <input
                    type="number" className="input" step={0.5} min={1} max={10}
                    value={aestheticMin} onChange={(e) => setAestheticMin(Number(e.target.value))}
                    disabled={!filterAesthetic} style={{ width: 64, textAlign: "center" }}
                  />
                </label>

                {/* Has caption */}
                <label className="row-flex" style={{ gap: 8 }}>
                  <input type="checkbox" className="checkbox" checked={filterCaptioned} onChange={(e) => setFilterCaptioned(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>Has caption</span>
                </label>

                {/* Labels — the second organisational facet. Unlike everything
                    else in this panel, a label filter **narrows** the export the
                    way "Limit to subfolders" does rather than joining the
                    exclusion tally: it says which images the export is about.
                    Hidden entirely until a vocabulary exists. */}
                {exportLabels.length > 0 && (
                  <div>
                    <div style={{ fontSize: 12.5, marginBottom: 5 }}>
                      Labels{labelFilter.size === 0 && !labelMissing ? " — none selected, all images" : ""}
                    </div>
                    {/* `placement="inline"`: this whole page body is
                        `overflowY: auto`, which clips an absolutely positioned
                        panel at the scroll container's edge. */}
                    <LabelPicker
                      placement="inline"
                      labels={exportLabels}
                      selected={labelFilter}
                      ariaLabel="Label filters"
                      triggerAriaLabel="Filter the export by label"
                      active={labelFilter.size > 0 || labelMissing}
                      triggerContent={
                        labelMissing
                          ? "Unlabelled only"
                          : labelFilter.size === 0
                            ? "Choose labels"
                            : `${labelFilter.size} selected${labelFilter.size > 1 && labelMatch === "all" ? " ·All" : ""}`
                      }
                      onToggle={(id) => {
                        setLabelMissing(false);
                        setLabelFilter((prev) => {
                          const next = new Set(prev);
                          if (next.has(id)) next.delete(id); else next.add(id);
                          return next;
                        });
                      }}
                      footer={
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 5, alignItems: "center" }}>
                          {labelFilter.size > 1 && (
                            <span style={{ display: "inline-flex", gap: 3 }} role="group" aria-label="Label match mode">
                              {(["any", "all"] as const).map((m) => (
                                <button
                                  key={m}
                                  className={`btn sm${labelMatch === m ? " primary" : ""}`}
                                  aria-pressed={labelMatch === m}
                                  onClick={() => setLabelMatch(m)}
                                >
                                  {m === "any" ? "Any" : "All"}
                                </button>
                              ))}
                            </span>
                          )}
                          <button
                            className={`btn sm${labelMissing ? " primary" : ""}`}
                            aria-pressed={labelMissing}
                            title="Only images carrying no label at all"
                            onClick={() => {
                              setLabelMissing((prev) => {
                                if (!prev) setLabelFilter(new Set());
                                return !prev;
                              });
                            }}
                          >
                            Unlabelled only
                          </button>
                        </div>
                      }
                    />
                  </div>
                )}

                {/* License filters — operate on the effective license
                    (image value coalesced over the dataset default). */}
                <div>
                  <label className="row-flex" style={{ gap: 8 }}>
                    <input
                      type="checkbox" className="checkbox"
                      checked={commercialOnly}
                      onChange={(e) => setCommercialOnly(e.target.checked)}
                    />
                    <span style={{ fontSize: 12.5 }}>Commercial-use only</span>
                  </label>
                  <div style={{ fontSize: 11, color: "var(--fg-mute)", paddingLeft: 24, marginTop: 2 }}>
                    Keeps only licenses known to permit it — unknown counts as no.
                  </div>
                  {/* Its own flag, not an allowlist of every known id: that
                      would also drop `other:<free text>` licenses, which are
                      licensed — just not from the curated vocabulary. */}
                  <label className="row-flex" style={{ gap: 8, marginTop: 7 }}>
                    <input
                      type="checkbox" className="checkbox"
                      checked={excludeUnlicensed}
                      onChange={(e) => setExcludeUnlicensed(e.target.checked)}
                    />
                    <span style={{ fontSize: 12.5 }}>Exclude unlicensed images</span>
                  </label>
                  <label className="row-flex" style={{ gap: 8, marginTop: 7 }}>
                    <input
                      type="checkbox" className="checkbox"
                      checked={excludeNoDerivatives}
                      onChange={(e) => setExcludeNoDerivatives(e.target.checked)}
                    />
                    <span style={{ fontSize: 12.5 }}>Exclude no-derivatives</span>
                  </label>
                  <div style={{ fontSize: 11, color: "var(--fg-mute)", paddingLeft: 24, marginTop: 2 }}>
                    An export ships resized/cropped copies — which CC BY-ND forbids redistributing.
                  </div>
                  <details style={{ marginTop: 7 }}>
                    <summary style={{ fontSize: 12, color: "var(--fg-mute)", cursor: "pointer" }}>
                      Specific licenses{licenseFilter.size > 0 ? ` (${licenseFilter.size} selected)` : ""}
                    </summary>
                    <div style={{ display: "flex", flexDirection: "column", gap: 5, paddingLeft: 4, marginTop: 5 }}>
                      <label className="row-flex" style={{ gap: 8 }}>
                        <input
                          type="checkbox" className="checkbox"
                          checked={licenseFilter.has("")}
                          onChange={() => toggleLicense("")}
                        />
                        <span style={{ fontSize: 12 }}>No license recorded</span>
                      </label>
                      {LICENSE_OPTIONS.map((l) => (
                        <label key={l.id} className="row-flex" style={{ gap: 8 }}>
                          <input
                            type="checkbox" className="checkbox"
                            checked={licenseFilter.has(l.id)}
                            onChange={() => toggleLicense(l.id)}
                          />
                          <span style={{ fontSize: 12 }}>{l.label}</span>
                        </label>
                      ))}
                      {licenseFilterCustoms.map((lic) => (
                        <label key={lic} className="row-flex" style={{ gap: 8 }}>
                          <input
                            type="checkbox" className="checkbox"
                            checked={licenseFilter.has(lic)}
                            onChange={() => toggleLicense(lic)}
                          />
                          <span style={{ fontSize: 12 }} title={lic}>{lic.slice(OTHER_PREFIX.length)}</span>
                        </label>
                      ))}
                    </div>
                  </details>
                </div>

                {/* Per-flag checkboxes */}
                <div>
                  <div style={{ fontSize: 12.5, color: "var(--fg-mute)", marginBottom: 5 }}>Exclude flagged:</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 5, paddingLeft: 4 }}>
                    {FLAG_OPTIONS.map(({ key, label }) => (
                      <label key={key} className="row-flex" style={{ gap: 8 }}>
                        <input type="checkbox" className="checkbox" checked={excludeFlags.has(key)} onChange={() => toggleFlag(key)} />
                        <span style={{ fontSize: 12 }}>{label}</span>
                      </label>
                    ))}
                  </div>
                </div>

                {/* Style similarity */}
                <label className="row-flex" style={{ gap: 8 }}>
                  <input type="checkbox" className="checkbox" checked={filterStyleSim} onChange={(e) => setFilterStyleSim(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>Style similarity ≥</span>
                  <input
                    type="number" className="input" step={0.05} min={0} max={1}
                    value={styleSimMin} onChange={(e) => setStyleSimMin(Number(e.target.value))}
                    disabled={!filterStyleSim} style={{ width: 64, textAlign: "center" }}
                  />
                </label>

                {/* Subfolder filter */}
                {subfolders.length > 0 && (
                  <div>
                    <label className="row-flex" style={{ gap: 8 }}>
                      <input type="checkbox" className="checkbox" checked={subfolderFilterActive}
                        onChange={(e) => setSubfolderFilterActive(e.target.checked)} />
                      <span style={{ fontSize: 12.5 }}>Limit to subfolders</span>
                    </label>
                    {subfolderFilterActive && (
                      <div style={{ display: "flex", flexDirection: "column", gap: 5, paddingLeft: 4, marginTop: 6 }}>
                        {subfolders.map((sf) => (
                          <label key={sf.path} className="row-flex" style={{ gap: 8 }}>
                            <input
                              type="checkbox" className="checkbox"
                              checked={selectedSubfolders.has(sf.path)}
                              onChange={() => {
                                setSelectedSubfolders(prev => {
                                  const next = new Set(prev);
                                  next.has(sf.path) ? next.delete(sf.path) : next.add(sf.path);
                                  return next;
                                });
                              }}
                            />
                            <span style={{ fontSize: 12 }}>{sf.path === "" ? "(root)" : sf.path}</span>
                            <span style={{ fontSize: 11, color: "var(--fg-mute)", marginLeft: "auto" }}>{sf.image_count}</span>
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Resize */}
            <div className={`form-row${captionsOnly ? " disabled" : ""}`}>
              <div className="lbl-col">
                <h4>Resize on export</h4>
                <p>
                  Scales the <strong>longest side</strong> down to the chosen pixel count.
                  Aspect ratio is preserved — no cropping. Images already smaller than the
                  target are not upscaled. Originals are never modified.
                </p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div className="row-flex">
                  {([512, 768, 1024] as number[]).map((r) => (
                    <button
                      key={r}
                      className={`btn sm${!customResize && resizeTo === r ? " primary" : ""}`}
                      onClick={() => { setResizeTo(r); setCustomResize(false); }}
                    >
                      {r}
                    </button>
                  ))}
                  <button
                    className={`btn sm${customResize ? " primary" : ""}`}
                    onClick={() => setCustomResize(true)}
                  >
                    Custom
                  </button>
                  <button
                    className={`btn sm${!customResize && resizeTo === null ? " primary" : ""}`}
                    onClick={() => { setResizeTo(null); setCustomResize(false); }}
                  >
                    No resize
                  </button>
                </div>
                {customResize && (
                  <div className="row-flex" style={{ alignItems: "center", gap: 6 }}>
                    <input
                      className="input"
                      type="number"
                      min={64}
                      max={8192}
                      placeholder="e.g. 1536"
                      value={customResizeVal}
                      style={{ width: 110 }}
                      onChange={(e) => setCustomResizeVal(e.target.value)}
                    />
                    <span style={{ color: "var(--fg-muted)", fontSize: 12 }}>px (longest side)</span>
                  </div>
                )}
              </div>
            </div>

            {/* Output dir */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Output directory</h4>
                <p>Folder where files will be written.</p>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <input className="input" style={{ flex: 1 }} placeholder="C:\training\my_dataset" value={outputDir} onChange={(e) => setOutputDir(e.target.value)} />
                <button className="btn sm" title="Browse…" onClick={() => setDirPickerOpen(true)}>
                  <FolderOpen size={14} /> Browse
                </button>
              </div>
            </div>

            {dirPickerOpen && (
              <DirPickerModal
                initialPath={outputDir}
                onConfirm={(p) => { setOutputDir(p); setDirPickerOpen(false); }}
                onCancel={() => setDirPickerOpen(false)}
              />
            )}

            {/* Concept */}
            {showConcept && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Concept</h4>
                  <p>Token or concept name used in the folder structure.</p>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  {format === "kohya" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>Repeats</span>
                      <input type="number" className="input" min={1} max={100} value={nRepeats} onChange={(e) => setNRepeats(Number(e.target.value))} style={{ width: 80 }} />
                    </div>
                  )}
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                    <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>{format === "kohya" ? "Concept token" : "Concept name"}</span>
                    <input className="input" value={conceptToken} onChange={(e) => setConceptToken(e.target.value)} />
                  </div>
                </div>
              </div>
            )}

            {/* Loss masks */}
            <div className={`form-row${captionsOnly ? " disabled" : ""}`}>
              <div className="lbl-col">
                <h4>Loss masks</h4>
                <p>
                  Write a grayscale mask PNG per image from object detections, for
                  masked-loss training (kohya <code>conditioning_data_dir</code>,
                  ai-toolkit <code>mask_path</code>). White areas train; black areas
                  are ignored.
                </p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                <label className="row-flex" style={{ gap: 8 }}>
                  <input type="checkbox" className="checkbox" checked={exportMasks} onChange={(e) => setExportMasks(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>Export masks</span>
                </label>
                {exportMasks && (
                  <>
                    <div>
                      <div style={{ fontSize: 12.5, color: "var(--fg-mute)", marginBottom: 5 }}>
                        Detection labels{maskLabels.size === 0 ? " — none selected, all labels used" : ""}
                      </div>
                      {detectionLabels.length === 0 ? (
                        <div style={{ fontSize: 12, color: "var(--fg-dim)" }}>
                          No detections in this dataset yet — run object detection first.
                        </div>
                      ) : (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                          {detectionLabels.map(({ label, image_count }) => (
                            <button
                              key={label}
                              className={`btn sm${maskLabels.has(label) ? " primary" : ""}`}
                              onClick={() => toggleMaskLabel(label)}
                              title={`${image_count} image${image_count === 1 ? "" : "s"}`}
                            >
                              {label}
                              <span style={{ fontSize: 10, opacity: 0.7, marginLeft: 4 }}>{image_count}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    {detectionLabels.length > 0 && (
                      <div>
                        <div style={{ fontSize: 12.5, color: "var(--fg-mute)", marginBottom: 5 }}>
                          Exclude from mask
                        </div>
                        <div style={{ fontSize: 11.5, color: "var(--fg-dim)", marginBottom: 5 }}>
                          Regions with these labels are always painted black — overrides the selection above.
                        </div>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                          {detectionLabels.map(({ label, image_count }) => (
                            <button
                              key={label}
                              className={`btn sm${maskExcludeLabels.has(label) ? " primary" : ""}`}
                              onClick={() => toggleMaskExcludeLabel(label)}
                              title={`${image_count} image${image_count === 1 ? "" : "s"}`}
                            >
                              {label}
                              <span style={{ fontSize: 10, opacity: 0.7, marginLeft: 4 }}>{image_count}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    <label className="row-flex" style={{ gap: 8 }}>
                      <input type="checkbox" className="checkbox" checked={maskInvert} onChange={(e) => setMaskInvert(e.target.checked)} />
                      <span style={{ fontSize: 12.5 }}>Invert — train the background, mask out detections</span>
                    </label>
                    <div className="row-flex" style={{ gap: 8, alignItems: "center" }}>
                      <span style={{ fontSize: 12.5 }}>Images without detections:</span>
                      <select
                        className="input"
                        value={maskMissing}
                        onChange={(e) => setMaskMissing(e.target.value as MaskMissing)}
                        style={{ width: "auto" }}
                      >
                        <option value="white">Full-white mask (train normally)</option>
                        <option value="skip">Skip image</option>
                      </select>
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* Captions only */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Captions only</h4>
                <p>Skip image files — export caption files only.</p>
              </div>
              <label className="row-flex" style={{ gap: 8 }}>
                <input type="checkbox" className="checkbox" checked={captionsOnly} onChange={(e) => setCaptionsOnly(e.target.checked)} />
                <span style={{ fontSize: 12.5 }}>Enabled</span>
              </label>
            </div>

            {/* Image format */}
            <div className={`form-row${captionsOnly ? " disabled" : ""}`} style={{ borderBottom: "none" }}>
              <div className="lbl-col">
                <h4>Image format</h4>
                <p>Convert images when copying to the export folder.</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div className="row-flex">
                  {[["original", "Keep original"], ["png", "Force PNG"], ["jpeg", "Force JPEG"]].map(([v, label]) => (
                    <button key={v} className={`btn sm${outputImgFmt === v ? " primary" : ""}`} onClick={() => setOutputImgFmt(v)}>{label}</button>
                  ))}
                </div>
                <label className="row-flex" style={{ gap: 8 }}>
                  <input type="checkbox" className="checkbox" checked={stripMetadata} onChange={(e) => setStripMetadata(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>Strip generation metadata</span>
                </label>
              </div>
            </div>
          </div>
        </div>

        {/* Right: Summary + progress + build */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Summary */}
          <div className="panel">
            <div className="panel-h"><h3>Export summary</h3></div>
            <div style={{ padding: "14px 18px" }}>
              {preview ? (
                <>
                  {/* Will export stat */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
                    <div className="stat-card" style={{ padding: "12px 14px" }}>
                      <div className="sk">Will export</div>
                      <div className="sv" style={{ fontSize: 22 }}>{preview.will_export?.toLocaleString() ?? preview.image_count?.toLocaleString()}</div>
                    </div>
                    <div className="stat-card" style={{ padding: "12px 14px" }}>
                      <div className="sk">Total images</div>
                      <div className="sv" style={{ fontSize: 22 }}>{preview.image_count?.toLocaleString()}</div>
                    </div>
                  </div>

                  {/* Unlicensed warning — advisory only; export never blocks. */}
                  {!!preview.unlicensed_count && (
                    <div
                      style={{
                        marginBottom: 14, padding: "9px 12px", borderRadius: "var(--r)",
                        background: "rgba(210,154,58,.10)", border: "1px solid rgba(210,154,58,.35)",
                        fontSize: 12, color: "var(--warn)",
                      }}
                    >
                      {preview.unlicensed_count.toLocaleString()} image
                      {preview.unlicensed_count !== 1 ? "s have" : " has"} no license recorded.
                      {/* From the backend, which applied *every* filter. Deriving
                          this from the license flags alone claimed "they still
                          export" even when a caption or aesthetic filter had
                          already dropped them. */}
                      {preview.unlicensed_will_export === 0
                        ? " None of them are included in this export."
                        : preview.unlicensed_will_export === preview.unlicensed_count
                          ? " They still export, and are listed as unlicensed in CREDITS.md."
                          : ` ${preview.unlicensed_will_export.toLocaleString()} of them still export,` +
                            " listed as unlicensed in CREDITS.md."}
                    </div>
                  )}

                  {/* Stale scores — advisory only; export never blocks. Same
                      whole-dataset-count / survives-every-filter shape as the
                      unlicensed warning above, because this is where a stale
                      score actually costs something: "Exclude flagged" decides
                      on flags derived from the old pixels. */}
                  {!!preview.stale_scores_count && (
                    <div
                      style={{
                        marginBottom: 14, padding: "9px 12px", borderRadius: "var(--r)",
                        background: "rgba(210,154,58,.10)", border: "1px solid rgba(210,154,58,.35)",
                        fontSize: 12, color: "var(--warn)",
                      }}
                    >
                      {preview.stale_scores_count.toLocaleString()} image
                      {preview.stale_scores_count !== 1 ? "s were" : " was"} edited in place
                      (resize, crop, upscale, LUT, or frame re-extraction) after being scored,
                      so their quality scores and flags describe pixels that no longer exist.
                      {preview.stale_scores_will_export === 0
                        ? " None of them are included in this export."
                        : preview.stale_scores_will_export === preview.stale_scores_count
                          ? " They still export"
                          : ` ${preview.stale_scores_will_export.toLocaleString()} of them still export`}
                      {preview.stale_scores_will_export > 0 && (
                        <>
                          , and any flag-based exclusion may be dropping or keeping the wrong
                          images. Re-run quality scoring to refresh them.
                        </>
                      )}
                    </div>
                  )}

                  {/* Mixed aesthetic models — advisory only, and shown whenever
                      more than one marker is present rather than only when
                      `aesthetic_min` is on: the gallery's aesthetic sort reads
                      the same column, so a mixed dataset is worth saying either
                      way. The second sentence is the one that depends on the
                      filter. Never blocks, never changes `will_export`, and has
                      no exclusion row — a mixed column is a fact about the data,
                      not an exclusion the export applied. */}
                  {Object.keys(preview.aesthetic_models).length > 1 && (
                    <div
                      style={{
                        marginBottom: 14, padding: "9px 12px", borderRadius: "var(--r)",
                        background: "rgba(210,154,58,.10)", border: "1px solid rgba(210,154,58,.35)",
                        fontSize: 12, color: "var(--warn)",
                      }}
                    >
                      Aesthetic scores in this dataset come from more than one model
                      {" — "}
                      {Object.entries(preview.aesthetic_models)
                        .map(([m, n]) => `${n.toLocaleString()} by ${aestheticModelLabel(m)}`)
                        .join(", ")}
                      . The scales are not comparable.
                      {filterAesthetic && (
                        <>
                          {" "}The minimum-score filter applies one threshold to all of them, so it is
                          over- or under-including depending on which model scored each image
                          {/* `> 0`, not `> 1`: the extreme instance of one
                              threshold cutting two scales unequally is a
                              threshold that eliminates one model's images
                              *entirely*, which leaves exactly one surviving key
                              — the case where "900 by LAION still export" is the
                              most actionable number the box can carry, since it
                              is also saying the other model lost all of them.
                              The outer box is already gated on the whole-scope
                              dict having more than one key, so this only renders
                              inside an already-mixed dataset. */}
                          {Object.keys(preview.aesthetic_models_will_export).length > 0
                            ? ` (${Object.entries(preview.aesthetic_models_will_export)
                                .map(([m, n]) => `${n.toLocaleString()} by ${aestheticModelLabel(m)}`)
                                .join(", ")} still export)`
                            : ""}
                          . Re-score with one model on the Score images page to make it meaningful.
                        </>
                      )}
                    </div>
                  )}

                  {/* Free text under the ND filter — the one place the two
                      redistribution-safety filters disagree. "Commercial use"
                      drops what it cannot classify; "exclude no-derivatives"
                      keeps it. Only worth saying when that filter is on. */}
                  {excludeNoDerivatives && !!preview.freetext_will_export && (
                    <div
                      style={{
                        marginBottom: 14, padding: "9px 12px", borderRadius: "var(--r)",
                        background: "rgba(210,154,58,.10)", border: "1px solid rgba(210,154,58,.35)",
                        fontSize: 12, color: "var(--warn)",
                      }}
                    >
                      {preview.freetext_will_export.toLocaleString()} exporting image
                      {preview.freetext_will_export !== 1 ? "s have" : " has"} a free-text
                      license Crucible can't classify. <strong>Exclude no-derivatives</strong> drops
                      only licenses known to be ND, so these are included — check them if this
                      export will be redistributed.
                    </div>
                  )}

                  {/* Exclusion breakdown */}
                  {exclusionRows.length > 0 && (
                    <div style={{ marginBottom: 14 }}>
                      <div style={{ fontSize: 10.5, color: "var(--fg-dim)", marginBottom: 6, textTransform: "uppercase", letterSpacing: ".04em" }}>Excluded by filter</div>
                      {exclusionRows.map(({ label, count }) => (
                        <div key={label} style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 3 }}>
                          <span style={{ color: "var(--fg-mute)" }}>{label}</span>
                          <span className="mono" style={{ color: count ? "var(--warn)" : "var(--fg-dim)" }}>{count?.toLocaleString() ?? "—"}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Mask coverage */}
                  {exportMasks && !captionsOnly && preview.images_without_detections != null && (
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 14 }}>
                      <span style={{ color: "var(--fg-mute)" }}>
                        No detections → {maskMissing === "skip" ? "skipped" : "white mask"}
                      </span>
                      <span className="mono" style={{ color: preview.images_without_detections ? "var(--warn)" : "var(--fg-dim)" }}>
                        {preview.images_without_detections.toLocaleString()}
                      </span>
                    </div>
                  )}

                  {/* Sample captions */}
                  {preview.sample_files?.length > 0 && (
                    <div>
                      <div style={{ fontSize: 10.5, color: "var(--fg-dim)", marginBottom: 6, textTransform: "uppercase", letterSpacing: ".04em" }}>Sample captions</div>
                      {(preview.sample_files as { image: string; caption_preview: string }[]).map((f) => (
                        <div key={f.image} style={{ display: "flex", gap: 8, marginBottom: 5, fontSize: 11.5 }}>
                          <span className="mono" style={{ color: "var(--fg-dim)", width: 110, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.image}</span>
                          <span style={{ color: "var(--fg-mute)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {f.caption_preview || <em style={{ color: "var(--bad)" }}>no caption</em>}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <div style={{ textAlign: "center", padding: "30px 0", color: "var(--fg-soft)", fontSize: 13 }}>Loading preview…</div>
              )}
            </div>
          </div>

          {/* Progress */}
          {jobProgress && (
            <div className="panel">
              <div className="panel-h">
                <h3>Progress</h3>
                <div style={{ flex: 1 }} />
                <span className={`badge dot ${isDone ? "good" : "info"}`}>{isDone ? "Done" : "Running"}</span>
              </div>
              <div style={{ padding: "14px 18px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--fg-mute)", marginBottom: 6 }}>
                  <span>{jobProgress.message || "Exporting…"}</span>
                  <span className="mono">{jobProgress.done}/{jobProgress.total}</span>
                </div>
                <div style={{ height: 5, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${jobProgress.percent ?? 0}%`, background: "linear-gradient(90deg, var(--accent-2), var(--accent))", transition: "width .4s" }} />
                </div>
                {isDone && (
                  <p style={{ color: "var(--good)", fontSize: 12, marginTop: 8 }}>
                    ✓ Export complete → {outputDir}
                    {/* Named by the backend, not hardcoded: a manifest can supersede
                        the existing one, land on CREDITS.2.md, or be skipped as
                        byte-identical, so only the job knows what was written. */}
                    {manifestFiles.length > 0 && (
                      <>
                        <br />
                        <span style={{ color: "var(--fg-mute)" }}>
                          Manifests: {manifestFiles.join(", ")}
                        </span>
                      </>
                    )}
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Job label */}
          <input
            className="input"
            type="text"
            placeholder="Job label (optional)"
            value={jobLabel}
            onChange={(e) => setJobLabel(e.target.value)}
            style={{ width: "100%", fontSize: 12, marginBottom: 8 }}
            title="Optional name shown in the job queue"
          />

          {/* Build button */}
          <button
            className="btn primary"
            style={{ height: 38, width: "100%", justifyContent: "center" }}
            onClick={() => exportMutation.mutate()}
            disabled={!outputDir || isRunning}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M8 10V2M5 7l3 3 3-3M2.5 13.5h11"/>
            </svg>
            {isRunning ? "Exporting…" : "Build export"}
          </button>
        </div>
      </div>
    </div>
  );
}
