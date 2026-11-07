import { useLayoutEffect, type RefObject } from "react";

/**
 * Publish a scroll container's sticky geometry as CSS custom properties on the container
 * itself, so sticky children can size themselves without a re-render:
 *
 * - `--comfy-head-h`: the sticky header's height. It wraps on a narrow pane, so it varies.
 * - `--comfy-view-h`: the scroller's visible height (`clientHeight`). A sticky side column
 *   caps its height with it. `100vh` would be wrong here: a pane is shorter than the window.
 *
 * Written straight to the element's style from a ResizeObserver, never through state.
 * `mounted` must change whenever the two elements mount or unmount (the page renders a
 * placeholder without them), since the refs alone cannot re-run the effect.
 */
export function useStickyOffsets(
  scrollerRef: RefObject<HTMLElement | null>,
  headerRef: RefObject<HTMLElement | null>,
  mounted: boolean,
): void {
  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    const header = headerRef.current;
    if (!mounted || !scroller || !header) return;
    const update = () => {
      scroller.style.setProperty("--comfy-head-h", `${header.offsetHeight}px`);
      scroller.style.setProperty("--comfy-view-h", `${scroller.clientHeight}px`);
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(scroller);
    ro.observe(header);
    return () => ro.disconnect();
  }, [scrollerRef, headerRef, mounted]);
}
