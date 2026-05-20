import { useState, useCallback, useEffect, useRef } from "react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { imagesApi } from "../api/images";
import type { ImageListItem, SubfolderInfo } from "../types";
import GenerationMetadata from "../components/image/GenerationMetadata";
import ConfirmDialog from "../components/common/ConfirmDialog";
import { datasetsApi } from "../api/datasets";
import ImageCard from "../components/gallery/ImageCard";
import SelectionToolbar from "../components/gallery/SelectionToolbar";
import { useSelectionStore } from "../store/selectionStore";

const SORT_OPTIONS = [
  { label: "Newest first", sort: "created_at", order: "desc" },
  { label: "Oldest first", sort: "created_at", order: "asc" },
  { label: "Aesthetic ↓", sort: "aesthetic_score", order: "desc" },
  { label: "Aesthetic ↑", sort: "aesthetic_score", order: "asc" },
  { label: "Name A-Z", sort: "filename", order: "asc" },
  { label: "Style similarity ↓", sort: "style_similarity_score", order: "desc" },
  { label: "Colorfulness ↓", sort: "color_score", order: "desc" },
];

type QualityFilter = "" | "is_blurry" | "is_noisy" | "is_uniform" | "has_watermark" | "is_duplicate";

interface ScoreFilter { field: string; min: string; max: string; }

const SCORE_FIELDS = [
  { value: "aesthetic_score",        label: "Aesthetic (1–10)",  short: "Aesthetic"  },
  { value: "watermark_score",        label: "Watermark (0–1)",   short: "Watermark"  },
  { value: "style_similarity_score", label: "Style sim. (0–1)",  short: "Style sim." },
  { value: "blur_score",             label: "Blur",              short: "Blur"       },
  { value: "noise_score",            label: "Noise",             short: "Noise"      },
  { value: "uniformity_score",       label: "Uniformity",        short: "Uniformity" },
  { value: "color_score",            label: "Color",             short: "Color"      },
  { value: "saturation_score",       label: "Saturation",        short: "Saturation" },
];

function scoreChipLabel(f: ScoreFilter): string {
  const short = SCORE_FIELDS.find(s => s.value === f.field)?.short ?? f.field;
  if (f.min && f.max) return `${short}: ${f.min}–${f.max}`;
  if (f.min) return `${short} ≥ ${f.min}`;
  if (f.max) return `${short} ≤ ${f.max}`;
  return short;
}

function loadSavedState(datasetId: string) {
  try {
    const raw = sessionStorage.getItem(`gallery-state-${datasetId}`);
    if (raw) return JSON.parse(raw) as { page: number; sortIdx: number; captionedFilter: boolean | null; scrollTop: number };
  } catch {}
  return null;
}

export default function GalleryPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();
  const { selectAll, clear, count } = useSelectionStore();

  const saved = datasetId ? loadSavedState(datasetId) : null;
  const [page, setPage] = useState(saved?.page ?? 1);
  const [sortIdx, setSortIdx] = useState(saved?.sortIdx ?? 0);
  const [captionedFilter, setCaptionedFilter] = useState<boolean | undefined>(
    saved?.captionedFilter == null ? undefined : saved.captionedFilter
  );
  const [qualityFilter, setQualityFilter] = useState<QualityFilter>("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [detectionLabelInput, setDetectionLabelInput] = useState("");
  const [detectionLabel, setDetectionLabel] = useState("");
  const [scoreFilters, setScoreFilters] = useState<ScoreFilter[]>([]);
  const [showAddScore, setShowAddScore] = useState(false);
  const [draftField, setDraftField] = useState(SCORE_FIELDS[0].value);
  const [draftMin, setDraftMin] = useState("");
  const [draftMax, setDraftMax] = useState("");
  const [uploading, setUploading] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const [genMetaImage, setGenMetaImage] = useState<ImageListItem | null>(null);
  const [activeSubfolder, setActiveSubfolder] = useState<string | undefined>(undefined);
  const [uploadSubfolder, setUploadSubfolder] = useState("");
  const [showCreateSubfolder, setShowCreateSubfolder] = useState(false);
  const [newSubfolderName, setNewSubfolderName] = useState("");
  const [pendingDeleteSubfolder, setPendingDeleteSubfolder] = useState<SubfolderInfo | null>(null);

  const sortOpt = SORT_OPTIONS[sortIdx];
  const scrollRef = useRef<HTMLDivElement>(null);
  const hasRestoredScroll = useRef(false);
  const liveState = useRef({ page, sortIdx, captionedFilter });
  liveState.current = { page, sortIdx, captionedFilter };

  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
      hasRestoredScroll.current = false;
    }, 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    const t = setTimeout(() => {
      setDetectionLabel(detectionLabelInput);
      setPage(1);
      hasRestoredScroll.current = false;
    }, 350);
    return () => clearTimeout(t);
  }, [detectionLabelInput]);

  useEffect(() => {
    return () => {
      const scrollTop = scrollRef.current?.scrollTop ?? 0;
      const { page, sortIdx, captionedFilter } = liveState.current;
      if (datasetId) {
        sessionStorage.setItem(
          `gallery-state-${datasetId}`,
          JSON.stringify({ page, sortIdx, captionedFilter: captionedFilter ?? null, scrollTop })
        );
      }
    };
  }, [datasetId]);

  const { data: dataset } = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => datasetsApi.get(datasetId!),
    enabled: !!datasetId,
  });

  const { data: subfolders = [] } = useQuery<SubfolderInfo[]>({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  useEffect(() => {
    setUploadSubfolder(activeSubfolder ?? "");
  }, [activeSubfolder]);

  const scoreFiltersParam = scoreFilters.length > 0
    ? JSON.stringify(scoreFilters.map(f => ({
        field: f.field,
        min: f.min !== "" ? parseFloat(f.min) : undefined,
        max: f.max !== "" ? parseFloat(f.max) : undefined,
      })))
    : undefined;

  const { data: images = [], isLoading, refetch } = useQuery({
    queryKey: ["images", datasetId, page, sortOpt, captionedFilter, qualityFilter, search, scoreFiltersParam, activeSubfolder, detectionLabel],
    queryFn: () =>
      imagesApi.list({
        dataset_id: datasetId!,
        page,
        limit: 100,
        sort: sortOpt.sort,
        order: sortOpt.order,
        captioned: captionedFilter,
        search: search || undefined,
        quality_flag: qualityFilter || undefined,
        score_filters: scoreFiltersParam,
        subfolder: activeSubfolder,
        detection_label: detectionLabel || undefined,
      }),
    enabled: !!datasetId,
    placeholderData: keepPreviousData,
  });

  useEffect(() => {
    if (!isLoading && images.length > 0 && !hasRestoredScroll.current && scrollRef.current && saved?.scrollTop) {
      hasRestoredScroll.current = true;
      scrollRef.current.scrollTop = saved.scrollTop;
    }
  }, [isLoading, images.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (images.length > 0 && datasetId) {
      sessionStorage.setItem(
        `gallery-nav-${datasetId}`,
        JSON.stringify({ ids: images.map((i) => i.id), page, sort: sortOpt.sort, order: sortOpt.order, captionedFilter: captionedFilter ?? null })
      );
    }
  }, [images, datasetId, page, sortOpt, captionedFilter]);

  const handleUpload = useCallback(async (files: FileList, sf?: string) => {
    if (!datasetId) return;
    setUploading(true);
    try {
      await imagesApi.upload(datasetId, Array.from(files), sf ?? uploadSubfolder);
      await refetch();
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      toast.success(`Uploaded ${files.length} image(s)`);
    } catch {
      toast.error("Upload failed");
    } finally {
      setUploading(false);
    }
  }, [datasetId, refetch, qc, uploadSubfolder]);

  const createSubfolderMutation = useMutation({
    mutationFn: (path: string) => datasetsApi.createSubfolder(datasetId!, path),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      setShowCreateSubfolder(false);
      setNewSubfolderName("");
      setActiveSubfolder(data.path);
      toast.success(`Created subfolder "${data.path}"`);
    },
    onError: () => toast.error("Failed to create subfolder"),
  });

  const deleteSubfolderMutation = useMutation({
    mutationFn: (path: string) => datasetsApi.deleteSubfolder(datasetId!, path),
    onSuccess: (_data, path) => {
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setPendingDeleteSubfolder(null);
      if (activeSubfolder === path) { setActiveSubfolder(undefined); resetPage(); }
      toast.success(`Deleted subfolder "${path}"`);
    },
    onError: () => toast.error("Failed to delete subfolder"),
  });

  const handleDragEnter = (e: React.DragEvent) => {
    if (e.dataTransfer.types.includes("Files")) {
      e.preventDefault();
      setIsDragOver(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    const related = e.relatedTarget as Node | null;
    if (!related || !e.currentTarget.contains(related)) setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files.length) handleUpload(e.dataTransfer.files);
  };

  const flaggedCount = dataset ? (dataset.image_count - dataset.captioned_count) : 0; // placeholder

  const resetPage = () => { setPage(1); hasRestoredScroll.current = false; };

  const applyScoreFilter = () => {
    if (!draftMin && !draftMax) return;
    setScoreFilters(prev => [...prev, { field: draftField, min: draftMin, max: draftMax }]);
    setDraftMin("");
    setDraftMax("");
    setShowAddScore(false);
    resetPage();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Page header */}
      <div style={{ padding: "18px 28px 0", flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, marginBottom: 0 }}>
          <div>
            <h1 style={{ margin: "0 0 4px", fontSize: 22, fontWeight: 600, letterSpacing: "-.02em" }}>{dataset?.name ?? "Gallery"}</h1>
            <p style={{ margin: 0, color: "var(--fg-mute)", fontSize: 13 }}>
              {dataset?.description && <>{dataset.description} · </>}
              {dataset?.image_count ?? 0} images
            </p>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0, paddingTop: 4 }}>
            <span className="badge dot good">{dataset?.image_count ?? 0} images</span>
            <span className="badge dot info">{dataset?.captioned_count ?? 0} captioned</span>
            {flaggedCount > 0 && <span className="badge dot warn">{flaggedCount} uncaptioned</span>}
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <div style={{
        padding: "14px 28px", borderBottom: "1px solid var(--line)",
        display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        background: "var(--surface-1)", flexShrink: 0,
      }}>
        <div className="search-wrap">
          <svg className="search-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5l3 3"/>
          </svg>
          <input
            className="input"
            placeholder="Search filename or caption…"
            style={{ width: 280 }}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>

        <select className="select" style={{ width: "auto" }} value={sortIdx}
          onChange={(e) => { setSortIdx(Number(e.target.value)); resetPage(); }}>
          {SORT_OPTIONS.map((o, i) => <option key={i} value={i}>{o.label}</option>)}
        </select>

        <select className="select" style={{ width: "auto" }}
          value={captionedFilter === undefined ? "" : String(captionedFilter)}
          onChange={(e) => { const v = e.target.value; setCaptionedFilter(v === "" ? undefined : v === "true"); resetPage(); }}>
          <option value="">All images</option>
          <option value="true">Captioned only</option>
          <option value="false">Uncaptioned</option>
        </select>

        <select className="select" style={{ width: "auto" }} value={qualityFilter}
          onChange={(e) => { setQualityFilter(e.target.value as QualityFilter); resetPage(); }}>
          <option value="">All quality</option>
          <option value="is_blurry">Flagged: blurry</option>
          <option value="is_noisy">Flagged: noisy</option>
          <option value="is_uniform">Flagged: near-uniform</option>
          <option value="has_watermark">Flagged: watermark</option>
          <option value="is_duplicate">Flagged: duplicate</option>
        </select>

        <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"
            style={{ position: "absolute", left: 8, width: 13, height: 13, color: "var(--fg-mute)", pointerEvents: "none" }}>
            <rect x="2" y="3" width="12" height="10" rx="1.5"/>
            <circle cx="8" cy="8" r="2.5"/>
          </svg>
          <input
            className="input"
            placeholder="Objects: cat, dog…"
            style={{ paddingLeft: 26, width: 160 }}
            value={detectionLabelInput}
            onChange={(e) => setDetectionLabelInput(e.target.value)}
            title="Filter by detected object label"
          />
          {detectionLabelInput && (
            <button
              onClick={() => { setDetectionLabelInput(""); setDetectionLabel(""); resetPage(); }}
              style={{ position: "absolute", right: 6, background: "none", border: "none", cursor: "pointer", color: "var(--fg-mute)", fontSize: 14, lineHeight: 1, padding: 0 }}
              title="Clear"
            >×</button>
          )}
        </div>

        {/* Multi-score filters */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {scoreFilters.map((f, i) => (
            <span
              key={i}
              style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "2px 6px 2px 8px", borderRadius: "var(--r)",
                background: "var(--surface-3)", border: "1px solid var(--accent)",
                fontSize: 12, color: "var(--fg)", whiteSpace: "nowrap",
              }}
            >
              {scoreChipLabel(f)}
              <button
                onClick={() => { setScoreFilters(prev => prev.filter((_, j) => j !== i)); resetPage(); }}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--fg-mute)", padding: "0 1px", fontSize: 14, lineHeight: 1, display: "flex", alignItems: "center" }}
                title="Remove filter"
              >×</button>
            </span>
          ))}

          {showAddScore ? (
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <select
                className="select"
                style={{ width: "auto" }}
                value={draftField}
                onChange={e => setDraftField(e.target.value)}
              >
                {SCORE_FIELDS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
              </select>
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>≥</span>
              <input
                className="input"
                type="number"
                placeholder="min"
                value={draftMin}
                onChange={e => setDraftMin(e.target.value)}
                style={{ width: 62 }}
                onKeyDown={e => {
                  if (e.key === "Enter") applyScoreFilter();
                  if (e.key === "Escape") { setShowAddScore(false); setDraftMin(""); setDraftMax(""); }
                }}
              />
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>≤</span>
              <input
                className="input"
                type="number"
                placeholder="max"
                value={draftMax}
                onChange={e => setDraftMax(e.target.value)}
                style={{ width: 62 }}
                onKeyDown={e => {
                  if (e.key === "Enter") applyScoreFilter();
                  if (e.key === "Escape") { setShowAddScore(false); setDraftMin(""); setDraftMax(""); }
                }}
              />
              <button className="btn sm" onClick={applyScoreFilter} disabled={!draftMin && !draftMax}>Apply</button>
              <button className="icon-btn" style={{ fontSize: 14 }} onClick={() => { setShowAddScore(false); setDraftMin(""); setDraftMax(""); }}>×</button>
            </div>
          ) : (
            <button
              className="btn ghost sm"
              onClick={() => setShowAddScore(true)}
              style={{ whiteSpace: "nowrap" }}
            >
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M8 2v12M2 8h12"/>
              </svg>
              Score filter
            </button>
          )}
        </div>

        <div style={{ flex: 1 }} />

        {subfolders.length === 0 && !showCreateSubfolder && (
          <button
            className="btn ghost sm"
            onClick={() => { setShowCreateSubfolder(true); setNewSubfolderName(""); }}
            style={{ whiteSpace: "nowrap" }}
          >
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M8 2v12M2 8h12"/>
            </svg>
            Subfolder
          </button>
        )}

        <button
          className="btn ghost sm"
          onClick={() => count === images.length ? clear() : selectAll(images.map(i => i.id))}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/>
          </svg>
          {count === images.length && images.length > 0 ? "Deselect all" : "Select all"}
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          {subfolders.length > 0 && (
            <select
              className="select"
              style={{ width: "auto", maxWidth: 120 }}
              value={uploadSubfolder}
              onChange={(e) => setUploadSubfolder(e.target.value)}
              title="Upload to subfolder"
            >
              <option value="">(root)</option>
              {subfolders.filter(sf => sf.path !== "").map(sf => (
                <option key={sf.path} value={sf.path}>{sf.path}</option>
              ))}
            </select>
          )}
          <label className="btn" style={{ cursor: "pointer" }}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M8 10V2M5 5l3-3 3 3M2.5 13.5h11"/>
            </svg>
            {uploading ? "Uploading…" : "Upload"}
            <input type="file" multiple accept="image/*" style={{ display: "none" }}
              onChange={(e) => e.target.files && handleUpload(e.target.files)} />
          </label>
        </div>
      </div>

      {/* Grid area with subfolder sidebar */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {/* Subfolder sidebar */}
        {(subfolders.length > 0 || showCreateSubfolder) && (
          <div style={{
            width: 180, flexShrink: 0, borderRight: "1px solid var(--line)",
            overflowY: "auto", padding: "10px 6px",
            background: "var(--surface-1)",
          }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "2px 8px 6px" }}>
              <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".08em", color: "var(--fg-mute)", textTransform: "uppercase" }}>
                Subfolders
              </span>
              <button
                className="icon-btn"
                style={{ width: 20, height: 20, fontSize: 16, lineHeight: 1 }}
                title="Create subfolder"
                onClick={() => { setShowCreateSubfolder(true); setNewSubfolderName(""); }}
              >+</button>
            </div>
            {showCreateSubfolder && (
              <div style={{ padding: "0 6px 6px" }}>
                <input
                  className="input"
                  style={{ width: "100%", fontSize: 12, padding: "3px 6px" }}
                  placeholder="characters/poses"
                  value={newSubfolderName}
                  onChange={(e) => setNewSubfolderName(e.target.value)}
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && newSubfolderName.trim()) createSubfolderMutation.mutate(newSubfolderName.trim());
                    if (e.key === "Escape") { setShowCreateSubfolder(false); setNewSubfolderName(""); }
                  }}
                />
                <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
                  <button
                    className="btn sm"
                    style={{ flex: 1, fontSize: 11 }}
                    disabled={!newSubfolderName.trim() || createSubfolderMutation.isPending}
                    onClick={() => createSubfolderMutation.mutate(newSubfolderName.trim())}
                  >Create</button>
                  <button
                    className="btn ghost sm"
                    style={{ fontSize: 11 }}
                    onClick={() => { setShowCreateSubfolder(false); setNewSubfolderName(""); }}
                  >✕</button>
                </div>
              </div>
            )}
            {/* "All" entry */}
            <button
              onClick={() => { setActiveSubfolder(undefined); resetPage(); }}
              style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                width: "100%", padding: "5px 8px", borderRadius: "var(--r)",
                border: "none", cursor: "pointer", textAlign: "left",
                background: activeSubfolder === undefined ? "var(--surface-3)" : "transparent",
                color: activeSubfolder === undefined ? "var(--accent)" : "var(--fg)",
                fontSize: 12.5, fontWeight: activeSubfolder === undefined ? 600 : 400,
              }}
            >
              <span>All</span>
              <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>{dataset?.image_count ?? ""}</span>
            </button>
            {subfolders.map((sf) => {
              const depth = sf.path === "" ? 0 : (sf.path.split("/").length - 1);
              const label = sf.path === "" ? "(root)" : sf.path.split("/").pop()!;
              const isActive = activeSubfolder === sf.path;
              return (
                <div
                  key={sf.path}
                  className="subfolder-row"
                  style={{
                    display: "flex", alignItems: "center",
                    borderRadius: "var(--r)",
                    background: isActive ? "var(--surface-3)" : "transparent",
                  }}
                >
                  <button
                    onClick={() => { setActiveSubfolder(sf.path); resetPage(); }}
                    title={sf.path || "(root)"}
                    style={{
                      display: "flex", alignItems: "center", justifyContent: "space-between",
                      flex: 1, minWidth: 0, padding: "5px 4px 5px 8px",
                      paddingLeft: 8 + depth * 12,
                      border: "none", cursor: "pointer", textAlign: "left",
                      background: "transparent",
                      color: isActive ? "var(--accent)" : "var(--fg)",
                      fontSize: 12.5, fontWeight: isActive ? 600 : 400,
                    }}
                  >
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{label}</span>
                    <span style={{ fontSize: 11, color: "var(--fg-mute)", marginLeft: 4, flexShrink: 0 }}>{sf.image_count}</span>
                  </button>
                  <button
                    className="subfolder-delete-btn"
                    title={`Delete "${sf.path || "(root)"}"`}
                    onClick={(e) => { e.stopPropagation(); setPendingDeleteSubfolder(sf); }}
                    style={{
                      flexShrink: 0, width: 18, height: 18, padding: 0,
                      border: "none", cursor: "pointer", borderRadius: "var(--r)",
                      background: "transparent", color: "var(--fg-mute)",
                      fontSize: 13, lineHeight: 1, display: "flex", alignItems: "center", justifyContent: "center",
                      marginRight: 4,
                    }}
                  >×</button>
                </div>
              );
            })}
          </div>
        )}

        {/* Main grid column */}
        <div style={{ flex: 1, position: "relative", minHeight: 0 }}>
        {isDragOver && (
          <div style={{
            position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none",
            background: "rgba(0,0,0,0.55)", border: "2px dashed var(--accent)",
            borderRadius: "var(--r-lg)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <div style={{ textAlign: "center" }}>
              <svg width="40" height="40" viewBox="0 0 16 16" fill="none" stroke="var(--accent)" strokeWidth="1.2">
                <path d="M8 10V2M5 5l3-3 3 3M2.5 13.5h11"/>
              </svg>
              <p style={{ margin: "12px 0 0", color: "var(--accent)", fontSize: 15, fontWeight: 600 }}>
                Drop images to upload
              </p>
            </div>
          </div>
        )}
        <div
          ref={scrollRef}
          style={{ height: "100%", overflowY: "auto", padding: "18px 28px" }}
          onDragEnter={handleDragEnter}
          onDragOver={(e) => e.preventDefault()}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
        {isLoading ? (
          <div style={{ textAlign: "center", marginTop: 80, color: "var(--fg-mute)" }}>Loading…</div>
        ) : images.length === 0 ? (
          <div className="empty-state">
            <p>No images found. Upload or adjust filters.</p>
            <label className="btn primary" style={{ cursor: "pointer" }}>
              Upload images
              <input type="file" multiple accept="image/*" style={{ display: "none" }}
                onChange={(e) => e.target.files && handleUpload(e.target.files)} />
            </label>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 12 }}>
            {images.map((img) => (
              <ImageCard
                key={img.id}
                image={img}
                onShowGenMeta={img.generation_metadata ? setGenMetaImage : undefined}
              />
            ))}
          </div>
        )}

        {/* Pagination */}
        {(page > 1 || images.length === 100) && (
          <div style={{ display: "flex", justifyContent: "center", gap: 12, marginTop: 24 }}>
            {page > 1 && <button className="btn" onClick={() => setPage(p => p - 1)}>← Previous</button>}
            <span style={{ alignSelf: "center", fontSize: 12, color: "var(--fg-mute)" }}>Page {page}</span>
            {images.length === 100 && <button className="btn" onClick={() => setPage(p => p + 1)}>Next →</button>}
          </div>
        )}

        {/* Selection bar (sticky bottom within scroll area) */}
        <SelectionToolbar datasetId={datasetId!} subfolders={subfolders} />
        </div>
        </div>
      </div>

      {/* Generation metadata modal */}
      {genMetaImage?.generation_metadata && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 50,
            background: "rgba(0,0,0,.6)", backdropFilter: "blur(4px)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
          onClick={() => setGenMetaImage(null)}
        >
          <div
            style={{
              background: "var(--surface-1)", border: "1px solid var(--line)",
              borderRadius: "var(--r-lg)", padding: "16px 20px",
              width: 480, maxWidth: "90vw", maxHeight: "80vh", overflowY: "auto",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: "var(--fg)" }}>
                {genMetaImage.filename}
              </span>
              <button
                className="icon-btn"
                style={{ width: 24, height: 24 }}
                onClick={() => setGenMetaImage(null)}
              >
                ×
              </button>
            </div>
            <GenerationMetadata metadata={genMetaImage.generation_metadata} />
          </div>
        </div>
      )}

      {pendingDeleteSubfolder && (
        <ConfirmDialog
          title={`Delete "${pendingDeleteSubfolder.path || "(root)"}"`}
          message={
            pendingDeleteSubfolder.image_count > 0
              ? `This subfolder contains ${pendingDeleteSubfolder.image_count} image${pendingDeleteSubfolder.image_count !== 1 ? "s" : ""}. They will be moved to root (ungrouped). The images themselves will not be deleted.`
              : "This empty subfolder will be removed."
          }
          confirmLabel="Delete Subfolder"
          danger
          onConfirm={() => deleteSubfolderMutation.mutate(pendingDeleteSubfolder.path)}
          onCancel={() => setPendingDeleteSubfolder(null)}
        />
      )}
    </div>
  );
}
