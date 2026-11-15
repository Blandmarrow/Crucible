import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";

/**
 * Linked zoom and pan for the Compare page (docs/dev/gallery/compare.md).
 *
 * Every pane shares one view, held in *normalized* coordinates: the point of the
 * picture at the pane's centre (`cx`, `cy`, 0..1 on each axis) and a zoom factor
 * relative to "fit" (`z`, 1 = the whole image fits). Normalized, so a 1024 px
 * source and its 2048 px upscale show the same region of the picture — the
 * comparison an edit needs. Match pixels passes a `ref` size to `geometry`, so
 * every pane takes the first image's scale and a bigger image shows bigger.
 *
 * The view lives outside React state. A wheel tick changes it dozens of times a
 * second, and re-rendering four panes for each would stutter; instead each pane
 * subscribes and writes its own `<img>` style on the next animation frame. Only
 * the zoom readout re-renders, through `useViewportZoom`.
 */

export interface ViewState { cx: number; cy: number; z: number }
export interface Size { w: number; h: number }
export interface Geometry {
  /** The pane's size in CSS pixels. */
  W: number;
  H: number;
  /** Screen pixels per image pixel, as drawn now. `scale` grows in proportion to
   *  the view's `z`, so `z / scale` is the zoom that draws this image at 100%. */
  scale: number;
  /** The drawn image's size and offset inside the pane. */
  w: number;
  h: number;
  tx: number;
  ty: number;
}

export const ZOOM_MAX = 24;
const FIT: ViewState = { cx: 0.5, cy: 0.5, z: 1 };
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Where an image of size `img` sits in a `W`×`H` pane under view `v`. */
export function geometry(W: number, H: number, img: Size, v: ViewState, ref?: Size | null): Geometry {
  const base = ref ?? img;
  const scale = Math.min(W / base.w, H / base.h) * v.z;
  const w = img.w * scale;
  const h = img.h * scale;
  return { W, H, scale, w, h, tx: W / 2 - v.cx * w, ty: H / 2 - v.cy * h };
}

export interface LinkedViewport {
  get(): ViewState;
  set(v: ViewState): void;
  /** Zoom to `z`, keeping the picture point under (`mx`, `my`) where it is. */
  zoomAt(g: Geometry, mx: number, my: number, z: number): void;
  /** Move the picture by (`dx`, `dy`) screen pixels in the pane `g` describes. */
  panBy(g: Geometry, dx: number, dy: number): void;
  reset(): void;
  subscribe(fn: () => void): () => void;
}

function createViewport(): LinkedViewport {
  let view: ViewState = FIT;
  const listeners = new Set<() => void>();
  let frame = 0;

  const notify = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      listeners.forEach((fn) => fn());
    });
  };

  const vp: LinkedViewport = {
    get: () => view,
    set(v) {
      const z = clamp(v.z, 1, ZOOM_MAX);
      // At fit there is nothing to pan, and an off-centre fit would leave the
      // picture hanging to one side after zooming out with the cursor near an edge.
      view = z <= 1.001 ? FIT : { z, cx: clamp(v.cx, 0, 1), cy: clamp(v.cy, 0, 1) };
      notify();
    },
    zoomAt(g, mx, my, z) {
      const nz = clamp(z, 1, ZOOM_MAX);
      const px = (mx - g.tx) / g.w;
      const py = (my - g.ty) / g.h;
      const k = nz / view.z;
      vp.set({ z: nz, cx: px - (mx - g.W / 2) / (g.w * k), cy: py - (my - g.H / 2) / (g.h * k) });
    },
    panBy(g, dx, dy) {
      vp.set({ z: view.z, cx: view.cx - dx / g.w, cy: view.cy - dy / g.h });
    },
    reset: () => vp.set(FIT),
    subscribe(fn) {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
  };
  return vp;
}

/** One shared viewport for the life of the component. */
export function useLinkedViewport(): LinkedViewport {
  const [vp] = useState(createViewport);
  return vp;
}

/** The current zoom factor, re-rendering only when it changes. */
export function useViewportZoom(vp: LinkedViewport): number {
  return useSyncExternalStore(vp.subscribe, () => vp.get().z);
}

interface PaneBinding {
  vp: LinkedViewport;
  /** The image's natural size, once it has loaded; null until then. */
  size: Size | null;
  /** Match pixels: the size every pane takes its scale from. */
  refSize: Size | null;
  /** Called after every layout with the pane's geometry. */
  onLayout?: (g: Geometry) => void;
}

/**
 * Bind one pane: the element `paneRef` receives wheel, drag, double-click and key
 * input, and `imgRef` is positioned to follow the shared view.
 *
 * The wheel listener is attached by hand because React registers `onWheel` as
 * passive, and a passive listener cannot stop the page scrolling under the zoom.
 */
export function useViewportPane(
  paneRef: React.RefObject<HTMLElement | null>,
  imgRef: React.RefObject<HTMLElement | null>,
  { vp, size, refSize, onLayout }: PaneBinding,
): () => Geometry | null {
  // Read through a ref so the listeners below are bound once, not per render.
  // Updated in a layout effect, which runs before any effect or frame that reads it.
  const latest = useRef({ size, refSize, onLayout });
  useLayoutEffect(() => {
    latest.current = { size, refSize, onLayout };
  });

  // Measured on demand, so a caller (the toolbar's 100%) never acts on a layout
  // from before a pane was moved, removed or resized.
  const measure = useCallback((): Geometry | null => {
    const pane = paneRef.current;
    const s = latest.current.size;
    if (!pane || !s || pane.clientWidth === 0 || pane.clientHeight === 0) return null;
    return geometry(pane.clientWidth, pane.clientHeight, s, vp.get(), latest.current.refSize);
  }, [paneRef, vp]);

  useEffect(() => {
    const pane = paneRef.current;
    if (!pane) return;

    const geom = measure;

    const layout = () => {
      const g = geom();
      const img = imgRef.current;
      if (!g || !img) return;
      img.style.width = `${g.w}px`;
      img.style.height = `${g.h}px`;
      img.style.transform = `translate(${g.tx}px, ${g.ty}px)`;
      latest.current.onLayout?.(g);
    };

    const local = (e: { clientX: number; clientY: number }) => {
      const r = pane.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top] as const;
    };

    const onWheel = (e: WheelEvent) => {
      const g = geom();
      if (!g) return;
      e.preventDefault();
      const [mx, my] = local(e);
      // Line-mode wheels (Firefox on some mice) report ~3 per notch, pixel-mode ~100.
      const factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0016));
      vp.zoomAt(g, mx, my, vp.get().z * factor);
    };

    let drag: { x: number; y: number; id: number } | null = null;
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0 || (e.target as HTMLElement).closest("button, a, input, select")) return;
      pane.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, id: e.pointerId };
      pane.dataset.dragging = "true";
    };
    const onMove = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      const g = geom();
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      drag = { ...drag, x: e.clientX, y: e.clientY };
      if (g) vp.panBy(g, dx, dy);
    };
    const onUp = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      drag = null;
      delete pane.dataset.dragging;
    };

    const onDouble = (e: MouseEvent) => {
      if ((e.target as HTMLElement).closest("button, a, input, select")) return;
      const g = geom();
      if (!g) return;
      if (vp.get().z > 1.01) { vp.reset(); return; }
      // Fit → this image at its real pixels, centred on the click. A small image
      // already past 100% at fit doubles instead, so the gesture always zooms.
      const [mx, my] = local(e);
      const full = vp.get().z / g.scale;
      vp.set({ z: full > 1.1 ? full : 2, cx: (mx - g.tx) / g.w, cy: (my - g.ty) / g.h });
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      // Keys aimed at a control inside the pane belong to it: A/B's divider moves
      // on the arrow keys, and a focused corner button should not pan. Checked
      // here because this is a native listener on the pane, which runs before any
      // React handler on the control could stop the event.
      if ((e.target as HTMLElement).closest("button, a, input, select, textarea")) return;
      const g = geom();
      if (!g) return;
      const v = vp.get();
      const step = 0.1 / v.z;
      switch (e.key) {
        case "+": case "=": vp.set({ ...v, z: v.z * 1.5 }); break;
        case "-": case "_": vp.set({ ...v, z: v.z / 1.5 }); break;
        case "0": vp.reset(); break;
        case "ArrowLeft": vp.set({ ...v, cx: v.cx - step }); break;
        case "ArrowRight": vp.set({ ...v, cx: v.cx + step }); break;
        case "ArrowUp": vp.set({ ...v, cy: v.cy - step }); break;
        case "ArrowDown": vp.set({ ...v, cy: v.cy + step }); break;
        default: return;
      }
      e.preventDefault();
    };

    const unsubscribe = vp.subscribe(layout);
    const ro = new ResizeObserver(layout);
    ro.observe(pane);
    pane.addEventListener("wheel", onWheel, { passive: false });
    pane.addEventListener("pointerdown", onDown);
    pane.addEventListener("pointermove", onMove);
    pane.addEventListener("pointerup", onUp);
    pane.addEventListener("pointercancel", onUp);
    pane.addEventListener("dblclick", onDouble);
    pane.addEventListener("keydown", onKey);
    layout();
    return () => {
      unsubscribe();
      ro.disconnect();
      pane.removeEventListener("wheel", onWheel);
      pane.removeEventListener("pointerdown", onDown);
      pane.removeEventListener("pointermove", onMove);
      pane.removeEventListener("pointerup", onUp);
      pane.removeEventListener("pointercancel", onUp);
      pane.removeEventListener("dblclick", onDouble);
      pane.removeEventListener("keydown", onKey);
    };
  }, [paneRef, imgRef, vp, measure]);

  // A newly loaded image, or Match pixels switched, needs a layout the view did
  // not ask for. Ask through the viewport so it lands on the same frame as the rest.
  useEffect(() => {
    vp.set(vp.get());
  }, [vp, size?.w, size?.h, refSize?.w, refSize?.h]);

  return measure;
}
