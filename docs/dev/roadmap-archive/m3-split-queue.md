# M3 — The split queue

**Status: archive (landed 2026-10-30).** *Record for the M3 line in the
[roadmap](../roadmap.md#landed).*

Gate: every file over budget split or queued with a current seam, and the Map growing by
family rather than by file ([decision 2](../decisions.md#2-claudemd-budget-and-hub-routing)).

The seams lived in [pending-splits.md](../pending-splits.md); the milestone was only their
order. Each split re-measured before cutting, and a seam the file had grown past was
re-chosen.

- [x] Raise `MAX_CLAUDE_WORDS` to 6,000 in `scripts/check_docs.py`, with the dated reason
      pointing at decision 2, and delete the § CLAUDE.md queue entry (2026-10-29).
- [x] Image similarity/scoring hub (2026-10-29): the old image-similarity.md split four
      ways, the old dev scoring.md split two ways, and both moved with scores-stale.md into
      `docs/dev/quality/` behind the `docs/dev/quality.md` hub. `docs/scoring.md` split off
      `docs/style-similarity.md`; its residual seam stays queued.
- [x] Gallery hub (2026-10-29): the old gallery.md (6,017 words) split four ways and moved,
      with the dnd, state and nav files, into `docs/dev/gallery/` behind the
      `docs/dev/gallery.md` hub.
- [x] Video folder (2026-10-29): `docs/dev/video.md`'s eleven detail files moved, without a
      split, into `docs/dev/video/` behind the hub, and twelve Map rows became one.
- [x] The video family's two queued seams (2026-10-29): `docs/dev/video/reextract.md` (the
      request side moved to `docs/dev/video/reextract-endpoints.md` and § Tests into
      `docs/dev/video/tests.md`), then the user doc `docs/video.md`, split three ways into
      itself, `docs/video-extraction.md` and `docs/video-reextract.md`.
- [x] `docs/dev/labels.md`, both seams (2026-10-29): the consumers (filters, export, the
      snapshot mirror, cross-dataset and derivative hooks) moved to
      `docs/dev/label-consumers.md` and the frontend to `docs/dev/labels-ui.md`, as three
      flat siblings rather than a hub.
- [x] `docs/dev/ml-models.md` (2026-10-29): both recorded seams plus a third. LaMa joined
      `docs/dev/image-processing-models.md`; eviction, unloads, leases and releasing moved
      to `docs/dev/model-residency.md`; the ID registry and per-model loader notes moved to
      `docs/dev/model-registry.md`. Three flat siblings, no hub.
- [x] `docs/dev/captioning.md` (2026-10-29): the recorded provider seam plus the picker.
      Providers moved to `docs/dev/llm-providers.md` and the model picker to
      `docs/dev/caption-model-picker.md`; the job half stays at ~2,480 (71%) as one
      subsystem. Three flat siblings, no hub.
- [x] `docs/dev/bulk-image-jobs.md` (2026-10-29): the recorded seam. Detection-driven
      cropping moved to `docs/dev/detection-crop.md` and watermark removal to
      `docs/dev/inpaint.md`; the crop remap stays as its own `##`, since all four crop sites
      use it. Three flat siblings, no hub.
- [x] `docs/dev/file-browser.md` (2026-10-29): three ways rather than the recorded two,
      whose mutations half would have sat at 80%. `POST /move` (API-only) moved to
      `docs/dev/file-browser-move.md`, and rename and delete, the two DB-syncing mutations
      the page fires, to `docs/dev/file-browser-rename-delete.md`; the endpoint table,
      collisions, path safety, § Still open and the page stay. Three flat siblings, no hub.
- [x] Versioning hub (2026-10-29): the recorded restore seam would have left
      `versioning-service.md` at ~65%, and moving the mirrors too made the family four
      files, so it got a hub. Both `versioning-service.md` and the old body of
      `docs/dev/versioning.md` split eight ways into `docs/dev/versioning/` (guards, routes,
      storage, hooks, restore, mirrors, frontend, tests) behind the `docs/dev/versioning.md`
      hub, and two Map rows became one.
- [x] `docs/dev/backend-infrastructure.md` (2026-10-29): the recorded seam, which held.
      What wraps every request (the request guard, SPA serving, the app-level exception
      handlers, the server control endpoints and restart loop) moved to
      `docs/dev/http-edge.md`; the job queue, SSE, cancellation and CI stay. Two flat
      siblings, no hub.
- [x] `docs/dev/image-files.md` (2026-10-29): the recorded seam, which held, after adding
      `##` headings. Folder import, rescan/sync, standalone caption import, the drag-`.txt`
      shortcut and the stem-keyed collision rule moved to `docs/dev/image-import.md`; naming,
      cross-dataset moves and copies, and deletes stay. Two flat siblings, no hub.
- [x] The other dev docs, largest first (2026-10-29): none is over budget after the
      image-files split.
- [x] The user docs, each with its README Docs-table and `docs/features.md` rows.
  - [x] `docs/gallery.md` (2026-10-29): three ways rather than the recorded two, which
        would have left 79% behind. Datasets and getting images in moved to
        `docs/datasets.md`, the subfolder sidebar to `docs/subfolders.md`; browsing,
        sorting and manual ordering stay. Three flat siblings, no hub.
  - [x] `docs/workspace.md` (2026-10-29): three ways rather than the recorded two, which
        would have left 67% behind. Starting up, restarting and shutting down, and database
        backups moved to `docs/server.md`, the File Browser to `docs/file-browser.md`;
        dialogs, the top bar and jobs, meters, Split View, Logs and Booru stay. Three flat
        siblings, no hub.
  - [x] `README.md` (2026-10-30): not split. Its budget was raised to 4,000 in
        `scripts/check_docs.py` at the user's call, which leaves it under budget at 3,027;
        the § Features seam stays queued in [pending-splits.md](../pending-splits.md).
- [x] `pending-splits.md` itself (2026-10-29): under budget at ~3,340 once the ml-models
      entry left.

Of the recorded seams, the captioning, file-browser, versioning-service, gallery and
workspace ones were re-cut on execution, because as recorded they would have left one half
at ~93%, ~80%, ~65%, ~79% and ~67% of budget; the backend-infrastructure and image-files
ones held. The lesson carried forward: re-measure before cutting, and check both halves
land under 60%.
