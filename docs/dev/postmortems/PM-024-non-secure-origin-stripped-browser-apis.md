# PM-024: A non-secure origin stripped the browser APIs two features were built on

### Symptom

A user on Garuda Linux / Vivaldi reported two entries in Crucible's own error console
([issue #91 comment](https://github.com/Blandmarrow/Crucible/issues/91#issuecomment-5610961504),
filed as [#93](https://github.com/Blandmarrow/Crucible/issues/93)):

```
Uncaught TypeError: crypto.randomUUID is not a function
Uncaught TypeError: Cannot read properties of undefined (reading 'writeText')
```

The first fired when saving a caption preset, the second when clicking **Copy Errors**. Both
stack traces were served from **`http://0.0.0.0:8000`**.

What the user actually saw depended on the surface. On `CaptioningPage` the preset panel threw
before `toast.success("Preset saved")`, so nothing was appended and no toast appeared. The same
store's `save` is reached from `PromptPresetManager`, mounted in `SelectionToolbar` and
`ImageDetailPage`, and neither of those shows a toast at all — there the bug simply read as *the
button does nothing*. **Copy Errors** silently copied nothing, on both the `ErrorConsole` overlay
and the Logs page's Errors tab. `GenerationMetadata`'s copy button was worse than silent: it had
no `.catch()`, so its failure became an `unhandledrejection` that popped the global error console
— a failed copy looked like a crash.

### Root cause

`crypto.randomUUID` and `navigator.clipboard` are both `[SecureContext]` in their IDL. Chromium
installs them on HTTPS, `localhost` and `127.0.0.1`, and **nowhere else** — on any other `http://`
origin they are never defined. Not gated, not permission-prompted: absent. So
`crypto.randomUUID()` is a call on `undefined`, and `navigator.clipboard.writeText(…)` is a
property read on `undefined`, and both throw *synchronously*.

Synchronously is the operative word for the clipboard sites. Two of the five wrote
`navigator.clipboard.writeText(x).catch(() => {})` — a handler that catches nothing, because the
TypeError is raised while evaluating the expression, before any promise exists.

`0.0.0.0` is a bind address, not an address to browse to, but it is the one the user lands on. Both
launchers print `Starting server at http://localhost:8000` ([manage.sh:636](../../../manage.sh#L636),
[manage.ps1:669](../../../manage.ps1#L669)) — and uvicorn's own banner, `Uvicorn running on
http://0.0.0.0:8000`, prints immediately after and is the last, terminal-hyperlinked URL on
screen. And anyone reaching the app over their LAN (`http://192.168.1.5:8000`) is on an insecure
origin no matter which URL we print.

### Generalizable rule

**A `[SecureContext]` API used without a feature test is a bug in this app.** Crucible is a
self-hosted server routinely opened on `http://0.0.0.0:8000` and over a LAN IP, where
`window.isSecureContext` is `false` and the API is not merely restricted but *missing*. Flag any
frontend code naming one of:

- `crypto.randomUUID` — use `nanoid()` from `frontend/src/store/nanoid.ts`.
- `navigator.clipboard` — use `copyText()` from `frontend/src/utils/clipboard.ts`.
- `crypto.subtle`, `navigator.geolocation`, `navigator.mediaDevices`, service workers /
  `navigator.serviceWorker`, `PushManager` — no consumer today; a new one needs a fallback or a
  feature-tested degradation *before* it ships.

Two corollaries, both of which bit here:

- **A `.catch()` does not cover a missing API.** The TypeError precedes the promise. The test has
  to be a feature test — `typeof navigator.clipboard?.writeText === "function"` — not a
  try/catch around the call.
- **`?.` is not a fix.** `navigator.clipboard?.writeText(t).then(…)` short-circuits the *property
  access* only; the whole expression is `undefined` where there is no clipboard, and `.then` on
  it throws. It is the original bug, one character shorter.

And the reason a feature test rather than a try/catch, specifically for the clipboard: the
`document.execCommand("copy")` fallback needs live user activation, and awaiting a rejected
`writeText` first spends it.

### Why it wasn't caught the first time

No gate can observe the failing origin. Every developer runs on `localhost:5173` or
`localhost:8000`, and the Playwright suite serves `http://localhost:8199` — all secure contexts by
fiat. The APIs are present in every environment the project has ever tested in, so the code is
correct everywhere it was ever run and broken in the place users actually arrive.

The compounding gap is that the two features had *no* failure path at all: three of the five
clipboard sites either swallowed the failure into a no-op `.catch()`, claimed success
unconditionally, or had no handler, and two of the four preset-save surfaces show no toast. A
feature that cannot report its own failure cannot be reported *by a user* either — this surfaced
only because Crucible's error console captured the raw TypeError and the user copied it out (from
the Logs page, since the overlay's own Copy button was one of the broken ones).

### Fix

Branch `Blandmarrow/issue93-insecure-origin`:

- **`frontend/src/utils/clipboard.ts`** (new) — `copyText(text): Promise<boolean>`, a *fallback*
  rather than a guard: with no Clipboard API it copies via the hidden-`<textarea>` +
  `document.execCommand("copy")` recipe. Never throws, never rejects; `false` is the only failure
  signal. Documented in `docs/dev/shared-utilities.md`.
- **`frontend/src/store/promptPresetsStore.ts`** — `nanoid()` in place of `crypto.randomUUID()`.
  Ids are opaque and never parsed, so existing presets keep their UUIDs; no migration.
- All five clipboard sites moved onto `copyText`, each keeping its own feedback, and three of them
  fixing a second bug: `GenerationMetadata` (no `.catch()` at all; its 1500 ms timer is also now
  cleared on unmount), `BooruPage` (toasted success unconditionally), and the two byte-identical
  Copy Errors handlers, now the shared `components/common/CopyErrorsButton.tsx`.
- **`frontend/src/main.tsx`** — `location.hostname === "0.0.0.0"` redirects to the `localhost`
  equivalent, preserving port, path and query, with the `createRoot` call as the **`else` branch**:
  `location.replace` queues a navigation and does not halt script execution, so falling through
  would mount the whole app against `0.0.0.0` — SSE stream, initial queries, and a zustand
  `persist` hydration into that origin's separate `localStorage` — for the milliseconds before the
  navigation lands. Exactly that one hostname: a LAN IP is insecure too, but it is a deliberate
  remote-access choice and rewriting it would point the browser at the wrong machine.
  Complementary, not primary — the fallbacks are what make the features work everywhere; the
  redirect stops the specific footgun we hand people.
- **`frontend/e2e/insecure-origin.spec.ts`** (new) — fakes the origin with `page.addInitScript`.
  The trap: `delete navigator.clipboard` deletes a non-existent *own* property, returns `true`,
  and changes nothing. Both APIs live on a **prototype** (`Navigator.prototype.clipboard` is an
  accessor, `Crypto.prototype.randomUUID` a method) and both are `configurable: true`. Deleting
  there is both what works and what a real insecure origin looks like: never installed.
- **`frontend/eslint.config.js`** — `no-restricted-syntax` bans bare `navigator.clipboard` and
  `crypto.randomUUID` in `frontend/src/`, pointing at the helpers, so the rule above is
  enforceable rather than aspirational. The two selectors are named constants so the one
  exemption — `utils/clipboard.ts`, which must name the API it wraps — can re-declare the
  *other* restriction instead of switching the rule off and quietly un-banning both.
  (`npm run lint` is `continue-on-error` in CI, so it documents more than it blocks.)

### Status & date

MITIGATED — the two known consumers are fixed and lint guards the two named APIs, but the wider
family (`crypto.subtle`, `mediaDevices`, …) is guarded by review only, and no gate runs against a
genuinely insecure origin. Last reviewed for staleness: 2026-09-10.
