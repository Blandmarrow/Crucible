/**
 * Copy text to the clipboard on any origin, secure or not.
 *
 * `navigator.clipboard` is `[SecureContext]` in the IDL, so Chromium installs it
 * on HTTPS, `localhost` and `127.0.0.1` and nowhere else. Crucible is routinely
 * opened on `http://0.0.0.0:8000` (uvicorn's own banner prints that URL) and over
 * a LAN IP, where the whole `navigator.clipboard` object is simply absent and
 * `navigator.clipboard.writeText(...)` throws a synchronous TypeError. See
 * `docs/dev/postmortems/PM-024-non-secure-origin-stripped-browser-apis.md`.
 *
 * This is a *fallback*, not a guard: with no Clipboard API it copies through the
 * hidden-`<textarea>` + `document.execCommand("copy")` recipe. A copy button that
 * politely explains it cannot copy is still a broken copy button.
 *
 * Two invariants every caller depends on:
 *
 * 1. **Never throws, never rejects.** `false` is the only failure signal. One
 *    caller is the error console, which owns the app's `error` /
 *    `unhandledrejection` listeners — a copy button that can raise files a report
 *    about itself, which is exactly how this bug surfaced.
 * 2. **The missing-API case never leaves the click's tick.** The feature test is on
 *    `navigator.clipboard?.writeText` rather than a try/catch around the call,
 *    because `execCommand` needs live user activation and awaiting a rejected
 *    `writeText` first spends it. Do not "simplify" to
 *    `try { await navigator.clipboard.writeText(t) }`.
 *
 * Note that `navigator.clipboard?.writeText(text).then(…)` is *not* a fix either:
 * `?.` short-circuits the property access only, so the whole expression is
 * `undefined` where there is no clipboard and `.then` on it throws — the original
 * bug, one character shorter.
 *
 * Known limit: `execCommand("copy")` over an empty selection returns `false`, so
 * copying `""` reports failure on the fallback path. No caller can reach it —
 * every copy button is disabled or unrendered with nothing to copy.
 */
export async function copyText(text: string): Promise<boolean> {
  // Feature-test the method, not the object: some environments expose a
  // `navigator.clipboard` without `writeText` (permissions-policy blocked).
  if (typeof navigator !== "undefined" && typeof navigator.clipboard?.writeText === "function") {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Denied permission, or a document that was not focused. Fall through to
      // the legacy path, which is subject to neither.
    }
  }
  return legacyCopy(text);
}

/** The pre-Clipboard-API recipe. Synchronous, so it runs inside the click's user activation. */
function legacyCopy(text: string): boolean {
  if (typeof document === "undefined" || !document.body) return false;

  // A copy button must not steal the caret out of a caption being edited, so
  // snapshot what is selected and focused and put both back afterwards.
  const previousFocus = document.activeElement as HTMLElement | null;
  const selection = document.getSelection();
  const ranges: Range[] = [];
  if (selection) {
    for (let i = 0; i < selection.rangeCount; i++) ranges.push(selection.getRangeAt(i));
  }

  const ta = document.createElement("textarea");
  ta.value = text;
  // Off-screen rather than `display: none` / `hidden` — hidden text is not
  // selectable, and an unselectable textarea copies nothing.
  ta.style.position = "fixed";
  ta.style.top = "-9999px";
  ta.style.left = "-9999px";
  ta.style.opacity = "0";
  ta.setAttribute("aria-hidden", "true");
  // `readOnly` + `setSelectionRange` rather than `select()`: iOS Safari ignores
  // `select()` on an editable field and pops the keyboard instead.
  ta.readOnly = true;

  let ok: boolean;
  try {
    document.body.appendChild(ta);
    ta.focus();
    ta.setSelectionRange(0, text.length);
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }

  // Teardown gets its own `try`, deliberately not a `finally`: a `finally` that
  // throws replaces the return value and breaks invariant 1.
  try {
    ta.remove();
    if (selection) {
      selection.removeAllRanges();
      for (const r of ranges) selection.addRange(r);
    }
    previousFocus?.focus?.();
  } catch {
    // The copy already happened (or did not); restoring the caret is a nicety.
  }

  return ok;
}
