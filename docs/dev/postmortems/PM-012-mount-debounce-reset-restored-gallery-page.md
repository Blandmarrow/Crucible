# PM-012: a debounce effect firing on mount reset the restored gallery page

### Symptom

Opening an image from page 2+ of the gallery and clicking **Back** landed on page 1 of the
dataset instead of the page the user left. The same happened on a browser reload of the
gallery. It was not a clean failure: the restore *worked*, then undid itself — for roughly
a third of a second the correct page was on screen, and the pagination footer read
`Page 2` before snapping to `Page 1`. Scroll position was lost with it.

The loss compounded: the debounced save effect then wrote `page: 1` into
`gallery-state-${datasetId}`, so the remembered page was gone for good, not just for that
navigation.

### Root cause

`GalleryPage` debounces its search box and its detection-label box into committed state:

```js
useEffect(() => {
  const t = setTimeout(() => {
    setSearch(searchInput);
    setPage(1);                       // ← a filter changed, so restart at page 1
    hasRestoredScroll.current = false;
  }, 350);
  return () => clearTimeout(t);
}, [searchInput]);
```

The body reads as "when the user finishes typing, go back to page 1" — and it does do that.
But a `useEffect` also runs on **mount**, so the timer is armed on every arrival at the
page, including the remount that Back causes. 350 ms later it fired with `searchInput`
unchanged at `""`, and `setPage(1)` threw away the page restored from `localStorage` by the
`useState` initializer.

Nothing in the effect distinguishes "the value changed" from "this component just mounted".
The `setSearch(searchInput)` line is a no-op in the mount case (React bails on an identical
value), which is exactly why the bug hid: the *visible* half of the effect was correctly
inert, and only its side effects were not.

The bug predates the branch that surfaced it — it reproduces identically on `main`.

### Generalizable rule

**A debounced input effect that also resets navigation state must bail when its input
already equals the committed value.** Grep for `useEffect` bodies that pair a
`setCommitted(input)` with a `setPage(1)` / scroll reset / selection clear: the setter is
idempotent on mount, the reset is not. Guard with `if (input === committed) return;` and put
the committed value in the dependency array — a mount-only ref works too but does not cover
the second case, where the user types and deletes back to the original value before the
timer fires.

More generally: **when a mount-firing effect and a restore-from-storage initializer touch
the same state, the effect wins and the restore is silently discarded.** Any page with a
`loadPersisted`/`gallery-state`-style initializer should be read with that in mind.

**Restore-side corollary: the guard is an *equality* check, so anything that starts a
debounced pair unequal re-arms the bug.** Persisting a committed value later — `search` and
`detectionLabel` now ride in `gallery-state-*` — must seed the `*Input` draft from the same
stored value, or the pair arrives unequal, the timer fires on mount exactly as it did here,
and the restored page is destroyed again by a change that looks like a pure feature
addition. The same holds for every writer of one half: **Reset filters** and the inputs' ×
buttons clear both halves synchronously. And persist only the committed value — a persisted
draft leaves the restore choosing between applying a filter the user never committed and
seeding the pair unequally.

### Why it wasn't caught the first time

No test covered the restore at all, and the manual check that would have caught it is
unusually fragile: the correct page renders first and is replaced 350 ms later, so anyone
verifying by eye — or with an assertion that runs as soon as the gallery paints — sees a
pass. Any regression test for restored state has to assert **after** the debounce window,
not on arrival.

### Fix

Equality guard at the top of both debounce effects in `frontend/src/pages/GalleryPage.tsx`,
with the committed value added to each dependency array. Regression test:
`frontend/e2e/gallery-restore.spec.ts` (seeds four images, sets `gallery-page-size` to 2,
pages forward, opens a tile, clicks Back, and asserts `Page 2` both immediately and one
second later). A second guard in the same file covers the corollary —
`a search survives Back without resetting the page` types a query, pages forward inside the
filtered list, round-trips through the detail view and asserts the footer twice, so a
`search` seeded into only one half of its pair fails on the delayed assertion. Documented in
`docs/dev/image-detail.md` (§ Gallery persistence & detail-view navigation).

### Status & date

MITIGATED — the guard fixes the two known effects; nothing structurally prevents the next
debounced effect from being written without it. Last reviewed for staleness: 2026-07-29.
