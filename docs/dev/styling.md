# Styling: the Tailwind/CSS design system, the brand mark & ConfirmDialog

This file covers the frontend's visual layer: CSS custom-property tokens, the reusable component classes in `@layer components`, the `CrucibleMark` brand mark and its export/drift constraints, and the shared `ConfirmDialog`. Component and state conventions live in `docs/dev/frontend-core.md`.

### Design tokens and component classes

Tailwind CSS v3 with a dark theme. Color tokens are CSS custom properties defined in `index.css` (`:root { --bg, --surface-1/2/3, --accent, --line, --fg, --warn, --bad, --info }`) and aliased in `tailwind.config.js` so they can be used as Tailwind classes. Geist/Geist Mono fonts are loaded via Google Fonts in `index.html`. Reusable component classes are defined in `frontend/src/index.css` under `@layer components`:

| Class | Purpose |
|---|---|
| `.btn` + variants — both compound (`.btn.primary`, `.btn.ghost`, `.btn.danger`, `.btn.sm`) and hyphenated (`.btn-primary`, `.btn-secondary`, `.btn-ghost`, `.btn-danger`, `.btn-sm`) forms are defined; the hyphenated forms are the more common outside the comfy components | Button variants |
| `.input`, `.select`, `.checkbox` | Form controls |
| `.panel`, `.panel-h`, `.panel-b` | Card container with header/body sections |
| `.form-row` | 2-col grid (200px label + 1fr control) used in CaptioningPage and ExportPage |
| `.model-row` | Radio-style model selector row with name, description, and VRAM label |
| `.stat-card` | Metric card with large value, label, and optional delta |
| `.hist` / `.hist-axis` | CSS grid bar chart; set `--cols` and `gridTemplateRows: "1fr"` inline; bars use percentage `height` |
| `.flag-card` | 3-col grid (icon, label/desc, count) for quality flags |
| `.badge`, `.badge.dot`, `.badge.good/warn/bad/info/solid` | Semantic badge variants |
| `.icon-btn` | 30×30 ghost icon button |
| `.sel-bar` | Sticky bottom pill bar for selection actions |
| `.crumbs` | Breadcrumb navigation |
| `.nav-section`, `.nav-tail` | Sidebar section header and count badge |
| `.tabs`, `.tab` | Tab bar with accent underline active state |
| `.dialog-bg`, `.dialog` | Fixed full-screen dimmer (z-index 60) + centered modal panel (max-width 420px). Used by the `DatasetsPage` modals; `ConfirmDialog` predates these and uses its own Tailwind utilities instead |
| `.ds-flash` | 2s accent `box-shadow` pulse marking a newly created dataset (`box-shadow`, not `border`, so nothing reflows) |

### The brand mark

**`CrucibleMark`** (`frontend/src/components/common/CrucibleMark.tsx`) — the brand mark: a 5×5 contact sheet that curates down to a C. Props: `size` (px, default 22), `animated`, `className`, `label`. Renders 25 rounded rects on a 64-unit viewBox; the `KEPT` set is the C, `BRIGHT` the four `--accent-2` cells, everything else the dropped grid at `--accent-dim`. Used at 22px in the Sidebar brand and 132px (animated) in the TopBar restart overlay. The **component** is token-driven — no hardcoded hex — so palette changes carry through automatically; both current callsites are decorative so the mark defaults to `aria-hidden`, and a callsite that stands alone should pass `label` to get `role="img"` + `aria-label`. The **app icon is not token-driven**: `frontend/public/favicon.svg` (a copy of `docs/images/crucible-icon.svg`) and the favicon/apple-touch PNGs hardcode the palette, because a standalone icon file is never in the document's CSS scope. On a palette change these must be re-exported by the designer and re-copied into `frontend/public/` — the CSS token change alone will not touch them.

The designer's exports are the source of truth for the geometry and live in `docs/images/` (`crucible-mark.svg` static, `crucible-icon.svg` the app-icon variant, `Crucible Logo Animated.html` animated, `icons/*.png` rasters, `README.txt` brand notes). **If you change the mark, change it there and in the component** — they are independent transcriptions of the same design and will silently diverge. `scripts/check_mark.py` diffs the component against the exports (cell roles, bright cells, per-cell animation variant, and the app-icon C shape) and byte-compares the `frontend/public/` favicons against their `docs/images/` sources, failing on any drift. It runs in CI (`.github/workflows/check-mark.yml`).

**Class names in `@layer components` must appear verbatim in the source.** Tailwind only emits a rule from `@layer components` if its selector is found as literal text when scanning `content` files, so a dynamically assembled class name (``className={`cm-keep-${variant}`}``) type-checks, renders, and is then **purged from the bundle** — the element carries a class that no CSS matches. This fails silently and only in a production build; the symptom is an element rendering unstyled with no error. Always write the full class name out (`variant === "a" ? "cm-keep-a" : "cm-keep-b"`), as `CrucibleMark` and `.pp-fill-indeterminate` do. `scripts/check_mark.py` asserts the built CSS still contains the mark's rules when `frontend/dist` is present.

`animated` attaches the `.cm-keep-a/b` and `.cm-drop-a/b` classes whose keyframes live in `index.css`. One 9.2s cycle: the grid flickers as if sampling, non-C cells shrink to dots and return, then vanish, leaving the C. The A/B split is a checkerboard (`(indexOf(x) + indexOf(y)) % 2`) so neighbouring cells fire on offset timelines. A `prefers-reduced-motion: reduce` block resolves the mark to its static form rather than freezing it mid-cycle.

**`--accent-dim` (`#0f6e4e`)** exists specifically for the mark's unresolved grid cells. It is *not* interchangeable with `--accent-deep` (`#064e3b`): at `--accent-deep` the dropped cells are dark enough that the C is already legible on frame one, which spoils the animation's reveal. Static cells use `--accent-dim` at `opacity .09` — the value the animation resolves to, and a near-exact match for `--accent-deep` once composited on `--bg`, which is why the static mark looks right either way and the difference only shows in motion.

### ConfirmDialog

**`ConfirmDialog`** (`frontend/src/components/common/ConfirmDialog.tsx`) — shared modal for destructive confirmations. Keyboard-aware: auto-focuses Cancel on mount by default (safe default for destructive actions), ArrowLeft/ArrowRight switch focus between Cancel and the confirm button, Enter fires the focused button natively. When adding any global `keydown` listener that handles ArrowLeft/ArrowRight, suppress it while a `ConfirmDialog` is open to avoid background navigation competing with dialog focus — see `showDeleteConfirm` guard in `ImageDetailPage`'s arrow-key effect. Accepts an optional `defaultFocus?: "cancel" | "confirm"` prop to override the focused button per-callsite. When `danger=true` and no `defaultFocus` is provided, the component reads `localStorage.getItem(CONFIRM_DEFAULT_KEY)` (from `constants/storage.ts` — see `docs/dev/persistence.md`) to respect the user's preference set in Settings → UI Behavior.

### CSS hist bars

The `.hist` class sets `display: grid; align-items: end; height: 90px`. For percentage `height` on bar children to resolve, you must also set `gridTemplateRows: "1fr"` as an inline style on the `.hist` div. Without this the single implicit row has no definite height and percentage heights collapse to 0.
