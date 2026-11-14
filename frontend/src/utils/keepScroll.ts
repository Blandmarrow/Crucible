/**
 * Run `fn`, then put back the scroll position of every scrolled ancestor of `el` (and
 * the page). For DOM work on a text box that the browser would otherwise answer by
 * scrolling: measuring an auto-sized textarea at `height: auto` lays the page out
 * shorter and clamps a panel scrolled near its bottom, and `setSelectionRange` on a
 * focused textarea scrolls the caret into view. Both made the caption editor jump
 * under the pointer (#52's synonym card).
 */
export function keepScroll(el: Element, fn: () => void): void {
  const scrolled: [Element, number][] = [];
  for (let p = el.parentElement; p; p = p.parentElement) {
    if (p.scrollTop > 0) scrolled.push([p, p.scrollTop]);
  }
  const root = document.scrollingElement;
  if (root && root.scrollTop > 0 && !scrolled.some(([p]) => p === root)) scrolled.push([root, root.scrollTop]);
  fn();
  for (const [p, top] of scrolled) p.scrollTop = top;
}
