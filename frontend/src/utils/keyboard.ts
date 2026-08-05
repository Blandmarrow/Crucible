/**
 * True when a keyboard event originated in something the user is typing into.
 *
 * Every global key handler needs this guard, and `ImageDetailPage` had grown two
 * hand-rolled copies of it before the label hotkeys would have made a third. It
 * is load-bearing there: the caption editor is a `<textarea>` on that page, so
 * without the check, typing "a" into a caption would toggle a label.
 */
export function isTextEntryTarget(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    el.isContentEditable === true
  );
}
