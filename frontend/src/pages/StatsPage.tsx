import { useState, useEffect, useCallback, useRef } from "react";
import { Settings, ChevronDown, ChevronRight, X } from "lucide-react";
import { Link } from "react-router-dom";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ImageListItem, SubfolderInfo } from "../types";
import { datasetsApi } from "../api/datasets";
import { captionsApi } from "../api/captions";
import { imagesApi, type ImageListParams } from "../api/images";
import { detectionApi, type DetectionStats } from "../api/detection";
import type { ScoreValues } from "../api/datasets";
import { settingsApi, type Thresholds } from "../api/settings";
import { OTHER_LICENSES_KEY, licenseInfo } from "../constants/licenses";
import LicenseBadge from "../components/common/LicenseBadge";
import { useJobStore } from "../store/jobStore";
import { STATS_FILTERS_PREFIX } from "../constants/storage";
import { loadPersisted, datasetScopedKey } from "../utils/persistentState";
import { useDebouncedPersist } from "../hooks/useDebouncedPersist";

const LIVE_STATS_JOB_TYPES = new Set(["caption", "caption_pipeline", "quality_score", "detection"]);

// ─── Constants ───────────────────────────────────────────────────────────────
const BLUR_EDGES   = [20, 40, 80, 150, 300];
const BLUR_LABELS  = ["0–20", "20–40", "40–80", "80–150", "150–300", "300+"];
const NOISE_EDGES  = [5, 10, 15, 20, 30];
const NOISE_LABELS = ["0–5", "5–10", "10–15", "15–20", "20–30", "30+"];
const UNI_EDGES    = [5, 10, 20, 40];
const UNI_LABELS   = ["0–5", "5–10", "10–20", "20–40", "40+"];
const COLOR_EDGES  = [10, 20, 40, 60];
const COLOR_LABELS = ["0–10", "10–20", "20–40", "40–60", "60+"];
const SAT_EDGES    = [10, 20, 40, 60];
const SAT_LABELS   = ["0–10", "10–20", "20–40", "40–60", "60+"];
const MP_EDGES     = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0];
const MP_LABELS    = ["<0.25", "0.25–0.5", "0.5–1", "1–2", "2–4", "4–8", "8+"];
const FS_EDGES_MB  = [0.1, 0.5, 1.0, 2.0, 5.0];
const FS_LABELS    = ["<0.1 MB", "0.1–0.5", "0.5–1", "1–2", "2–5", "5+"];
const AR_EDGES     = [0.5, 0.67, 0.85, 1.15, 1.4, 1.6, 1.95];
const AR_LABELS    = ["9:16+", "2:3", "3:4", "1:1", "4:3", "3:2", "16:9", "21:9+"];
const WC_EDGES     = [1, 6, 11, 21, 51];
const WC_LABELS    = ["No caption", "1–5 words", "6–10 words", "11–20 words", "21–50 words", "50+ words"];
const TC_EDGES     = [1, 20, 40, 60, 77];
const TC_LABELS    = ["No caption", "1–19", "20–39", "40–59", "60–76", "77+"];

const SCORE_GUIDE = [
  { metric: "Aesthetic",  range: "1–10", threshold: "≥ 5.0 keep · ≥ 6.5 curated · < 4.0 reject", note: "CLIP aesthetic predictor", cls: "warn" },
  { metric: "Watermark",  range: "0–1",  threshold: "< 0.6 clean · ≥ 0.6 exclude",                 note: "CLIP zero-shot detection",  cls: "bad"  },
  { metric: "Blur (Lap.)",range: "0–∞",  threshold: "≥ 80 sharp",                                   note: "Laplacian variance",        cls: "info" },
  { metric: "Noise",      range: "0–∞",  threshold: "< 15 clean",                                   note: "Smooth-region std dev",     cls: "info" },
  { metric: "Uniformity", range: "0–∞",  threshold: "≥ 12 has detail",                              note: "Grayscale std dev",         cls: "info" },
  { metric: "Style sim.", range: "0–1",  threshold: "≥ 0.5 consistent",                             note: "Cosine similarity",         cls: "good" },
];

const FLAG_DEFS = [
  { key: "blurry",      flag: "is_blurry",     label: "Blurry",       cls: "warn",
    icon: <><circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.5"/></> },
  { key: "noisy",       flag: "is_noisy",      label: "Noisy",        cls: "warn",
    icon: <><path d="M3 8h1.5M5.5 5v1M8 4v1M10.5 5v1M12.5 8h-1.5M10.5 11v-1M8 12v-1M5.5 11v-1"/></> },
  { key: "uniform",     flag: "is_uniform",    label: "Near-uniform", cls: "info",
    icon: <><rect x="3" y="3" width="10" height="10" rx="1"/></> },
  { key: "watermarked", flag: "has_watermark", label: "Watermarked",  cls: "bad",
    icon: <><path d="M3 6h10M3 9h7"/></> },
  { key: "duplicate",   flag: "is_duplicate",  label: "Duplicate",    cls: "info",
    icon: <><rect x="2.5" y="2.5" width="9" height="9" rx="1"/><rect x="5.5" y="5.5" width="8" height="8" rx="1"/></> },
  { key: "nsfw",        flag: "is_nsfw",          label: "NSFW",         cls: "bad",
    icon: <><path d="M2 2l12 12M6.5 6.5A3.5 3.5 0 0 0 4.5 9.5M9.5 9.5A3.5 3.5 0 0 0 11.5 6.5M3 4C1.5 5.5 1 7 1 8s.5 2.5 2 4M13 4c1.5 1.5 2 3 2 4s-.5 2.5-2 4"/></> },
  { key: "ai_artifacts", flag: "has_ai_artifacts", label: "AI artifacts", cls: "warn",
    icon: <><circle cx="8" cy="8" r="5.5"/><path d="M5.5 7.5C5.5 6.4 6.4 5.5 7.5 5.5h1C9.6 5.5 10.5 6.4 10.5 7.5c0 .8-.5 1.5-1.2 1.8L9 9.5V11"/><circle cx="9" cy="12.5" r=".5" fill="currentColor" stroke="none"/></> },
];

function flagHint(key: string, t: Thresholds): string {
  switch (key) {
    case "blurry":      return `Laplacian variance < ${t.blur_threshold}`;
    case "noisy":       return `Smooth-region std dev > ${t.noise_threshold}`;
    case "uniform":     return `Grayscale std dev < ${t.uniformity_threshold}`;
    case "watermarked": return `Watermark score ≥ ${t.watermark_threshold}`;
    case "duplicate":   return `Perceptual hash distance < ${t.duplicate_threshold}`;
    case "nsfw":        return `NSFW score ≥ ${t.nsfw_threshold}`;
    default:            return "";
  }
}

const AESTHETIC_FILTER_MAP: Record<string, FilterParams> = {
  "low (0-4)":   { min_score: 0, max_score: 4, sort: "aesthetic_score", order: "desc" },
  "mid (4-6)":   { min_score: 4, max_score: 6, sort: "aesthetic_score", order: "desc" },
  "high (6-10)": { min_score: 6, sort: "aesthetic_score", order: "desc" },
  "unscored":    { score_is_null: true, sort: "created_at", order: "desc" },
};

// ─── Types ───────────────────────────────────────────────────────────────────
type FilterParams = Omit<ImageListParams, "dataset_id" | "page" | "limit">;
interface ChartEntry { name: string; count: number; filter?: FilterParams; }
type PanelFilter = { title: string; params: FilterParams } | null;

type CategoryId = "summary" | "aesthetic" | "technical" | "properties" | "captions" | "detections";
type ItemId =
  | "score_guide" | "quality_flags" | "score_coverage" | "licenses"
  | "aesthetic_score" | "style_sim" | "color_richness" | "saturation"
  | "blur" | "noise" | "uniformity" | "watermark"
  | "aspect_ratio" | "megapixels" | "file_size" | "file_formats"
  | "caption_wc" | "caption_tc" | "top_tags" | "cooccurrence"
  | "det_overview" | "det_labels" | "det_models" | "det_score" | "det_coverage" | "det_per_image";

interface StatsCategoryDef { id: CategoryId; label: string; items: { id: ItemId; label: string }[]; }
interface StatsVisibility {
  categories: Record<CategoryId, boolean>;
  items: Record<ItemId, boolean>;
  collapsed: Record<CategoryId, boolean>;
}

// ─── Data builders ────────────────────────────────────────────────────────────
function scoreEntries(
  dist: Record<string, number>,
  labels: string[],
  edges: number[],
  field: string,
  order: "asc" | "desc" = "asc",
): ChartEntry[] {
  return labels
    .filter((lbl) => lbl in dist)
    .map((name) => {
      const i = labels.indexOf(name);
      return {
        name,
        count: dist[name],
        filter: {
          score_field: field,
          min_score: i > 0 ? edges[i - 1] : undefined,
          max_score: i < edges.length ? edges[i] : undefined,
          sort: field,
          order,
        },
      };
    });
}

function mpEntries(dist: Record<string, number>): ChartEntry[] {
  return MP_LABELS.filter((lbl) => lbl in dist).map((name) => {
    const i = MP_LABELS.indexOf(name);
    return {
      name,
      count: dist[name],
      filter: {
        mp_min: i > 0 ? MP_EDGES[i - 1] : undefined,
        mp_max: i < MP_EDGES.length ? MP_EDGES[i] : undefined,
      },
    };
  });
}

function fsEntries(dist: Record<string, number>): ChartEntry[] {
  return FS_LABELS.filter((lbl) => lbl in dist).map((name) => {
    const i = FS_LABELS.indexOf(name);
    return {
      name,
      count: dist[name],
      filter: {
        file_size_min: i > 0 ? Math.round(FS_EDGES_MB[i - 1] * 1_048_576) : undefined,
        file_size_max: i < FS_EDGES_MB.length ? Math.round(FS_EDGES_MB[i] * 1_048_576) : undefined,
        sort: "file_size_bytes",
        order: "asc" as const,
      },
    };
  });
}

function arFineEntries(dist: Record<string, number>): ChartEntry[] {
  return AR_LABELS.filter((lbl) => lbl in dist).map((name) => {
    const i = AR_LABELS.indexOf(name);
    return {
      name,
      count: dist[name],
      filter: {
        ar_min: i > 0 ? AR_EDGES[i - 1] : undefined,
        ar_max: i < AR_EDGES.length ? AR_EDGES[i] : undefined,
      },
    };
  });
}

function arCoarseEntries(dist: Record<string, number>): ChartEntry[] {
  return Object.entries(dist).map(([name, count]) => ({
    name,
    count,
    filter:
      name === "portrait"
        ? { ar_max: 0.8 }
        : name === "landscape"
        ? { ar_min: 1.2 }
        : { ar_min: 0.8, ar_max: 1.2 },
  }));
}

function wmEntries(dist: Record<string, number>): ChartEntry[] {
  return Object.entries(dist).map(([name, count]) => {
    const parts = name.split("–").map(Number);
    return {
      name,
      count,
      filter: {
        score_field: "watermark_score",
        min_score: isNaN(parts[0]) ? undefined : parts[0],
        max_score: isNaN(parts[1]) ? undefined : parts[1],
        sort: "watermark_score",
        order: "desc" as const,
      },
    };
  });
}

function ssimEntries(dist: Record<string, number>): ChartEntry[] {
  return Object.entries(dist).map(([name, count]) => {
    const parts = name.split("–").map(Number);
    return {
      name,
      count,
      filter: {
        score_field: "style_similarity_score",
        min_score: isNaN(parts[0]) ? undefined : parts[0],
        max_score: isNaN(parts[1]) ? undefined : parts[1],
        sort: "style_similarity_score",
        order: "desc" as const,
      },
    };
  });
}

// ─── Rebucketing helpers ─────────────────────────────────────────────────────
type FB = (min: number | undefined, max: number | undefined) => FilterParams | undefined;

const mkScore = (field: string, order: "asc" | "desc"): FB =>
  (min, max) => ({ score_field: field, min_score: min, max_score: max, sort: field, order });

const mkMp: FB = (min, max) => ({ mp_min: min, mp_max: max });

const mkFs: FB = (min, max) => ({
  file_size_min: min !== undefined ? Math.round(min * 1_048_576) : undefined,
  file_size_max: max !== undefined ? Math.round(max * 1_048_576) : undefined,
  sort: "file_size_bytes",
  order: "asc",
});

const mkCapWC: FB = (min, max) => ({ caption_words_min: min, caption_words_max: max });
const mkCapTC: FB = (min, max) => ({ caption_tokens_min: min, caption_tokens_max: max });

function capLenEntries(dist: Record<string, number>): ChartEntry[] {
  return WC_LABELS.filter(lbl => lbl in dist).map(name => {
    const i = WC_LABELS.indexOf(name);
    if (i === 0) return { name, count: dist[name], filter: { captioned: false } };
    return { name, count: dist[name],
      filter: mkCapWC(WC_EDGES[i - 1], i < WC_EDGES.length ? WC_EDGES[i] : undefined) };
  });
}

function capTokenEntries(dist: Record<string, number>): ChartEntry[] {
  return TC_LABELS.filter(lbl => lbl in dist).map(name => {
    const i = TC_LABELS.indexOf(name);
    if (i === 0) return { name, count: dist[name], filter: { captioned: false } };
    return { name, count: dist[name],
      filter: mkCapTC(TC_EDGES[i - 1], i < TC_EDGES.length ? TC_EDGES[i] : undefined) };
  });
}

const DEFAULT_EDGES: Record<string, string> = {
  aesthetic:  "4, 6",
  blur:       "20, 40, 80, 150, 300",
  noise:      "5, 10, 15, 20, 30",
  uniformity: "5, 10, 20, 40",
  watermark:  "0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9",
  color:      "10, 20, 40, 60",
  saturation: "10, 20, 40, 60",
  style_sim:  "0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9",
  megapixels: "0.25, 0.5, 1, 2, 4, 8",
  file_size:  "0.1, 0.5, 1, 2, 5",
  caption_wc: "1, 6, 11, 21, 51",
  caption_tc: "1, 20, 40, 60, 77",
};

function fmtEdge(n: number): string {
  if (Number.isInteger(n)) return String(n);
  return parseFloat(n.toPrecision(4)).toString();
}

function buildEdgeLabels(edges: number[]): string[] {
  const s = [...edges].sort((a, b) => a - b);
  return [
    `< ${fmtEdge(s[0])}`,
    ...s.slice(1).map((e, i) => `${fmtEdge(s[i])}–${fmtEdge(e)}`),
    `${fmtEdge(s[s.length - 1])}+`,
  ];
}

function rebucketValues(values: number[], edges: number[], fb: FB): ChartEntry[] {
  const s = [...edges].sort((a, b) => a - b);
  const labels = buildEdgeLabels(s);
  const counts: Record<string, number> = {};
  for (const l of labels) counts[l] = 0;
  for (const v of values) {
    let placed = false;
    for (let i = 0; i < s.length; i++) {
      if (v < s[i]) { counts[labels[i]]++; placed = true; break; }
    }
    if (!placed) counts[labels[s.length]]++;
  }
  return labels
    .map((name, i) => ({
      name,
      count: counts[name],
      filter: fb(i > 0 ? s[i - 1] : undefined, i < s.length ? s[i] : undefined),
    }))
    .filter(e => e.count > 0);
}

// ─── Category config ─────────────────────────────────────────────────────────
const STATS_CONFIG: StatsCategoryDef[] = [
  { id: "summary",    label: "Summary",           items: [
    { id: "score_guide",    label: "Score guide"     },
    { id: "quality_flags",  label: "Quality flags"   },
    { id: "score_coverage", label: "Score coverage"  },
    { id: "licenses",       label: "Licenses"        },
  ]},
  { id: "aesthetic",  label: "Aesthetic & Style",  items: [
    { id: "aesthetic_score", label: "Aesthetic score"  },
    { id: "style_sim",       label: "Style similarity" },
    { id: "color_richness",  label: "Color richness"   },
    { id: "saturation",      label: "Saturation"       },
  ]},
  { id: "technical",  label: "Technical Quality",  items: [
    { id: "blur",       label: "Blur score"       },
    { id: "noise",      label: "Noise score"      },
    { id: "uniformity", label: "Uniformity score" },
    { id: "watermark",  label: "Watermark score"  },
  ]},
  { id: "properties", label: "Image Properties",   items: [
    { id: "aspect_ratio", label: "Aspect ratio" },
    { id: "megapixels",   label: "Megapixels"   },
    { id: "file_size",    label: "File size"     },
    { id: "file_formats", label: "File formats"  },
  ]},
  { id: "captions",   label: "Captions & Tags",    items: [
    { id: "caption_wc",   label: "Caption word count"  },
    { id: "caption_tc",   label: "Caption token count" },
    { id: "top_tags",     label: "Top 20 tags"         },
    { id: "cooccurrence", label: "Tag co-occurrence"   },
  ]},
  { id: "detections", label: "Detections & Masks", items: [
    { id: "det_overview",  label: "Overview cards"        },
    { id: "det_labels",    label: "Label distribution"    },
    { id: "det_models",    label: "Model breakdown"       },
    { id: "det_score",     label: "Detection score"       },
    { id: "det_coverage",  label: "Mask coverage"         },
    { id: "det_per_image", label: "Detections per image"  },
  ]},
];

// ─── Detection chart builders ─────────────────────────────────────────────────
const COVERAGE_BUCKETS: { name: string; min?: number; max?: number }[] = [
  { name: "<2%",     max: 0.02 },
  { name: "2–10%",   min: 0.02, max: 0.10 },
  { name: "10–25%",  min: 0.10, max: 0.25 },
  { name: "25–50%",  min: 0.25, max: 0.50 },
  { name: "50–75%",  min: 0.50, max: 0.75 },
  { name: "75–95%",  min: 0.75, max: 0.95 },
  { name: ">95%",    min: 0.95 },
];

const PER_IMAGE_BUCKETS: { name: string; min: number; max?: number }[] = [
  { name: "0", min: 0, max: 0 },
  { name: "1", min: 1, max: 1 },
  { name: "2", min: 2, max: 2 },
  { name: "3–5", min: 3, max: 5 },
  { name: "6+", min: 6 },
];

function detLabelEntries(dist: Record<string, number>): ChartEntry[] {
  return Object.entries(dist).map(([name, count]) => ({
    name, count, filter: { detection_label_exact: name },
  }));
}

// Model isn't a gallery filter param, so bars are non-clickable (no filter).
function detModelEntries(dist: Record<string, number>): ChartEntry[] {
  return Object.entries(dist).map(([name, count]) => ({ name, count }));
}

function detScoreEntries(dist: Record<string, number>): ChartEntry[] {
  return Object.entries(dist).map(([name, count]) => {
    if (name === "unscored") return { name, count, filter: { detection_score_null: true } };
    const [lo, hi] = name.split("–").map(Number);
    return {
      name, count,
      filter: {
        detection_score_min: isNaN(lo) ? undefined : lo,
        // Top bin (0.9–1.0) must include score == 1.0, which the exclusive max
        // would drop — leave max open there.
        detection_score_max: isNaN(hi) || hi >= 1 ? undefined : hi,
      },
    };
  });
}

function detCoverageEntries(dist: Record<string, number>): ChartEntry[] {
  return COVERAGE_BUCKETS.filter((b) => b.name in dist).map((b) => ({
    name: b.name, count: dist[b.name],
    filter: { mask_coverage_min: b.min, mask_coverage_max: b.max },
  }));
}

function detPerImageEntries(dist: Record<string, number>): ChartEntry[] {
  return PER_IMAGE_BUCKETS.filter((b) => b.name in dist).map((b) => ({
    name: b.name, count: dist[b.name],
    filter: { detection_count_min: b.min, detection_count_max: b.max },
  }));
}

// ─── Visibility hook ──────────────────────────────────────────────────────────
const STATS_VIS_KEY = "stats-visibility-v1";

function defaultVisibility(): StatsVisibility {
  const categories = {} as Record<CategoryId, boolean>;
  const items      = {} as Record<ItemId, boolean>;
  const collapsed  = {} as Record<CategoryId, boolean>;
  for (const cat of STATS_CONFIG) {
    categories[cat.id] = true;
    collapsed[cat.id]  = false;
    for (const item of cat.items) items[item.id] = true;
  }
  return { categories, items, collapsed };
}

function useStatsVisibility() {
  const [vis, setVis] = useState<StatsVisibility>(() => {
    try {
      const raw = localStorage.getItem(STATS_VIS_KEY);
      if (!raw) return defaultVisibility();
      const p = JSON.parse(raw) as Partial<StatsVisibility>;
      const d = defaultVisibility();
      return {
        categories: { ...d.categories, ...(p.categories ?? {}) },
        items:      { ...d.items,      ...(p.items      ?? {}) },
        collapsed:  { ...d.collapsed,  ...(p.collapsed  ?? {}) },
      };
    } catch { return defaultVisibility(); }
  });
  useEffect(() => { localStorage.setItem(STATS_VIS_KEY, JSON.stringify(vis)); }, [vis]);
  const toggleCategory = useCallback((id: CategoryId) =>
    setVis(v => ({ ...v, categories: { ...v.categories, [id]: !v.categories[id] } })), []);
  const toggleItem = useCallback((id: ItemId) =>
    setVis(v => ({ ...v, items: { ...v.items, [id]: !v.items[id] } })), []);
  const toggleCollapsed = useCallback((id: CategoryId) =>
    setVis(v => ({ ...v, collapsed: { ...v.collapsed, [id]: !v.collapsed[id] } })), []);
  const reset = useCallback(() => setVis(defaultVisibility()), []);
  return { vis, toggleCategory, toggleItem, toggleCollapsed, reset };
}

// ─── SettingsDrawer ───────────────────────────────────────────────────────────
function SettingsDrawer({
  categories, items, onToggleCategory, onToggleItem, onReset, onClose,
}: {
  categories: Record<CategoryId, boolean>;
  items: Record<ItemId, boolean>;
  onToggleCategory: (id: CategoryId) => void;
  onToggleItem: (id: ItemId) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 55, background: "rgba(0,0,0,.35)" }} />
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: "fixed", top: 0, right: 0, bottom: 0, zIndex: 56,
          width: 280, background: "var(--surface-1)",
          borderLeft: "1px solid var(--line)",
          boxShadow: "-8px 0 32px rgba(0,0,0,.45)",
          display: "flex", flexDirection: "column",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 16px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>Display settings</span>
          <button className="icon-btn" onClick={onClose}><X size={15} /></button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "10px 0" }}>
          {STATS_CONFIG.map((cat) => {
            const catOn = categories[cat.id];
            return (
              <div key={cat.id} style={{ marginBottom: 4 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 16px", cursor: "pointer" }}>
                  <input type="checkbox" className="checkbox" checked={catOn} onChange={() => onToggleCategory(cat.id)} />
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--fg)" }}>{cat.label}</span>
                </label>
                {cat.items.map((item) => (
                  <label key={item.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "5px 16px 5px 38px", cursor: catOn ? "pointer" : "default", opacity: catOn ? 1 : 0.4 }}>
                    <input type="checkbox" className="checkbox" checked={items[item.id]} disabled={!catOn} onChange={() => onToggleItem(item.id)} />
                    <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>{item.label}</span>
                  </label>
                ))}
              </div>
            );
          })}
        </div>
        <div style={{ padding: "12px 16px", borderTop: "1px solid var(--line)", flexShrink: 0 }}>
          <button className="btn ghost" style={{ width: "100%", justifyContent: "center", fontSize: 12 }} onClick={onReset}>
            Reset to defaults
          </button>
        </div>
      </div>
    </>
  );
}

// ─── CategorySection ──────────────────────────────────────────────────────────
function CategorySection({ label, isVisible, isCollapsed, onToggleCollapsed, children }: {
  label: string; isVisible: boolean;
  isCollapsed: boolean; onToggleCollapsed: () => void; children: React.ReactNode;
}) {
  if (!isVisible) return null;
  return (
    <section style={{ marginBottom: 22 }}>
      <button
        onClick={onToggleCollapsed}
        style={{
          display: "flex", alignItems: "center", gap: 8, width: "100%",
          background: "none", border: "none", padding: "0 0 8px 0",
          cursor: "pointer", borderBottom: "1px solid var(--line)", marginBottom: 14,
          color: "var(--fg)",
        }}
      >
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--fg-dim)", flex: 1, textAlign: "left" }}>
          {label}
        </span>
        {isCollapsed ? <ChevronRight size={13} style={{ flexShrink: 0 }} /> : <ChevronDown size={13} style={{ flexShrink: 0 }} />}
      </button>
      {!isCollapsed && children}
    </section>
  );
}

// ─── ImageLightbox ────────────────────────────────────────────────────────────
function ImageLightbox({
  images,
  index,
  datasetId,
  onClose,
  onNavigate,
  onDelete,
}: {
  images: ImageListItem[];
  index: number;
  datasetId: string;
  onClose: () => void;
  onNavigate: (i: number) => void;
  onDelete: (imageId: string) => Promise<void>;
}) {
  const img = images[index];
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const prev = useCallback(() => { setConfirmDelete(false); onNavigate(index - 1); }, [index, onNavigate]);
  const next = useCallback(() => { setConfirmDelete(false); onNavigate(index + 1); }, [index, onNavigate]);

  useEffect(() => { setConfirmDelete(false); }, [index]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") { if (confirmDelete) setConfirmDelete(false); else onClose(); }
      if (e.key === "ArrowLeft"  && index > 0)               prev();
      if (e.key === "ArrowRight" && index < images.length - 1) next();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [index, images.length, onClose, prev, next, confirmDelete]);

  const handleDelete = async () => {
    setDeleting(true);
    try { await onDelete(img.id); }
    finally { setDeleting(false); setConfirmDelete(false); }
  };

  const scores: { label: string; value: string | number | null }[] = [
    { label: "Aesthetic",  value: img.aesthetic_score  != null ? img.aesthetic_score.toFixed(2)  : null },
    { label: "Blur",       value: img.blur_score       != null ? img.blur_score.toFixed(1)       : null },
    { label: "Watermark",  value: img.watermark_score  != null ? img.watermark_score.toFixed(2)  : null },
    { label: "Color",      value: img.color_score      != null ? img.color_score.toFixed(1)      : null },
    { label: "Saturation", value: img.saturation_score != null ? img.saturation_score.toFixed(1) : null },
  ].filter((s) => s.value != null);

  return (
    <div
      style={{ position: "fixed", inset: 0, zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,.85)" }}
      onClick={onClose}
    >
      <div
        style={{ background: "var(--surface-1)", borderRadius: "var(--r-xl)", maxWidth: 900, width: "100%", margin: "0 16px", display: "flex", flexDirection: "column", maxHeight: "90vh", border: "1px solid var(--line-2)", boxShadow: "var(--shadow-lg)", overflow: "hidden" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: "1px solid var(--line)", gap: 12, flexShrink: 0 }}>
          <span style={{ fontSize: 13, color: "var(--fg-mute)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={img.filename}>
            {img.filename}
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <span style={{ fontSize: 11, color: "var(--fg-dim)" }}>{index + 1} / {images.length}</span>
            <Link
              to={`/datasets/${datasetId}/image/${img.id}`}
              style={{ fontSize: 11.5, padding: "3px 8px", borderRadius: "var(--r)", background: "var(--surface-3)", color: "var(--fg-mute)", textDecoration: "none" }}
            >
              View Details →
            </Link>
            {confirmDelete ? (
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ fontSize: 12, color: "var(--bad)" }}>Remove image?</span>
                <button onClick={handleDelete} disabled={deleting} className="btn sm danger">{deleting ? "…" : "Remove"}</button>
                <button onClick={() => setConfirmDelete(false)} className="btn sm ghost">Cancel</button>
              </div>
            ) : (
              <button onClick={() => setConfirmDelete(true)} className="btn sm danger">Delete</button>
            )}
            <button onClick={onClose} className="icon-btn" style={{ fontSize: 18 }}>×</button>
          </div>
        </div>

        {/* Image */}
        <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,.4)", flex: 1, overflow: "hidden", minHeight: 0 }}>
          {index > 0 && (
            <button onClick={prev} style={{ position: "absolute", left: 8, zIndex: 10, width: 36, height: 36, borderRadius: "50%", background: "rgba(0,0,0,.6)", color: "#fff", fontSize: 20, border: "none", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}>‹</button>
          )}
          <img key={img.id} src={imagesApi.fileUrl(img.id)} alt={img.filename} style={{ objectFit: "contain", maxHeight: "60vh", maxWidth: "100%" }} />
          {index < images.length - 1 && (
            <button onClick={next} style={{ position: "absolute", right: 8, zIndex: 10, width: 36, height: 36, borderRadius: "50%", background: "rgba(0,0,0,.6)", color: "#fff", fontSize: 20, border: "none", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}>›</button>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: "10px 16px", borderTop: "1px solid var(--line)", flexShrink: 0, display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          {img.width && img.height && <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>{img.width}×{img.height}</span>}
          {img.file_size_bytes && (
            <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>
              {img.file_size_bytes > 1_048_576 ? `${(img.file_size_bytes / 1_048_576).toFixed(1)} MB` : `${Math.round(img.file_size_bytes / 1024)} KB`}
            </span>
          )}
          {scores.map(({ label, value }) => (
            <span key={label} style={{ fontSize: 12, color: "var(--fg-dim)" }}>
              <span style={{ color: "var(--fg-soft)" }}>{label}:</span> {value}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── BucketPanel ─────────────────────────────────────────────────────────────
function BucketPanel({
  title,
  params,
  datasetId,
  onClose,
  subfolder,
}: {
  title: string;
  params: FilterParams;
  datasetId: string;
  onClose: () => void;
  subfolder?: string;
}) {
  const [previewIdx, setPreviewIdx] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const queryKey = ["bucket-images", datasetId, params, subfolder];

  const { data: images = [], isLoading } = useQuery({
    queryKey,
    queryFn: () => imagesApi.list({ dataset_id: datasetId, limit: 200, ...params, subfolder }),
  });

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") { if (confirmDeleteId) setConfirmDeleteId(null); else onClose(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, confirmDeleteId]);

  const handleDelete = async (imageId: string) => {
    await imagesApi.delete(imageId);
    const deletedIdx = images.findIndex((img) => img.id === imageId);
    const next = images.filter((img) => img.id !== imageId);
    queryClient.setQueryData<ImageListItem[]>(queryKey, next);
    queryClient.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
    queryClient.invalidateQueries({ queryKey: ["detection-stats", datasetId] });
    queryClient.invalidateQueries({ queryKey: ["tag-stats", datasetId] });
    queryClient.invalidateQueries({ queryKey: ["score-values", datasetId] });
    queryClient.invalidateQueries({ queryKey: ["tag-cooccurrence", datasetId] });
    queryClient.invalidateQueries({ queryKey: ["datasets"] });
    queryClient.invalidateQueries({ queryKey: ["dataset", datasetId] });
    setConfirmDeleteId(null);
    if (previewIdx !== null && deletedIdx !== -1) {
      if (next.length === 0) setPreviewIdx(null);
      else if (deletedIdx <= previewIdx) setPreviewIdx(Math.max(0, previewIdx - 1));
    }
  };

  return (
    <>
      <div style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,.7)" }} onClick={onClose}>
        <div style={{ background: "var(--surface-1)", borderRadius: "var(--r-xl)", width: "100%", maxWidth: 900, maxHeight: "85vh", display: "flex", flexDirection: "column", margin: "0 16px", border: "1px solid var(--line-2)", boxShadow: "var(--shadow-lg)" }} onClick={(e) => e.stopPropagation()}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 20px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
            <div>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{title}</h3>
              {!isLoading && (
                <p style={{ margin: "2px 0 0", fontSize: 11.5, color: "var(--fg-mute)" }}>
                  {images.length === 200 ? "200+ images (first 200 shown)" : `${images.length} image${images.length !== 1 ? "s" : ""}`}
                  {images.length > 0 && <span style={{ color: "var(--fg-soft)" }}> · click to preview</span>}
                </p>
              )}
            </div>
            <button onClick={onClose} className="icon-btn" style={{ fontSize: 18 }}>×</button>
          </div>

          <div style={{ overflowY: "auto", padding: 16 }}>
            {isLoading ? (
              <div style={{ textAlign: "center", padding: "60px 0", color: "var(--fg-mute)", fontSize: 13 }}>Loading…</div>
            ) : images.length === 0 ? (
              <div style={{ textAlign: "center", padding: "60px 0", color: "var(--fg-mute)", fontSize: 13 }}>No images found</div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(90px, 1fr))", gap: 6 }}>
                {images.map((img, i) => (
                  <div key={img.id} style={{ position: "relative", aspectRatio: "1/1" }} className="group">
                    <button
                      style={{ width: "100%", height: "100%", overflow: "hidden", borderRadius: "var(--r)", border: "1px solid var(--line)", background: "var(--surface-2)", cursor: "pointer", padding: 0 }}
                      onClick={() => { setConfirmDeleteId(null); setPreviewIdx(i); }}
                    >
                      <img src={imagesApi.thumbnailUrlVersioned(img.id, img.updated_at)} alt={img.filename} style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} loading="lazy" />
                    </button>

                    {confirmDeleteId === img.id ? (
                      <div style={{ position: "absolute", inset: 0, borderRadius: "var(--r)", background: "rgba(0,0,0,.85)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 6, zIndex: 10 }}>
                        <span style={{ fontSize: 11, color: "var(--bad)", fontWeight: 500 }}>Remove?</span>
                        <div style={{ display: "flex", gap: 4 }}>
                          <button onClick={(e) => { e.stopPropagation(); handleDelete(img.id); }} className="btn sm danger" style={{ fontSize: 11 }}>Yes</button>
                          <button onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(null); }} className="btn sm ghost" style={{ fontSize: 11 }}>No</button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(img.id); }}
                        style={{ position: "absolute", top: 4, right: 4, width: 18, height: 18, borderRadius: "50%", background: "rgba(0,0,0,.7)", color: "#fff", fontSize: 12, border: "none", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", opacity: 0, transition: "opacity .15s", zIndex: 10 }}
                        className="delete-btn"
                        title="Delete image"
                      >×</button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {previewIdx !== null && images.length > 0 && (
        <ImageLightbox
          images={images}
          index={previewIdx}
          datasetId={datasetId}
          onClose={() => setPreviewIdx(null)}
          onNavigate={setPreviewIdx}
          onDelete={handleDelete}
        />
      )}
    </>
  );
}

// ─── CooccurrenceHeatmap ─────────────────────────────────────────────────────
function CooccurrenceHeatmap({ tags, matrix }: { tags: string[]; matrix: number[][] }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);
  if (!tags.length) return <div style={{ fontSize: 12, color: "var(--fg-dim)", padding: "16px 0", textAlign: "center" }}>No tag data</div>;

  const maxOff = Math.max(1, ...matrix.flatMap((row, i) => row.filter((_, j) => i !== j)));
  const LABEL_W = 100;
  const CELL = 28;

  return (
    <div style={{ overflowX: "auto", position: "relative" }}>
      {tooltip && (
        <div style={{ position: "fixed", zIndex: 50, pointerEvents: "none", padding: "3px 8px", borderRadius: "var(--r)", fontSize: 11, background: "var(--surface-3)", border: "1px solid var(--line-2)", color: "var(--fg)", left: tooltip.x + 12, top: tooltip.y - 8 }}>
          {tooltip.text}
        </div>
      )}
      <div style={{ display: "inline-block", minWidth: LABEL_W + tags.length * CELL }}>
        <div style={{ display: "flex", marginLeft: LABEL_W }}>
          {tags.map((tag) => (
            <div key={tag} style={{ width: CELL, minWidth: CELL, fontSize: 9, color: "var(--fg-dim)", transform: "rotate(-45deg)", transformOrigin: "bottom left", height: 64, overflow: "hidden", whiteSpace: "nowrap", paddingLeft: 2 }}>{tag}</div>
          ))}
        </div>
        {tags.map((rowTag, i) => (
          <div key={rowTag} style={{ display: "flex", alignItems: "center" }}>
            <div style={{ width: LABEL_W, minWidth: LABEL_W, fontSize: 10, color: "var(--fg-dim)", overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis", paddingRight: 6, textAlign: "right" }}>{rowTag}</div>
            {tags.map((colTag, j) => {
              const val = matrix[i][j];
              const isDiag = i === j;
              const opacity = isDiag ? 0.25 : val / maxOff;
              const bg = isDiag ? "rgba(16,185,129,0.35)" : `rgba(79,147,214,${Math.min(opacity, 1).toFixed(2)})`;
              return (
                <div
                  key={colTag}
                  style={{ width: CELL, minWidth: CELL, height: CELL, background: bg, border: "1px solid rgba(255,255,255,.04)", cursor: "default", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 8, color: val > 0 ? "rgba(255,255,255,.6)" : "transparent" }}
                  onMouseMove={(e) => setTooltip({ x: e.clientX, y: e.clientY, text: isDiag ? `${rowTag}: ${val} images` : `${rowTag} + ${colTag}: ${val} images` })}
                  onMouseLeave={() => setTooltip(null)}
                >
                  {val > 0 ? val : ""}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── CssHist ─────────────────────────────────────────────────────────────────
function CssHist({ entries, onBarClick }: { entries: ChartEntry[]; onBarClick?: (e: ChartEntry) => void }) {
  if (!entries.length) return <div style={{ color: "var(--fg-dim)", fontSize: 12, padding: "24px 0", textAlign: "center" }}>No data</div>;
  const max = Math.max(...entries.map((e) => e.count), 1);
  const cols = entries.length;
  return (
    <>
      <div className="hist" style={{ "--cols": cols, gridTemplateRows: "1fr" } as React.CSSProperties}>
        {entries.map((e) => (
          <div
            key={e.name}
            className="hist-bar"
            style={{ height: `${Math.max(5, Math.round((e.count / max) * 100))}%` }}
            title={`${e.name}: ${e.count.toLocaleString()}`}
            onClick={() => e.filter && onBarClick?.(e)}
          />
        ))}
      </div>
      <div className="hist-axis" style={{ "--cols": cols } as React.CSSProperties}>
        {entries.map((e) => <span key={e.name}>{e.name}</span>)}
      </div>
    </>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function formatSize(mb: number) {
  return mb > 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(1)} MB`;
}

function meanAesthetic(values: number[] | undefined): string {
  if (!values || values.length === 0) return "—";
  return (values.reduce((a, b) => a + b, 0) / values.length).toFixed(2);
}

function escapeCsv(v: string): string {
  if (/[",\n\r]/.test(v)) return `"${v.replace(/"/g, '""')}"`;
  return v;
}

function safeFilename(name: string): string {
  return name.replace(/[/\\:*?"<>|]/g, "_").trim();
}

function triggerDownload(csv: string, filename: string) {
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function meanOf(values: number[] | undefined): string {
  if (!values || values.length === 0) return "";
  return (values.reduce((a, b) => a + b, 0) / values.length).toFixed(4);
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function downloadCsv(stats: any, sv: ScoreValues | undefined, det: DetectionStats | undefined, datasetId: string, datasetName: string) {
  type Row = [string, string | number];
  const section = (label: string): Row[] => [["", ""], [`## ${label}`, ""]];
  const dist = (prefix: string, d: Record<string, number>): Row[] =>
    Object.entries(d).map(([k, v]) => [`${prefix}_${k.replace(/[^a-z0-9]/gi, "_").replace(/^_+|_+$/g, "").toLowerCase()}`, v]);

  const rows: Row[] = [
    ...section("SUMMARY"),
    ["dataset_id", datasetId],
    ["dataset_name", escapeCsv(datasetName)],
    ["image_count", stats.image_count],
    ["captioned_count", stats.captioned_count ?? ""],
    ["caption_coverage_pct", stats.caption_coverage_pct],
    ["total_size_mb", stats.total_size_mb],
    ["avg_width", stats.avg_width ?? ""],
    ["avg_height", stats.avg_height ?? ""],

    ...section("FILE SIZE SUMMARY"),
    ["file_size_min_mb",    stats.file_size_summary?.min_mb    ?? ""],
    ["file_size_median_mb", stats.file_size_summary?.median_mb ?? ""],
    ["file_size_p95_mb",    stats.file_size_summary?.p95_mb    ?? ""],
    ["file_size_max_mb",    stats.file_size_summary?.max_mb    ?? ""],

    ...section("QUALITY FLAGS"),
    ...Object.entries(stats.quality_flag_counts as Record<string, number>).map(([k, v]): Row => [`flag_${k}`, v]),

    ...section("SCORE COVERAGE"),
    ...Object.entries(stats.score_coverage as Record<string, number>).map(([k, v]): Row => [`coverage_${k}`, v]),

    ...section("MEAN SCORES"),
    ["mean_aesthetic_score",       meanOf(sv?.aesthetic_score)],
    ["mean_blur_score",            meanOf(sv?.blur_score)],
    ["mean_noise_score",           meanOf(sv?.noise_score)],
    ["mean_uniformity_score",      meanOf(sv?.uniformity_score)],
    ["mean_watermark_score",       meanOf(sv?.watermark_score)],
    ["mean_color_score",           meanOf(sv?.color_score)],
    ["mean_saturation_score",      meanOf(sv?.saturation_score)],
    ["mean_style_similarity_score",meanOf(sv?.style_similarity_score)],

    ...section("AESTHETIC SCORE DISTRIBUTION"),
    ...dist("aesthetic", stats.score_distribution),

    ...section("BLUR DISTRIBUTION"),
    ...dist("blur", stats.blur_distribution),

    ...section("NOISE DISTRIBUTION"),
    ...dist("noise", stats.noise_distribution),

    ...section("UNIFORMITY DISTRIBUTION"),
    ...dist("uniformity", stats.uniformity_distribution),

    ...section("WATERMARK DISTRIBUTION"),
    ...dist("watermark", stats.watermark_distribution),

    ...section("COLOR RICHNESS DISTRIBUTION"),
    ...dist("color", stats.color_distribution),

    ...section("SATURATION DISTRIBUTION"),
    ...dist("saturation", stats.saturation_distribution),

    ...section("STYLE SIMILARITY DISTRIBUTION"),
    ...dist("style_sim", stats.style_similarity_distribution ?? {}),

    ...section("MEGAPIXEL DISTRIBUTION"),
    ...dist("megapixels", stats.megapixel_distribution),

    ...section("FILE SIZE DISTRIBUTION"),
    ...dist("file_size", stats.file_size_distribution),

    ...section("ASPECT RATIO DISTRIBUTION"),
    ...dist("aspect_ratio", Object.keys(stats.aspect_ratio_fine).length > 0
      ? stats.aspect_ratio_fine
      : stats.aspect_ratio_distribution),

    ...section("FORMAT DISTRIBUTION"),
    ...dist("format", stats.format_distribution),

    ...section("CAPTION WORD COUNT DISTRIBUTION"),
    ...dist("caption_words", stats.caption_length_distribution),

    ...section("CAPTION TOKEN DISTRIBUTION"),
    ...dist("caption_tokens", stats.caption_token_distribution),

    ...(det ? [
      ...section("DETECTIONS SUMMARY"),
      ["total_detections", det.total_detections] as Row,
      ["images_with_detections", det.images_with_detections] as Row,
      ["images_without_detections", det.images_without_detections] as Row,
      ["distinct_labels", det.distinct_labels] as Row,
      ["bbox_only_count", det.bbox_only_count] as Row,

      ...section("DETECTION LABEL DISTRIBUTION"),
      ...dist("det_label", det.label_distribution),

      ...section("DETECTION MODEL DISTRIBUTION"),
      ...dist("det_model", det.model_distribution),

      ...section("DETECTION SCORE DISTRIBUTION"),
      ...dist("det_score", det.score_histogram),

      ...section("MASK COVERAGE DISTRIBUTION"),
      ...dist("mask_coverage", det.coverage_histogram),

      ...section("DETECTIONS PER IMAGE DISTRIBUTION"),
      ...dist("det_per_image", det.detections_per_image),
    ] : []),
  ];

  const csv = rows.map(([k, v]) => `${k},${v}`).join("\n");
  triggerDownload(csv, `dataset-${safeFilename(datasetName) || datasetId}-stats.csv`);
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function downloadTagsCsv(tags: any[], datasetId: string, datasetName: string) {
  const header = "tag,count";
  const rows = tags.map(t => `${escapeCsv(String(t.tag))},${t.count}`);
  triggerDownload([header, ...rows].join("\n"), `dataset-${safeFilename(datasetName) || datasetId}-tags.csv`);
}

// ─── HistPanel ────────────────────────────────────────────────────────────────
interface HistPanelProps {
  title: string;
  subtitle?: string;
  entries: ChartEntry[];
  onBarClick: (e: ChartEntry) => void;
  rawValues?: number[];
  defaultEdgeStr?: string;
  fb?: FB;
  footer?: React.ReactNode;
  storageKey?: string;
}

function HistPanel({ title, subtitle, entries, onBarClick, rawValues, defaultEdgeStr, fb, footer, storageKey }: HistPanelProps) {
  const canEdit = rawValues !== undefined && defaultEdgeStr !== undefined && fb !== undefined;
  const [isEditing, setIsEditing] = useState(false);
  const [activeEdgeStr, setActiveEdgeStr] = useState<string | null>(() => {
    if (!storageKey) return null;
    try { return localStorage.getItem(storageKey) || null; } catch { return null; }
  });
  const [draft, setDraft] = useState(activeEdgeStr ?? defaultEdgeStr ?? "");

  useEffect(() => {
    if (!storageKey) return;
    try {
      if (activeEdgeStr === null) localStorage.removeItem(storageKey);
      else localStorage.setItem(storageKey, activeEdgeStr);
    } catch {}
  }, [storageKey, activeEdgeStr]);

  // Re-read from storage when the key changes (dataset switch without remount).
  useEffect(() => {
    if (!storageKey) {
      setActiveEdgeStr(null);
      setDraft(defaultEdgeStr ?? "");
      return;
    }
    try {
      const stored = localStorage.getItem(storageKey) || null;
      setActiveEdgeStr(stored);
      setDraft(stored ?? defaultEdgeStr ?? "");
    } catch {
      setActiveEdgeStr(null);
      setDraft(defaultEdgeStr ?? "");
    }
  }, [storageKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const isCustom = activeEdgeStr !== null;

  const displayEntries = (() => {
    if (!canEdit || activeEdgeStr === null) return entries;
    const edges = activeEdgeStr.split(",").map(s => parseFloat(s.trim())).filter(n => isFinite(n));
    if (!edges.length) return entries;
    return rebucketValues(rawValues!, edges, fb!);
  })();

  const handleApply = () => {
    const edges = draft.split(",").map(s => parseFloat(s.trim())).filter(n => isFinite(n));
    if (edges.length > 0) setActiveEdgeStr(draft.trim());
    setIsEditing(false);
  };

  const handleReset = () => {
    setActiveEdgeStr(null);
    setDraft(defaultEdgeStr ?? "");
    setIsEditing(false);
  };

  return (
    <div className="panel" style={{ padding: "14px 16px" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8, marginBottom: 4 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 500, color: "var(--fg-mute)", display: "flex", alignItems: "center", gap: 5 }}>
            {title}
            {isCustom && (
              <span style={{ fontSize: 9.5, padding: "1px 5px", borderRadius: "var(--r)", background: "var(--accent)", color: "#fff", fontWeight: 600, letterSpacing: ".02em" }}>
                custom
              </span>
            )}
          </div>
          {subtitle && !isEditing && <div style={{ fontSize: 11, color: "var(--fg-soft)", marginTop: 1 }}>{subtitle}</div>}
        </div>
        {canEdit && (
          <button
            className="icon-btn"
            title="Edit bucket edges"
            style={{ flexShrink: 0, opacity: 0.6 }}
            onClick={() => { setDraft(activeEdgeStr ?? defaultEdgeStr ?? ""); setIsEditing(v => !v); }}
          >
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M11.5 2.5a1.414 1.414 0 0 1 2 2L5 13H3v-2L11.5 2.5z"/>
            </svg>
          </button>
        )}
      </div>

      {isEditing && (
        <div style={{ marginBottom: 8, padding: "8px 10px", background: "var(--surface-2)", borderRadius: "var(--r)", border: "1px solid var(--line)" }}>
          <div style={{ fontSize: 11, color: "var(--fg-dim)", marginBottom: 5 }}>Bucket edges — comma-separated numbers</div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              className="input"
              style={{ flex: 1, fontSize: 12 }}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") handleApply(); if (e.key === "Escape") setIsEditing(false); }}
              placeholder="e.g. 4, 6, 8"
              autoFocus
            />
            <button className="btn sm" onClick={handleApply}>Apply</button>
            {isCustom && <button className="btn ghost sm" onClick={handleReset}>Reset</button>}
          </div>
        </div>
      )}

      <CssHist entries={displayEntries} onBarClick={onBarClick} />
      {footer}
    </div>
  );
}

// ─── StatsPage ────────────────────────────────────────────────────────────────
interface StatsFilters { activeSubfolder: string | null; }
const STATS_FILTERS_DEFAULTS: StatsFilters = { activeSubfolder: null };

export default function StatsPage() {
  const datasetId = usePaneDatasetId();
  const [panelFilter, setPanelFilter] = useState<PanelFilter>(null);
  const [activeSubfolder, setActiveSubfolder] = useState<string | undefined>(() => {
    if (!datasetId) return undefined;
    const f = loadPersisted(datasetScopedKey(STATS_FILTERS_PREFIX, datasetId), STATS_FILTERS_DEFAULTS);
    return f.activeSubfolder ?? undefined;
  });
  const { vis, toggleCategory, toggleItem, toggleCollapsed, reset } = useStatsVisibility();
  const [drawerOpen, setDrawerOpen] = useState(false);

  const prevDatasetId = useRef(datasetId);
  useEffect(() => {
    if (datasetId === prevDatasetId.current) return;
    prevDatasetId.current = datasetId;
    const next = datasetId
      ? loadPersisted(datasetScopedKey(STATS_FILTERS_PREFIX, datasetId), STATS_FILTERS_DEFAULTS)
      : STATS_FILTERS_DEFAULTS;
    setActiveSubfolder(next.activeSubfolder ?? undefined);
  }, [datasetId]);

  useDebouncedPersist(
    datasetId ? datasetScopedKey(STATS_FILTERS_PREFIX, datasetId) : null,
    { activeSubfolder: activeSubfolder ?? null },
  );

  const hasActiveJob = useJobStore(s =>
    [...s.activeJobs.values()].some(
      j => j.status === "running" &&
           LIVE_STATS_JOB_TYPES.has(j.job_type) &&
           j.dataset_id === datasetId
    )
  );
  const statsRefetchInterval = hasActiveJob ? 5_000 : false;

  const { data: subfolders = [] } = useQuery<SubfolderInfo[]>({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  const { data: stats, isLoading } = useQuery({
    queryKey: ["dataset-stats", datasetId, activeSubfolder],
    queryFn: () => datasetsApi.stats(datasetId!, activeSubfolder),
    enabled: !!datasetId,
    refetchInterval: statsRefetchInterval,
  });

  const { data: tagStats = [] } = useQuery({
    queryKey: ["tag-stats", datasetId, activeSubfolder],
    queryFn: () => captionsApi.tagStats(datasetId!, activeSubfolder),
    enabled: !!datasetId,
    refetchInterval: statsRefetchInterval,
  });

  const { data: cooccurrence } = useQuery({
    queryKey: ["tag-cooccurrence", datasetId, activeSubfolder],
    queryFn: () => datasetsApi.tagCooccurrence(datasetId!, 15, activeSubfolder),
    enabled: !!datasetId,
    refetchInterval: statsRefetchInterval,
  });

  const { data: sv, isLoading: svLoading } = useQuery<ScoreValues>({
    queryKey: ["score-values", datasetId, activeSubfolder],
    queryFn: () => datasetsApi.scoreValues(datasetId!, activeSubfolder),
    enabled: !!datasetId,
    refetchInterval: statsRefetchInterval,
  });

  const { data: detStats } = useQuery<DetectionStats>({
    queryKey: ["detection-stats", datasetId, activeSubfolder],
    queryFn: () => detectionApi.stats(datasetId!, activeSubfolder),
    enabled: !!datasetId,
    refetchInterval: statsRefetchInterval,
  });

  const { data: thresholds } = useQuery<Thresholds>({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
    staleTime: 60_000,
  });

  const show = (cat: CategoryId, item: ItemId) => vis.categories[cat] && vis.items[item];

  if (isLoading) return <div style={{ padding: 40, color: "var(--fg-mute)" }}>Loading stats…</div>;
  if (!stats) return <div style={{ padding: 40, color: "var(--fg-mute)" }}>No data</div>;

  // Effective-license breakdown, largest bucket first; "" = no license recorded.
  // The backend caps how many buckets it returns and collapses the tail into
  // OTHER_LICENSES_KEY (unbounded `other:` free text would otherwise be one
  // bucket per image), so that entry is a count, not a license id.
  const licenseBreakdown = stats.license_breakdown ?? {};
  const licenseRows = Object.entries(licenseBreakdown)
    .filter(([id]) => id !== OTHER_LICENSES_KEY)
    .map(([id, count]) => ({ id, count }))
    .sort((a, b) => b.count - a.count);
  const otherLicensesCount = licenseBreakdown[OTHER_LICENSES_KEY] ?? 0;
  const unlicensedCount = licenseBreakdown[""] ?? 0;

  // ── Chart data ──
  const blurData      = scoreEntries(stats.blur_distribution,       BLUR_LABELS,  BLUR_EDGES,  "blur_score",       "asc");
  const noiseData     = scoreEntries(stats.noise_distribution,      NOISE_LABELS, NOISE_EDGES, "noise_score",      "desc");
  const uniData       = scoreEntries(stats.uniformity_distribution, UNI_LABELS,   UNI_EDGES,   "uniformity_score", "asc");
  const colorData     = scoreEntries(stats.color_distribution,      COLOR_LABELS, COLOR_EDGES, "color_score",      "asc");
  const satData       = scoreEntries(stats.saturation_distribution, SAT_LABELS,   SAT_EDGES,   "saturation_score", "asc");
  const watermarkData = wmEntries(stats.watermark_distribution);
  const megapixelData = mpEntries(stats.megapixel_distribution);
  const fileSizeData  = fsEntries(stats.file_size_distribution);
  const arData = Object.keys(stats.aspect_ratio_fine).length > 0
    ? arFineEntries(stats.aspect_ratio_fine)
    : arCoarseEntries(stats.aspect_ratio_distribution);
  const aestheticData: ChartEntry[] = Object.entries(stats.score_distribution).map(([name, count]) => ({
    name, count, filter: AESTHETIC_FILTER_MAP[name],
  }));
  const formatData: ChartEntry[] = Object.entries(stats.format_distribution).map(([name, count]) => ({
    name, count, filter: { format_filter: name },
  }));
  const capLenData   = capLenEntries(stats.caption_length_distribution);
  const capTokenData = capTokenEntries(stats.caption_token_distribution);
  const topTags = tagStats.slice(0, 20);
  const maxTagCount = Math.max(...topTags.map((t) => t.count), 1);

  const ssimData = ssimEntries(stats.style_similarity_distribution ?? {});

  // ── Detection chart data ──
  const detLabelData    = detStats ? detLabelEntries(detStats.label_distribution)    : [];
  const detModelData    = detStats ? detModelEntries(detStats.model_distribution)    : [];
  const detScoreData    = detStats ? detScoreEntries(detStats.score_histogram)       : [];
  const detCoverageData = detStats ? detCoverageEntries(detStats.coverage_histogram) : [];
  const detPerImageData = detStats ? detPerImageEntries(detStats.detections_per_image) : [];
  const hasDetections   = (detStats?.total_detections ?? 0) > 0;

  // ── Conditional visibility ──
  const hasQualityScores  = Object.keys(stats.blur_distribution).length > 0;
  const hasFlags          = Object.values(stats.quality_flag_counts).some((v) => v > 0);
  const hasCoverage       = Object.values(stats.score_coverage).some((v) => v > 0);
  const hasCooccurrence   = (cooccurrence?.tags.length ?? 0) > 0;
  const hasStyleSimData   = ssimData.length > 0 || (sv?.style_similarity_score.length ?? 0) > 0;
  const fs = stats.file_size_summary;
  const totalFlagged = Object.values(stats.quality_flag_counts).reduce((a, b) => a + b, 0);

  // ── Click helpers ──
  const open = (title: string) => (entry: ChartEntry) => {
    if (entry.filter) setPanelFilter({ title, params: entry.filter });
  };
  const openFlag = (flag: string, label: string) =>
    setPanelFilter({ title: label, params: { quality_flag: flag } });

  const coverageDefs = [
    { key: "aesthetic",  label: "Aesthetic"  },
    { key: "technical",  label: "Technical"  },
    { key: "watermark",  label: "Watermark"  },
    { key: "embeddings", label: "Embeddings" },
  ];

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      {/* Page header */}
      <div className="page-h" style={{ marginBottom: 20 }}>
        <div>
          <h1>Analytics</h1>
          <p>Dataset quality metrics, score distributions and tag analysis.</p>
        </div>
        <div className="phactions">
          <button className="icon-btn" title="Display settings" onClick={() => setDrawerOpen(true)}>
            <Settings size={15} />
          </button>
          {subfolders.length > 0 && (
            <select
              className="select"
              value={activeSubfolder ?? ""}
              onChange={(e) => setActiveSubfolder(e.target.value === "" ? undefined : e.target.value)}
              style={{ fontSize: 12, height: 30 }}
            >
              <option value="">All subfolders</option>
              {subfolders.map((sf) => (
                <option key={sf.path} value={sf.path}>{sf.path} ({sf.image_count})</option>
              ))}
            </select>
          )}
          <button className="btn" disabled={svLoading} onClick={() => downloadCsv(stats, sv, detStats, datasetId!, stats?.name ?? "")}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M8 2v8M5 7l3 3 3-3M2.5 13.5h11"/>
            </svg>
            Export Stats CSV
          </button>
          <button
            className="btn"
            disabled={tagStats.length === 0}
            onClick={() => downloadTagsCsv(tagStats, datasetId!, stats?.name ?? "")}
            title={tagStats.length === 0 ? "No tags to export" : "Export tag frequency table"}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M8 2v8M5 7l3 3 3-3M2.5 13.5h11"/>
            </svg>
            Export Tags CSV
          </button>
        </div>
      </div>

      {/* 4-col stat cards — always visible */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 22 }}>
        {[
          { k: "Total images",    v: stats.image_count.toLocaleString() },
          { k: "Captioned",       v: `${stats.caption_coverage_pct}%` },
          { k: "Mean aesthetic",  v: meanAesthetic(sv?.aesthetic_score) },
          { k: "Disk usage",      v: formatSize(stats.total_size_mb) },
        ].map(({ k, v }) => (
          <div key={k} className="stat-card">
            <div className="sk">{k}</div>
            <div className="sv">{v}</div>
          </div>
        ))}
      </div>

      {/* ── Summary ─────────────────────────────────────────────────────────── */}
      <CategorySection
        label="Summary"
        isVisible={vis.categories.summary}
        isCollapsed={vis.collapsed.summary}
        onToggleCollapsed={() => toggleCollapsed("summary")}
      >
        {(show("summary", "score_guide") || show("summary", "quality_flags")) && (
          <div style={{ display: "grid", gridTemplateColumns: show("summary", "score_guide") && show("summary", "quality_flags") ? "1.35fr 1fr" : "1fr", gap: 14, marginBottom: 14 }}>
            {show("summary", "score_guide") && (
              <div className="panel">
                <div className="panel-h"><h3>Score guide</h3></div>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid var(--line)" }}>
                        {["Metric", "Range", "Recommended threshold", "Method"].map((h) => (
                          <th key={h} style={{ padding: "6px 16px", textAlign: "left", fontSize: 10.5, color: "var(--fg-dim)", fontWeight: 500, letterSpacing: ".04em", textTransform: "uppercase", whiteSpace: "nowrap" }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {SCORE_GUIDE.map(({ metric, range, threshold, note, cls }) => (
                        <tr key={metric} style={{ borderBottom: "1px solid var(--line)" }}>
                          <td style={{ padding: "9px 16px", fontWeight: 500, whiteSpace: "nowrap", fontSize: 12.5 }}>{metric}</td>
                          <td style={{ padding: "9px 16px", color: "var(--fg-dim)", fontFamily: "Geist Mono, monospace", fontSize: 11 }}>{range}</td>
                          <td style={{ padding: "9px 16px" }}><span className={`badge ${cls} dot`}>{threshold}</span></td>
                          <td style={{ padding: "9px 16px", color: "var(--fg-mute)", fontSize: 11.5 }}>{note}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            {show("summary", "quality_flags") && (
              <div className="panel">
                <div className="panel-h">
                  <h3>Quality flags</h3>
                  <div style={{ flex: 1 }} />
                  {hasFlags && <span className="badge dot warn">{totalFlagged.toLocaleString()} flagged</span>}
                </div>
                <div style={{ padding: "8px 14px 14px", display: "flex", flexDirection: "column", gap: 6 }}>
                  {FLAG_DEFS.map(({ key, flag, label, cls, icon }) => {
                    const n = stats.quality_flag_counts[key] ?? 0;
                    return (
                      <div key={key} className="flag-card" onClick={() => n > 0 && openFlag(flag, `${label} images`)} style={{ opacity: n === 0 ? 0.45 : 1, cursor: n === 0 ? "default" : "pointer" }}>
                        <div className={`fc-icon ${cls}`}>
                          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">{icon}</svg>
                        </div>
                        <div>
                          <div className="fc-name">{label}</div>
                          {thresholds && <div className="fc-desc">{flagHint(key, thresholds)}</div>}
                        </div>
                        <span className="fc-num">{n.toLocaleString()}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
        {show("summary", "licenses") && (
          <div className="panel" style={{ marginBottom: 14 }}>
            <div className="panel-h">
              <h3>Licenses</h3>
              <div style={{ flex: 1 }} />
              {unlicensedCount > 0 && (
                <span className="badge dot warn">{unlicensedCount.toLocaleString()} with no license</span>
              )}
            </div>
            {unlicensedCount > 0 && (
              <p style={{ padding: "10px 16px 0", fontSize: 12, color: "var(--fg-mute)" }}>
                {unlicensedCount.toLocaleString()} image{unlicensedCount !== 1 ? "s have" : " has"} no
                license recorded at either the image or dataset level. Exports include them by
                default, listed as unlicensed in <code>CREDITS.md</code>.
              </p>
            )}
            <div style={{ padding: "10px 16px 14px" }}>
              {licenseRows.length === 0 ? (
                <p style={{ fontSize: 12, color: "var(--fg-mute)" }}>No images.</p>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--fg-dim)", borderBottom: "1px solid var(--line)" }}>
                      <th style={{ padding: "6px 8px", fontWeight: 500 }}>License</th>
                      <th style={{ padding: "6px 8px", fontWeight: 500 }}>Commercial use</th>
                      <th style={{ padding: "6px 8px", fontWeight: 500, textAlign: "right" }}>Images</th>
                      <th style={{ padding: "6px 8px", fontWeight: 500, textAlign: "right" }}>Share</th>
                    </tr>
                  </thead>
                  <tbody>
                    {licenseRows.map(({ id, count }) => {
                      const info = licenseInfo(id);
                      const pct = stats.image_count > 0 ? (count / stats.image_count) * 100 : 0;
                      return (
                        <tr
                          key={id || "__none__"}
                          style={{ borderBottom: "1px solid var(--line)", cursor: "pointer" }}
                          onClick={() =>
                            setPanelFilter({
                              title: id ? `${info.label} images` : "Images with no license",
                              params: id
                                ? { license_filter: JSON.stringify([id]) }
                                : { license_missing: true },
                            })
                          }
                        >
                          <td style={{ padding: "8px" }}>
                            <LicenseBadge value={id} />
                          </td>
                          <td style={{ padding: "8px", color: "var(--fg-mute)" }}>
                            {info.allowsCommercial === null ? "Unknown" : info.allowsCommercial ? "Allowed" : "Not allowed"}
                            {info.requiresAttribution && " · attribution required"}
                          </td>
                          <td style={{ padding: "8px", textAlign: "right", fontFamily: "Geist Mono, monospace" }}>
                            {count.toLocaleString()}
                          </td>
                          <td style={{ padding: "8px", textAlign: "right", color: "var(--fg-dim)", fontFamily: "Geist Mono, monospace" }}>
                            {pct.toFixed(1)}%
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              {otherLicensesCount > 0 && (
                <p style={{ fontSize: 12, color: "var(--fg-mute)", marginTop: 8 }}>
                  + {otherLicensesCount.toLocaleString()} image
                  {otherLicensesCount !== 1 ? "s" : ""} across smaller license buckets, not listed
                  individually. Filter the gallery by license to see them.
                </p>
              )}
            </div>
          </div>
        )}
        {show("summary", "score_coverage") && hasCoverage && (
          <div className="panel" style={{ marginBottom: 14 }}>
            <div className="panel-h"><h3>Score coverage</h3></div>
            <div style={{ padding: "10px 16px 14px", display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
              {coverageDefs.map(({ key, label }) => {
                const n = stats.score_coverage[key] ?? 0;
                const pct = stats.image_count > 0 ? Math.round((n / stats.image_count) * 100) : 0;
                return (
                  <div key={key} style={{ background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r)", padding: "12px 14px" }}>
                    <div style={{ fontSize: 11, color: "var(--fg-dim)", marginBottom: 6 }}>{label}</div>
                    <div style={{ height: 4, background: "var(--surface-3)", borderRadius: 2, overflow: "hidden", marginBottom: 8 }}>
                      <div style={{ height: "100%", width: `${pct}%`, background: pct === 100 ? "var(--good)" : "var(--accent)", borderRadius: 2 }} />
                    </div>
                    <div style={{ fontSize: 11.5, fontFamily: "Geist Mono, monospace", color: "var(--fg)" }}>
                      {n.toLocaleString()} <span style={{ color: "var(--fg-dim)" }}>/ {stats.image_count.toLocaleString()}</span>
                    </div>
                    <div style={{ fontSize: 10.5, color: "var(--fg-mute)", marginTop: 2 }}>{pct}% scored</div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </CategorySection>

      {/* ── Aesthetic & Style ────────────────────────────────────────────────── */}
      <CategorySection
        label="Aesthetic & Style"
        isVisible={vis.categories.aesthetic}
        isCollapsed={vis.collapsed.aesthetic}
        onToggleCollapsed={() => toggleCollapsed("aesthetic")}
      >
        {(show("aesthetic", "aesthetic_score") || (show("aesthetic", "style_sim") && hasStyleSimData)) && (
          <div style={{ display: "grid", gridTemplateColumns: show("aesthetic", "aesthetic_score") && show("aesthetic", "style_sim") && hasStyleSimData ? "1fr 1fr" : "1fr", gap: 10, marginBottom: 14 }}>
            {show("aesthetic", "aesthetic_score") && (
              <HistPanel
                title="Aesthetic score distribution"
                subtitle="Click a bar to view images in that range"
                entries={aestheticData}
                onBarClick={open("Aesthetic")}
                rawValues={sv?.aesthetic_score}
                defaultEdgeStr={DEFAULT_EDGES.aesthetic}
                fb={mkScore("aesthetic_score", "desc")}
                storageKey={datasetId ? `stats-hist-edges-aesthetic-${datasetId}` : undefined}
              />
            )}
            {show("aesthetic", "style_sim") && hasStyleSimData && (
              <HistPanel
                title="Style similarity distribution"
                subtitle="Cosine similarity to reference images · higher = more consistent style · click to browse"
                entries={ssimData}
                onBarClick={open("Style similarity")}
                rawValues={sv?.style_similarity_score}
                defaultEdgeStr={DEFAULT_EDGES.style_sim}
                fb={mkScore("style_similarity_score", "desc")}
                storageKey={datasetId ? `stats-hist-edges-style_sim-${datasetId}` : undefined}
              />
            )}
          </div>
        )}
        {(show("aesthetic", "color_richness") || show("aesthetic", "saturation")) && (
          <div style={{ display: "grid", gridTemplateColumns: show("aesthetic", "color_richness") && show("aesthetic", "saturation") ? "1fr 1fr" : "1fr", gap: 10, marginBottom: 14 }}>
            {show("aesthetic", "color_richness") && (
              <HistPanel title="Color richness" entries={colorData} onBarClick={open("Color richness")} rawValues={sv?.color_score} defaultEdgeStr={DEFAULT_EDGES.color} fb={mkScore("color_score", "asc")} storageKey={datasetId ? `stats-hist-edges-color-${datasetId}` : undefined} />
            )}
            {show("aesthetic", "saturation") && (
              <HistPanel title="Saturation" entries={satData} onBarClick={open("Saturation")} rawValues={sv?.saturation_score} defaultEdgeStr={DEFAULT_EDGES.saturation} fb={mkScore("saturation_score", "asc")} storageKey={datasetId ? `stats-hist-edges-saturation-${datasetId}` : undefined} />
            )}
          </div>
        )}
      </CategorySection>

      {/* ── Technical Quality ────────────────────────────────────────────────── */}
      <CategorySection
        label="Technical Quality"
        isVisible={vis.categories.technical}
        isCollapsed={vis.collapsed.technical}
        onToggleCollapsed={() => toggleCollapsed("technical")}
      >
        {hasQualityScores && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10, marginBottom: 14 }}>
            {show("technical", "blur")       && <HistPanel title="Blur score"       subtitle="higher = sharper" entries={blurData}      onBarClick={open("Blur score")}      rawValues={sv?.blur_score}       defaultEdgeStr={DEFAULT_EDGES.blur}       fb={mkScore("blur_score", "asc")}       storageKey={datasetId ? `stats-hist-edges-blur-${datasetId}` : undefined} />}
            {show("technical", "noise")      && <HistPanel title="Noise score"      subtitle="lower = cleaner"  entries={noiseData}     onBarClick={open("Noise score")}     rawValues={sv?.noise_score}      defaultEdgeStr={DEFAULT_EDGES.noise}      fb={mkScore("noise_score", "desc")}     storageKey={datasetId ? `stats-hist-edges-noise-${datasetId}` : undefined} />}
            {show("technical", "uniformity") && <HistPanel title="Uniformity score" subtitle="higher = detail"  entries={uniData}       onBarClick={open("Uniformity score")}rawValues={sv?.uniformity_score} defaultEdgeStr={DEFAULT_EDGES.uniformity} fb={mkScore("uniformity_score", "asc")} storageKey={datasetId ? `stats-hist-edges-uniformity-${datasetId}` : undefined} />}
            {show("technical", "watermark")  && <HistPanel title="Watermark score"  subtitle="lower = cleaner"  entries={watermarkData} onBarClick={open("Watermark score")} rawValues={sv?.watermark_score}  defaultEdgeStr={DEFAULT_EDGES.watermark}  fb={mkScore("watermark_score", "asc")}  storageKey={datasetId ? `stats-hist-edges-watermark-${datasetId}` : undefined} />}
          </div>
        )}
      </CategorySection>

      {/* ── Image Properties ─────────────────────────────────────────────────── */}
      <CategorySection
        label="Image Properties"
        isVisible={vis.categories.properties}
        isCollapsed={vis.collapsed.properties}
        onToggleCollapsed={() => toggleCollapsed("properties")}
      >
        {(show("properties", "aspect_ratio") || show("properties", "megapixels") || show("properties", "file_size") || show("properties", "file_formats")) && (
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${[show("properties", "aspect_ratio"), show("properties", "megapixels"), show("properties", "file_size"), show("properties", "file_formats")].filter(Boolean).length}, 1fr)`, gap: 10, marginBottom: 14 }}>
            {show("properties", "aspect_ratio") && <HistPanel title="Aspect ratio" entries={arData} onBarClick={open("Aspect ratio")} />}
            {show("properties", "megapixels")   && <HistPanel title="Megapixels" entries={megapixelData} onBarClick={open("Megapixels")} rawValues={sv?.megapixels} defaultEdgeStr={DEFAULT_EDGES.megapixels} fb={mkMp} storageKey={datasetId ? `stats-hist-edges-megapixels-${datasetId}` : undefined} />}
            {show("properties", "file_size")    && (
              <HistPanel
                title="File size"
                entries={fileSizeData}
                onBarClick={open("File size")}
                rawValues={sv?.file_size_mb}
                defaultEdgeStr={DEFAULT_EDGES.file_size}
                fb={mkFs}
                storageKey={datasetId ? `stats-hist-edges-file_size-${datasetId}` : undefined}
                footer={Object.keys(fs).length > 0 ? (
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6, marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
                    {[
                      { label: "Min",    value: `${fs.min_mb?.toFixed(2)} MB`    },
                      { label: "Median", value: `${fs.median_mb?.toFixed(2)} MB` },
                      { label: "p95",    value: `${fs.p95_mb?.toFixed(2)} MB`    },
                      { label: "Max",    value: `${fs.max_mb?.toFixed(2)} MB`    },
                    ].map(({ label, value }) => (
                      <div key={label} style={{ textAlign: "center" }}>
                        <div style={{ fontSize: 10, color: "var(--fg-dim)" }}>{label}</div>
                        <div style={{ fontSize: 11.5, color: "var(--fg)", fontWeight: 500, marginTop: 2, fontFamily: "Geist Mono, monospace" }}>{value}</div>
                      </div>
                    ))}
                  </div>
                ) : undefined}
              />
            )}
            {show("properties", "file_formats") && <HistPanel title="File formats" subtitle="Click a bar to view images with that format" entries={formatData} onBarClick={open("Format")} />}
          </div>
        )}
      </CategorySection>

      {/* ── Captions & Tags ──────────────────────────────────────────────────── */}
      <CategorySection
        label="Captions & Tags"
        isVisible={vis.categories.captions}
        isCollapsed={vis.collapsed.captions}
        onToggleCollapsed={() => toggleCollapsed("captions")}
      >
        {(show("captions", "caption_wc") || show("captions", "caption_tc")) && (
          <div style={{ display: "grid", gridTemplateColumns: show("captions", "caption_wc") && show("captions", "caption_tc") ? "1fr 1fr" : "1fr", gap: 10, marginBottom: 14 }}>
            {show("captions", "caption_wc") && capLenData.length > 0 && (
              <HistPanel title="Caption word count" entries={capLenData} onBarClick={open("Caption word count")} rawValues={sv?.caption_words} defaultEdgeStr={DEFAULT_EDGES.caption_wc} fb={mkCapWC} storageKey={datasetId ? `stats-hist-edges-caption_wc-${datasetId}` : undefined} />
            )}
            {show("captions", "caption_tc") && capTokenData.length > 0 && (
              <HistPanel title="Caption token count" subtitle="77+ tokens will be truncated by CLIP" entries={capTokenData} onBarClick={open("Caption token count")} rawValues={sv?.caption_tokens} defaultEdgeStr={DEFAULT_EDGES.caption_tc} fb={mkCapTC} storageKey={datasetId ? `stats-hist-edges-caption_tc-${datasetId}` : undefined} />
            )}
          </div>
        )}
        {topTags.length > 0 && (show("captions", "top_tags") || show("captions", "cooccurrence")) && (
          <div style={{ display: "grid", gridTemplateColumns: show("captions", "top_tags") && show("captions", "cooccurrence") && hasCooccurrence ? "1fr 1.4fr" : "1fr", gap: 14, marginBottom: 14 }}>
            {show("captions", "top_tags") && (
              <div className="panel">
                <div className="panel-h"><h3>Top 20 tags</h3></div>
                <div style={{ padding: "8px 16px 14px", display: "flex", flexDirection: "column", gap: 4 }}>
                  {topTags.map((t) => (
                    <div key={t.tag} style={{ display: "grid", gridTemplateColumns: "120px 1fr auto", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 11.5, color: "var(--fg-mute)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "right" }}>{t.tag}</span>
                      <div style={{ height: 6, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
                        <div style={{ height: "100%", width: `${(t.count / maxTagCount) * 100}%`, background: "linear-gradient(90deg, var(--accent-2), var(--accent))", borderRadius: 3 }} />
                      </div>
                      <span style={{ fontSize: 11, color: "var(--fg-dim)", fontFamily: "Geist Mono, monospace", minWidth: 36, textAlign: "right" }}>{t.count.toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {show("captions", "cooccurrence") && hasCooccurrence && (
              <div className="panel">
                <div className="panel-h">
                  <h3>Tag co-occurrence</h3>
                  <span className="mono" style={{ fontSize: 11, color: "var(--fg-dim)", marginLeft: "auto" }}>top 15 tags</span>
                </div>
                <div style={{ padding: "8px 16px 14px" }}>
                  <p style={{ margin: "0 0 10px", fontSize: 11.5, color: "var(--fg-mute)" }}>Cell brightness = how often two tags appear on the same image. Diagonal = single-tag count.</p>
                  <CooccurrenceHeatmap tags={cooccurrence!.tags} matrix={cooccurrence!.matrix} />
                </div>
              </div>
            )}
          </div>
        )}
      </CategorySection>

      {/* ── Detections & Masks ───────────────────────────────────────────────── */}
      <CategorySection
        label="Detections & Masks"
        isVisible={vis.categories.detections}
        isCollapsed={vis.collapsed.detections}
        onToggleCollapsed={() => toggleCollapsed("detections")}
      >
        {!hasDetections ? (
          <div className="panel" style={{ padding: "24px 16px", textAlign: "center", color: "var(--fg-mute)", fontSize: 12.5, marginBottom: 14 }}>
            No detections in this {activeSubfolder ? "subfolder" : "dataset"} yet. Run object detection or draw masks to populate this section.
          </div>
        ) : (
          <>
            {show("detections", "det_overview") && detStats && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, marginBottom: 14 }}>
                {[
                  { k: "Total detections", v: detStats.total_detections.toLocaleString() },
                  { k: "Images w/ detections", v: `${detStats.images_with_detections.toLocaleString()} · ${detStats.total_images > 0 ? Math.round((detStats.images_with_detections / detStats.total_images) * 100) : 0}%` },
                  { k: "Distinct labels", v: detStats.distinct_labels.toLocaleString() },
                  { k: "Bbox-only (no mask)", v: detStats.bbox_only_count.toLocaleString() },
                  { k: "Images w/o detections", v: detStats.images_without_detections.toLocaleString() },
                ].map(({ k, v }) => (
                  <div key={k} className="stat-card">
                    <div className="sk">{k}</div>
                    <div className="sv" style={{ fontSize: 15 }}>{v}</div>
                  </div>
                ))}
              </div>
            )}

            {(show("detections", "det_labels") || show("detections", "det_models")) && (
              <div style={{ display: "grid", gridTemplateColumns: show("detections", "det_labels") && show("detections", "det_models") ? "1fr 1fr" : "1fr", gap: 10, marginBottom: 14 }}>
                {show("detections", "det_labels") && (
                  <HistPanel title="Label distribution" subtitle="Top 30 labels · click a bar to view matching images" entries={detLabelData} onBarClick={open("Detections")} />
                )}
                {show("detections", "det_models") && (
                  <HistPanel title="Model breakdown" subtitle="Detections by source model" entries={detModelData} onBarClick={() => {}} />
                )}
              </div>
            )}

            {(show("detections", "det_score") || show("detections", "det_coverage")) && (
              <div style={{ display: "grid", gridTemplateColumns: show("detections", "det_score") && show("detections", "det_coverage") ? "1fr 1fr" : "1fr", gap: 10, marginBottom: 14 }}>
                {show("detections", "det_score") && (
                  <HistPanel title="Detection score" subtitle="Confidence · &ldquo;unscored&rdquo; = manual rows · click to browse" entries={detScoreData} onBarClick={open("Detection score")} />
                )}
                {show("detections", "det_coverage") && (
                  <HistPanel title="Mask coverage" subtitle="Approx. % of image covered · &lt;2% or &gt;95% often flag bad masks" entries={detCoverageData} onBarClick={open("Mask coverage")} />
                )}
              </div>
            )}

            {show("detections", "det_per_image") && (
              <div style={{ marginBottom: 14 }}>
                <HistPanel title="Detections per image" subtitle="Click a bar to view images with that many detections" entries={detPerImageData} onBarClick={open("Detections per image")} />
              </div>
            )}
          </>
        )}
      </CategorySection>

      {/* Settings drawer */}
      {drawerOpen && (
        <SettingsDrawer
          categories={vis.categories}
          items={vis.items}
          onToggleCategory={toggleCategory}
          onToggleItem={toggleItem}
          onReset={reset}
          onClose={() => setDrawerOpen(false)}
        />
      )}

      {/* BucketPanel */}
      {panelFilter && (
        <BucketPanel
          title={panelFilter.title}
          params={panelFilter.params}
          datasetId={datasetId!}
          onClose={() => setPanelFilter(null)}
          subfolder={activeSubfolder}
        />
      )}

      <style>{`.delete-btn { opacity: 0; } .group:hover .delete-btn { opacity: 1; }`}</style>
    </div>
  );
}
