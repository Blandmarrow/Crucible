# Versioning frontend

This file covers the versioning UI: `VersionsPage`, the snapshot, restore and diff modals,
`BranchSelector`, the shared `JobProgressBar`, `versioningApi`, the sidebar version panel,
and the TanStack Query keys a snapshot, restore or checkout invalidates.

The routes these call are `docs/dev/versioning/routes.md`; the `result_data` a restore
reports is `docs/dev/versioning/restore.md`; which diff fields `DiffModal` receives, and how
each renders, is `docs/dev/versioning/mirrors.md` § The diff.

## Pages and components

- `frontend/src/pages/VersionsPage.tsx` — route `/datasets/:datasetId/versions`, sidebar "Versions". Shows disabled-state when `versioning_mode="off"` (link to Settings). Otherwise shows branch selector, filter bar (debounced search + date range), version list with source badges (`Manual`/`Pre-restore`/`Branch init`) and pin icon per card. Pin toggle uses `setQueryData` optimistic update + client-side re-sort (no refetch). Active branch persisted to `sessionStorage` under `VERSIONS_BRANCH_KEY-${datasetId}`; falls back to `dataset.current_branch_id`, then `branches[0]`. `resolvedBranchId = activeBranch?.id` is passed to `BranchSelector` (not raw `activeBranchId`) so the dropdown stays in sync after restarts. A `useRef`+`useEffect` watches `dataset.current_branch_id` post-mount; the guard (`prev !== undefined`) prevents the initial data load from clobbering the stored preference. All of that state is seeded at mount, so `PageRenderer` gives this page `key={view.datasetId}` — without it, switching a pane's dataset wrote A's branch id under B's `VERSIONS_BRANCH_KEY-${datasetId}` and left A's snapshot in an open confirm dialog while the mutation targeted B (`docs/dev/panes-routing.md` § A pane's dataset is part of a page's identity).
- `frontend/src/components/versioning/CreateSnapshotModal.tsx` — name + description inputs; shows `JobProgressBar` during bg job; passes `activeBranchId` in the snapshot body so new snapshots land on the correct branch.
- `frontend/src/components/versioning/RestoreConfirmModal.tsx` — keep/remove radio for extra images, pre-restore snapshot checkbox, file-unavailability warning, `JobProgressBar`
- `frontend/src/components/versioning/DiffModal.tsx` — select two versions; shows Added/Removed/Modified sections with field-level changes
- `frontend/src/components/versioning/BranchSelector.tsx` — branch `<select>` + "New branch…" option. Checkout and branch creation show a `SnapshotPrompt` dialog first when `BRANCH_SNAPSHOT_KEY === "ask"`. Checkout triggers a bg job; a fixed-position progress card appears bottom-right; `onSelect` fires only after job completion to avoid stale data. For sync branch creation (≤100 images), `doCheckout(result.id, false)` is called immediately so `current_branch_id` updates — `pre_restore_snapshot=false` because the branch was just created. A trash icon opens a delete modal with its own `<select>` (excluding `currentBranchId`); the active branch cannot be deleted.
- `frontend/src/components/common/JobProgressBar.tsx` — shared progress bar (message + animated fill bar); used by snapshot, restore, and branch-checkout flows
- `frontend/src/api/versioning.ts` — `versioningApi`: `listBranches`, `createBranch(datasetId, name, fromVersionId?, includeSnapshot = true)`, `checkoutBranch(datasetId, branchId, preRestoreSnapshot = true)`, `deleteBranch(datasetId, branchId)`, `listVersions` (accepts `ListVersionsParams` object with `branchId`, `search`, `createdAfter`, `createdBefore`), `createSnapshot`, `getVersion`, `deleteVersion`, `updateVersion` (PATCH for `is_pinned`), `restoreVersion`, `pruneStorage` (→ `{job_id}`; triggered by the "Prune storage" ghost button in the `VersionsPage` header, behind a `ConfirmDialog`; the global job progress bar picks the job up automatically — no cache invalidation needed), `diff`. `createSnapshot`/`createBranch` return `Version | { job_id: string }` — discriminate with `"job_id" in data`.
- **Sidebar version panel** (`SidebarVersionPanel.tsx`) — accordion below "Active dataset" label. Collapsed: branch name · head snapshot + chevron. Expanded: `<BranchSelector>`, the 7 most recent snapshots with `[Restore]` buttons (current shows "Now"), and "View all →" link. Snapshot query (`["versions", datasetId, activeBranch.id, "sidebar"]`) gated on `expanded` with `limit: 7`. `onSelect` writes `VERSIONS_BRANCH_KEY-${datasetId}` to sessionStorage; `onSuccess` after restore invalidates branches, dataset, images, captions, and versions queries. Only rendered when `activeBranch` is defined.

## TanStack Query keys

- `["branches", datasetId]` — invalidated after snapshot creation, restore, checkout
- `["versions", datasetId, resolvedBranchId, search, createdAfter, createdBefore]` — invalidated after snapshot creation, delete, restore; pin toggle uses `setQueryData` instead of invalidation
- `["images", datasetId]` — invalidated after restore and after checkout (image set and captions change)
- `["image"]` — prefix invalidation (no imageId) after restore and checkout; clears all cached image detail pages so `ImageDetailPage` refetches immediately
- `["caption"]` — prefix invalidation after restore and checkout; clears all cached caption data
- `["dataset", datasetId]` — invalidated after restore and checkout (image count, `current_branch_id`)
- `["dataset-stats", datasetId]`, `["tag-stats", datasetId]`, `["score-values", datasetId]`, `["tag-cooccurrence", datasetId]` — all four stats queries invalidated after restore (`VersionsPage` and `SidebarVersionPanel`) and after checkout (`BranchSelector`)
