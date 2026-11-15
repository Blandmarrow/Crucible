# The Compare viewer

The viewer half of the Compare page (#130): the linked viewport `useLinkedViewport`, the
pane, and the A/B frame. The user-facing page is `docs/compare.md`; the rulings are decision
20 (`docs/dev/decisions/20-compare-view.md`). Routing, opening beside the gallery, the batch
read, the actions and what was left unbuilt are in `docs/dev/gallery/compare.md`; the grid
and the contact sheet are in `docs/dev/gallery/contact-sheet.md`.

## Linked zoom: `useLinkedViewport`

`hooks/useLinkedViewport.ts` holds one view for every pane in **normalized** coordinates:
the picture point at the pane's centre (`cx`, `cy`, 0..1) and a zoom relative to fit
(`z`, 1..`ZOOM_MAX` = 24). Normalized, so a source and its 2× upscale show the same region
by default (decision 20). **Match pixels** passes the first image's size as `ref` to
`geometry`, so every pane takes that image's scale and a larger image draws larger.

The view lives **outside React state**. A wheel gesture changes it dozens of times a
second; each pane subscribes and writes its own `<img>` width, height and transform on
the next animation frame, and only the toolbar's readout re-renders
(`useViewportZoom`, a `useSyncExternalStore`). `useViewportPane` binds one pane:

- the **wheel** listener is attached by hand with `passive: false`, because React
  registers `onWheel` as passive and a passive listener cannot stop the page scrolling;
- **drag** pans with pointer capture; **double-click** toggles fit and 100% at the point,
  where `z / scale` is the zoom that draws that image at 100% in either link mode;
- with the pane focused, `+`/`−`/`0` and the arrow keys;
- a `ResizeObserver` re-lays the pane when split view or the window resizes it.

`set` clamps the view and snaps any zoom at or below 1 back to centred fit, so zooming
out near an edge never leaves the picture hanging to one side.

`useViewportPane` returns a `measure()` that computes the pane's geometry **now**. The
toolbar's **100%** asks the current first pane at click time (each pane registers its
`measure` with the page). It used to read a geometry cached by pane 1's last layout,
which a **Move left** or a removal does not trigger, so 100% could scale by the image
that had been first.

## The pane

`components/compare/ComparePane.tsx` shows the thumbnail at once and swaps in the full
file when it has decoded (`decoding="async"`), so four large PNGs never leave four blank
panes. The swap waits for the first **layout** (`placed`), not the load: between the
two the full image has no size or position, and showing it flashed it unscaled for a
frame. React owns only its `visibility` and `imageRendering`; the layout writes the
width, height and transform, which React never touches. It places the image from its **natural** size, as the browser draws it with EXIF
orientation applied, never from the stored `width`/`height`, which only the strip shows.
A file that fails to load says so in place.

`MetaStrip` marks the best value in words (**most px**, **best**), never by colour alone.
Scores are compared only when every scored image used one aesthetic model, the rule the
Quality page applies to *Keep best* (`bestMarks` in `ComparePage.tsx`). The "shown at N%"
figure is written by the pane's layout callback, not rendered.

`COMPARE_PREFS_KEY` (`constants/storage.ts`) remembers the mode, A/B's way of showing B,
Match pixels and Pixelated per browser; see `docs/dev/persistence.md`.

## A/B

`components/compare/ABView.tsx` draws two images in one frame under the same viewport.
It binds the frame with `useViewportPane` for image A only; its `onLayout` writes A's
geometry onto B as well, so B always fills **A's rectangle**. Images of one shape line up
exactly; another shape is stretched, and the foot says the overlay is approximate. A
crop is not aligned. Match pixels has no meaning with one frame, so the toolbar hides it.

- **Swipe** clips B with `clip-path: inset(0 0 0 Lpx)` and A with
  `inset(0 (w − L)px 0 0)`, where `L = W × split − tx`: `inset()` is measured in each
  image's own box, before its transform. Clipping A too keeps it from showing through a
  transparent B. The divider and handle are written from the same layout; the handle
  reads the current split when it remounts. The handle is a `role="slider"` button.
  **Its arrow keys reach the frame first**: the frame's key handler is a *native*
  listener on the pane, which runs before any React handler on the handle, so a
  synthetic `stopPropagation` there came too late and the picture panned whenever it
  was zoomed. `useViewportPane`'s `onKey` therefore ignores keys aimed at a button,
  link or field inside the pane.
- **Flip** shows B only while Space or the button is held, and hides A meanwhile (a
  transparent B again). The Space listener carries the guards every pane key handler
  needs: `isTextEntryTarget`, the active-pane check (so Space in the gallery beside it
  never flips), and **no focused button or link**, where Space means "activate". A
  window `blur` and the listener's own cleanup clear it, so neither a key released
  elsewhere nor leaving Flip mid-hold can leave B stuck on.
- **Difference** is `mix-blend-mode: difference` on B: unchanged pixels go black. No
  server work.

**Nothing claims to show B before B has loaded.** With B still downloading or failed,
every mode fell back to A's pixels under a "B" label, which reads as "A and B are
identical". The labels say *loading* or *could not be loaded*, the flip waits for B
(its button is disabled until then), and a failed A says so in place, as a failed pane
does in Side by side.

The A and B picks are **ids** held in page state (`abPick`), falling back to the first
two images when a pick leaves the set; choosing the image already on the other side
swaps the two. `ABView` is keyed on the pair, so a new pair remounts it while the shared
viewport keeps the zoom. It registers its `measure` under A's id, so **100%** in A/B
means A at its real pixels.

## Tests

`frontend/e2e/compare.spec.ts` covers the viewer in one journey (the rest are in `docs/dev/gallery/compare.md` § Tests):

- **A/B**: zoomed in, the divider's arrow keys move B's clip without panning; Space on a
  focused button does not flip, while on the frame it shows B only while held;
  Difference sets the blend; and the mode and its view survive a reload.
