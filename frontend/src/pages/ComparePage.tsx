import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Minus, Plus } from "lucide-react";
import { imagesApi } from "../api/images";
import type { CompareImage } from "../types";
import { COMPARE_IMAGES_KEY } from "../constants/queryKeys";
import { COMPARE_PREFS_KEY } from "../constants/storage";
import { usePaneCompareIds } from "../hooks/usePaneDatasetId";
import { usePaneNavigate } from "../hooks/usePaneNavigate";
import { useOpenBeside } from "../hooks/useOpenBeside";
import { ZOOM_MAX, useLinkedViewport, useViewportZoom } from "../hooks/useLinkedViewport";
import type { Geometry, Size } from "../hooks/useLinkedViewport";
import { loadPersisted, savePersisted } from "../utils/persistentState";
import { apiErrorDetail } from "../utils/apiError";
import { AB_HOWS, COMPARE_MAX, COMPARE_SIDE_MAX, compareUrl, compareView } from "../utils/compare";
import type { AbHow } from "../utils/compare";
import ComparePane from "../components/compare/ComparePane";
import ABView from "../components/compare/ABView";
import type { BestMarks } from "../components/compare/MetaStrip";

type CompareMode = "side" | "ab";
interface ComparePrefs { mode: CompareMode; abHow: AbHow; matchPixels: boolean; pixelated: boolean }
const DEFAULT_PREFS: ComparePrefs = { mode: "side", abHow: "swipe", matchPixels: false, pixelated: false };
const MODES: { value: CompareMode; label: string }[] = [
  { value: "side", label: "Side by side" },
  { value: "ab", label: "A/B" },
];

/**
 * The Compare page (#130, docs/dev/gallery/compare.md): up to four images side
 * by side with linked zoom and pan. Reached only from a Compare button, never the
 * sidebar (decision 20); its identity is the id list, which this page keeps current
 * as panes are removed or reordered, so a reload or a copied URL shows the same set.
 */
export default function ComparePage() {
  const ids = usePaneCompareIds();
  const idsKey = ids.join(",");
  // `ids` is a fresh array each render; everything below keys on the joined string.
  // Deduped: the server collapses repeats, and a hand-edited `?ids=a,a,b` would
  // otherwise draw two panes with one React key.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const wanted = useMemo(() => [...new Set(ids)].slice(0, COMPARE_MAX), [idsKey]);
  const { go } = usePaneNavigate();
  const openBeside = useOpenBeside();
  const qc = useQueryClient();

  // Follow every image write, wherever it happens: a crop in the detail view
  // beside this page, a caption save, a scoring job TopBar watches. Each of those
  // invalidates `["images", …]` or `["image", …]`, never this page's key (which no
  // dataset prefix can reach, since a comparison spans datasets), so this listens
  // for those invalidations instead of asking every writer to know about Compare.
  // Batched to one refetch per tick, since a job's completion invalidates several.
  useEffect(() => {
    let queued = false;
    return qc.getQueryCache().subscribe((event) => {
      if (event.type !== "updated" || event.action.type !== "invalidate") return;
      const head = event.query.queryKey[0];
      if ((head !== "images" && head !== "image") || queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        qc.invalidateQueries({ queryKey: [COMPARE_IMAGES_KEY] });
      });
    });
  }, [qc]);

  const query = useQuery({
    queryKey: [COMPARE_IMAGES_KEY, ...wanted],
    queryFn: () => imagesApi.byIds(wanted),
    enabled: wanted.length > 0,
    // Removing or reordering a pane changes the key; showing the previous rows
    // (filtered to the new list below) keeps the other panes on screen meanwhile.
    placeholderData: keepPreviousData,
  });

  const items = useMemo(() => {
    const byId = new Map((query.data ?? []).map((r) => [r.id, r]));
    return wanted.map((id) => byId.get(id)).filter((r): r is CompareImage => !!r);
  }, [query.data, wanted]);
  const shown = items.slice(0, COMPARE_SIDE_MAX);
  const missing = query.data && !query.isPlaceholderData ? wanted.length - items.length : 0;

  const [prefs, setPrefsState] = useState<ComparePrefs>(() => loadPersisted(COMPARE_PREFS_KEY, DEFAULT_PREFS));
  const setPrefs = (patch: Partial<ComparePrefs>) => {
    setPrefsState((p) => {
      const next = { ...p, ...patch };
      savePersisted(COMPARE_PREFS_KEY, next);
      return next;
    });
  };

  const vp = useLinkedViewport();
  const zoom = useViewportZoom(vp);
  const measures = useRef(new Map<string, () => Geometry | null>());
  const registerMeasure = useCallback((id: string, m: (() => Geometry | null) | null) => {
    if (m) measures.current.set(id, m);
    else measures.current.delete(id);
  }, []);

  // A new set of images (not one with a pane removed or moved) starts at fit.
  const prevIds = useRef<string[]>(wanted);
  useEffect(() => {
    const before = new Set(prevIds.current);
    if (!wanted.every((id) => before.has(id))) vp.reset();
    prevIds.current = wanted;
  }, [wanted, vp]);

  const [sizes, setSizes] = useState<Record<string, Size>>({});
  const onSize = useCallback((id: string, s: Size) => {
    setSizes((m) => (m[id]?.w === s.w && m[id]?.h === s.h ? m : { ...m, [id]: s }));
  }, []);
  const refSize = prefs.matchPixels && shown[0] ? sizes[shown[0].id] ?? null : null;

  // A/B's two images: the user's picks while both are still in the set, otherwise
  // the first two. Picks are ids, so a removal or reorder elsewhere cannot point
  // them at a different image.
  const [abPick, setAbPick] = useState<{ a?: string; b?: string }>({});
  const abA = items.find((r) => r.id === abPick.a) ?? items[0];
  const abB = items.find((r) => r.id === abPick.b && r.id !== abA?.id) ?? items.find((r) => r.id !== abA?.id);
  const pickAb = (which: "a" | "b", id: string) => {
    const other = which === "a" ? abB?.id : abA?.id;
    // Choosing the image already on the other side swaps the two.
    const swapTo = id === other ? (which === "a" ? abA?.id : abB?.id) : other;
    setAbPick(which === "a" ? { a: id, b: swapTo } : { a: swapTo, b: id });
  };
  const abMode = prefs.mode === "ab";

  const best = useMemo(() => bestMarks(shown), [shown]);
  const showDataset = new Set(shown.map((r) => r.dataset_id)).size > 1;

  const setIds = (next: string[]) => go(compareUrl(next), compareView(next), { replace: true });
  const remove = (id: string) => {
    if (items.length <= 2) {
      toast("A comparison needs two images. Open the gallery to choose others.");
      return;
    }
    setIds(ids.filter((x) => x !== id));
  };
  const moveLeft = (id: string) => {
    // Swap with the image shown to its left, wherever the two sit in the full list
    // (an id that no longer exists may sit between them).
    const at = shown.findIndex((r) => r.id === id);
    if (at <= 0) return;
    const left = shown[at - 1].id;
    setIds(ids.map((x) => (x === id ? left : x === left ? id : x)));
  };
  const open = (r: CompareImage) =>
    openBeside(`/datasets/${r.dataset_id}/image/${r.id}`, { page: "image-detail", datasetId: r.dataset_id, imageId: r.id });

  const datasetNames = [...new Set(items.map((r) => r.dataset_name))];
  const subtitle =
    items.length === 0 ? "" :
    `${items.length} image${items.length === 1 ? "" : "s"}` +
    (datasetNames.length === 1 ? ` from ${datasetNames[0]}` : ` from ${datasetNames.length} datasets`);

  let body: React.ReactNode;
  if (wanted.length === 0) {
    body = (
      <div className="empty-state">
        Nothing to compare yet. Select two or more images in the gallery and press Compare.
      </div>
    );
  } else if (query.isPending || (query.isPlaceholderData && items.length < 2)) {
    // The second case is a new set replacing the old one in place: the previous
    // rows, filtered to the new ids, can be empty while the fetch is out, and that
    // is not "none of these exist".
    body = <div className="empty-state">Loading…</div>;
  } else if (query.isError) {
    body = <div className="empty-state">{apiErrorDetail(query.error, "These images could not be loaded.")}</div>;
  } else if (items.length < 2) {
    body = (
      <div className="empty-state">
        {items.length === 0 ? "None of these images exist any more." : "Only one of these images still exists."}{" "}
        Choose others in the gallery.
      </div>
    );
  } else if (abMode && abA && abB) {
    body = (
      <ABView
        key={`${abA.id}:${abB.id}`}
        a={abA}
        b={abB}
        how={prefs.abHow}
        vp={vp}
        pixelated={prefs.pixelated}
        registerMeasure={registerMeasure}
      />
    );
  } else {
    body = (
      <div className={`cmp-panes n${shown.length}`}>
        {shown.map((r, i) => (
          <ComparePane
            key={r.id}
            item={r}
            index={i}
            vp={vp}
            refSize={refSize}
            pixelated={prefs.pixelated}
            best={best.get(r.id) ?? NO_MARKS}
            showDataset={showDataset}
            onSize={onSize}
            registerMeasure={registerMeasure}
            onOpen={() => open(r)}
            onMoveLeft={i > 0 ? () => moveLeft(r.id) : undefined}
            onRemove={() => remove(r.id)}
          />
        ))}
      </div>
    );
  }

  const canZoom = items.length >= 2;
  const notes: string[] = [];
  if (missing > 0) notes.push(`${missing} image${missing === 1 ? " was" : "s were"} not found (deleted, or from another install) and ${missing === 1 ? "is" : "are"} left out.`);
  if (ids.length > COMPARE_MAX) notes.push(`Only the first ${COMPARE_MAX} images are compared.`);
  if (!abMode && items.length > COMPARE_SIDE_MAX) notes.push(`Showing the first ${COMPARE_SIDE_MAX} of ${items.length}. Remove one to bring in the next.`);

  return (
    <div className="cmp-root">
      <div className="cmp-bar">
        <div className="cmp-title">
          <h1>Compare</h1>
          {subtitle && <span className="cmp-sub">{subtitle}</span>}
        </div>
        <div className="cmp-seg" role="group" aria-label="Mode">
          {MODES.map((m) => (
            <button key={m.value} type="button" aria-pressed={prefs.mode === m.value} onClick={() => setPrefs({ mode: m.value })}>
              {m.label}
            </button>
          ))}
        </div>
        {abMode && abA && abB && (
          <>
            <label className="cmp-check">
              A
              <select className="select cmp-pick" value={abA.id} onChange={(e) => pickAb("a", e.target.value)}>
                {items.map((r, i) => <option key={r.id} value={r.id}>{i + 1} · {r.filename}</option>)}
              </select>
            </label>
            <label className="cmp-check">
              B
              <select className="select cmp-pick" value={abB.id} onChange={(e) => pickAb("b", e.target.value)}>
                {items.map((r, i) => <option key={r.id} value={r.id}>{i + 1} · {r.filename}</option>)}
              </select>
            </label>
            <div className="cmp-seg" role="group" aria-label="How to show B">
              {AB_HOWS.map((h) => (
                <button key={h.value} type="button" aria-pressed={prefs.abHow === h.value} onClick={() => setPrefs({ abHow: h.value })}>
                  {h.label}
                </button>
              ))}
            </div>
          </>
        )}
        {!abMode && (
          <label className="cmp-check">
            <input type="checkbox" checked={prefs.matchPixels} onChange={(e) => setPrefs({ matchPixels: e.target.checked })} />
            Match pixels
          </label>
        )}
        <label className="cmp-check">
          <input type="checkbox" checked={prefs.pixelated} onChange={(e) => setPrefs({ pixelated: e.target.checked })} />
          Pixelated
        </label>
        <span className="cmp-spacer" />
        <div className="cmp-zoom" role="group" aria-label="Zoom">
          <button type="button" className="btn sm" aria-label="Zoom out" disabled={!canZoom || zoom <= 1}
            onClick={() => vp.set({ ...vp.get(), z: vp.get().z / 1.5 })}><Minus size={13} /></button>
          <span className="cmp-zoom-read mono" aria-live="polite">{zoom <= 1.001 ? "Fit" : `×${zoom.toFixed(1)}`}</span>
          <button type="button" className="btn sm" aria-label="Zoom in" disabled={!canZoom || zoom >= ZOOM_MAX}
            onClick={() => vp.set({ ...vp.get(), z: vp.get().z * 1.5 })}><Plus size={13} /></button>
          <button type="button" className="btn sm" disabled={!canZoom} onClick={() => vp.reset()}>Fit</button>
          <button type="button" className="btn sm" disabled={!canZoom} title={abMode ? "Image A at its real pixels" : "The first image at its real pixels"}
            onClick={() => {
              const first = abMode ? abA : shown[0];
              const g = first ? measures.current.get(first.id)?.() : null;
              if (g) vp.set({ ...vp.get(), z: vp.get().z / g.scale });
            }}>100%</button>
        </div>
      </div>
      <div className="cmp-stage">{body}</div>
      <div className="cmp-hint">
        <span>Scroll to zoom · drag to pan · double-click: fit or 100% · with a pane focused: + − 0 and the arrow keys</span>
        {notes.map((n) => <span key={n}>{n}</span>)}
      </div>
    </div>
  );
}

const NO_MARKS: BestMarks = { pixels: false, score: false };

/**
 * Which pane holds the best value of each fact, where the panes differ. Scores are
 * compared only when every scored image used the same aesthetic model: two models'
 * scales are not comparable (the Quality page refuses to rank across them too).
 */
function bestMarks(rows: CompareImage[]): Map<string, BestMarks> {
  const out = new Map<string, BestMarks>();
  const px = rows.map((r) => (r.width ?? 0) * (r.height ?? 0));
  const maxPx = Math.max(...px);
  const pxDiffer = px.some((p) => p !== maxPx) && maxPx > 0;

  const scored = rows.filter((r) => r.aesthetic_score != null);
  const oneScale = new Set(scored.map((r) => r.aesthetic_model)).size === 1;
  const scores = scored.map((r) => r.aesthetic_score as number);
  const maxScore = Math.max(...scores);
  const scoresDiffer = scored.length >= 2 && oneScale && scores.some((s) => s !== maxScore);

  rows.forEach((r, i) => {
    out.set(r.id, {
      pixels: pxDiffer && px[i] === maxPx,
      score: scoresDiffer && r.aesthetic_score === maxScore,
    });
  });
  return out;
}
