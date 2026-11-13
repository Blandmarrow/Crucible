# M8 — Structural cleanup

**Status: archive (landed 2026-11-08).** *Record for the M8 line in the
[roadmap](../roadmap.md#landed).* Its gate was met on 2026-11-02, when stage 1 landed with
regression tests; the last landed item merged on 2026-11-08 (PRs #114 to #117 and #120). Archived on
2026-11-13. Every open box below waits on M4 or on an upstream release, and has moved to the
roadmap's § Waiting. The plan, [cleanup-plan/README.md](../cleanup-plan/README.md), stays in
place for those stages.

Four bug fixes, then targeted consolidation rather than a broad restructure. Gate: stage 1 landed
with regression tests; the later stages can stop wherever the duplication stops costing anything.
Detail, hazards and what to leave alone:
[cleanup-plan/README.md](../cleanup-plan/README.md).

- [x] Stage 1: fix B1 (failed job shows success), B2 (stale dataset counts), B3 (bind limit) and
  B4 (unchecked write path). Landed 2026-11-02 on `fix/m8-stage1-bugs`.
- [x] Stage 1 follow-ups 1–6 (cleanup plan § Stage 1 follow-ups). Landed 2026-11-02 on
  `fix/m8-stage1-followups`. Items 7 and 8 there are folded into stages 6 and 3.
- [x] Stage 1 follow-ups 8 (a refusal's reason for the other edit jobs), 10 (batch resize, crop
  and detection crop's stats on a partway failure) and 11 (two hard-cancel edges in the stats
  wrapper). Merged 2026-11-07 as PR #114; 11's shutdown edge is accepted, not fixed.
- [x] Stage 1 follow-up 9 (the rest of the unchunked `IN` class). Merged 2026-11-07 as PR #115.
  `_apply_bulk_filters` takes a 422 cap at `SELECT_ALL_ID_CAP` rather than chunking, by the
  user's choice on 2026-11-07.
- [ ] Item 9's remainder: chunk batch move and copy to another dataset. Needs M4 merged, which
  rewrites both routes. Items 12 and 13 are folded into stage 6, beside item 7.
- [x] Stage 1 follow-up 14: guard the copy-mode thumbnail in detection crop and the upscale and
  LUT loop, as inpaint does. Merged 2026-11-08 as PR #116.
- [ ] Stage 1 follow-up 15: the same order for `images.py`'s single-image copy crop and
  `crop_upscale`'s new-file worker. Needs M4 merged, which rewrites `images.py`.
- [x] Stage 2: one shared helper for a job's image scope. Landed 2026-11-04 on
  `refactor/m8-stages-2-3`.
- [x] Stage 3: a shared job loop for upscale and LUT. Needs stage 2. Landed 2026-11-04 on the
  same branch.
- [x] Stage 4: `DatasetsPage` dialogs into components. Merged 2026-11-08 as PR #117.
- [ ] Stage 5: `SelectionToolbar` dialogs into forms. Needs M4 merged (M7 has).
- [ ] Stage 6: the detail page's inline tools onto those forms. Needs M4 merged (M7 has), and B1.
- [ ] Stage 7: `StatsPage` into files. Needs M4 merged.
- [ ] Stage 8: split `images.py` by section. Needs M4 merged and stage 2.
- [x] Any time: fix the coverage config, and correct CLAUDE.md § Data flow. Merged 2026-11-08
  as PR #120: `.coveragerc` sets `concurrency = greenlet,thread`, which lifts
  `images.py` from 55% to 89%.
- [ ] Remove the `minimatch` override in `frontend/package.json` (PR #111, for
  GHSA-g3hv-75wg-m249) once `eslint` and `typescript-eslint` depend on `minimatch ^11`
  themselves. Waits on upstream; check with `npm view eslint dependencies.minimatch`.
