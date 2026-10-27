# External review (GPT_Findings) — verified findings awaiting remediation

An external review of commit `c685ab5` (run on native Windows, report `GPT_Findings.md`, not
checked in) claimed 34 defects. Every claim was independently re-verified on 2026-10-17 in the
Linux dev container, using throwaway databases only. **All 34 hold**: 29 were reproduced at
runtime, 5 were confirmed by reading the code, and none were refuted. Fixed so far, on branch `fix/gpt-review`: 021, 027 and 015; then 025, 022, 023, 024 and the restore half of 016 (grouping (a), PM-026); then 012, 013, 014 and 030 (grouping (b), PM-027).
This folder is the handoff for the remediation sessions.

**Lifecycle**: this folder is transient, like `docs/dev/roadmap/`. When a finding is fixed:
1. Move any durable rationale into the owning `docs/dev/` topic file, and add a postmortem if
   the bug merits one (`docs/dev/postmortems.md`).
2. Turn its probe into a real regression test with **inverted** assertions (see below).
3. Delete its entry here.

Delete the folder when every entry is gone. It sits in a subfolder deliberately: like
`docs/dev/postmortems/`, `scripts/check_docs.py` globs `docs/dev/*.md` non-recursively, so files
here carry no Documentation Map row and no word budget.

## Files

| File | Findings |
|---|---|
| `docs/dev/gpt-review/versioning.md` | 026 |
| `docs/dev/gpt-review/file-mutations.md` | 009, 011, 016 (resize/crop half), 008, 010, 017, 020 |
| `docs/dev/gpt-review/jobs-ml.md` | 032, 033, 034 |
| `docs/dev/gpt-review/frontend.md` | 028, 029, 031 |
| `docs/dev/gpt-review/stats.md` | 018, 019, 035 |
| `docs/dev/gpt-review/infra.md` | 001, 002, 003, 004, 005, 007 |

Each entry gives:
- the claim
- **our** verdict and severity, which differ from GPT's in places
- what was observed
- current code locations
- the probe that reproduces it
- known docs or decisions that bear on it
- fix notes

Fix notes give a direction, not a design; plan each fix against the code at the time.

## Priority (our severity, not GPT's)

| Pri | IDs |
|---|---|
| **P1** | None open. 021 and 027 are fixed on `fix/gpt-review`. |
| P2 | 032, 009, 028, 033, 001, 003+005 (fix together) |
| P3 | 011, 010, 008, 016 (resize/crop half), 017, 018, 019, 020, 026, 029, 031, 034, 035, 002, 004 |
| Decision | 007: unauthenticated API on `0.0.0.0`. This is a deliberate LAN-access choice, so revisit it as policy rather than as a bug. |

Suggested grouping for remediation branches:
- **(a) Versioning integrity:** done — 025, 022, 023, 024 and the restore half of 016 (PM-026). 026 is left, pending a contract decision.
- **(b) Job queue:** done — 012, 013, 014 and 030 (PM-027).
- **(d) File-mutation ordering:** 009, 011, 008, 010.
- **(e) Small correctness items:** 018, 019, 020, 031, 035, 017, 028.
- **(f) CI and tooling:** 001, 003+005, 002, 004.
- **(g) ML lifetime:** 032, 034, 033.

## Already known before the review

Already known before the review:
- **007**: deliberate, and recorded in project memory.
- **008**: CLAUDE.md, the PM-021 bullet: "`images.py` and `filesystem.py` are wholly unconverted".
- **016, resize/crop half**: `docs/dev/bulk-image-jobs.md`, § the batch resize/crop paragraph.
- **026**: `docs/dev/bulk-image-jobs.md`, "Known limitation: a version-restore … does not remap detections".
- **034**: `docs/dev/detection-inference.md`, process-lifetime cache.
- **029, half of it**: `docs/dev/styling.md`, the bare SelectionToolbar overlays.

Docs whose text these findings **prove wrong**. Fix them alongside the bug:
- `docs/dev/comfyui.md` claims that the ComfyUI queue is "never stacked" and that nothing of ours waits in it (033).
- The tie-rule comments in `frontend/src/utils/percentile.ts` and `docs/dev/image-similarity.md` (019).
- The docstring of `utils.rename_with_sidecar`, which says "atomically" (009).

## Probes

`docs/dev/gpt-review/probes/` holds the verification probes. **Every probe asserts the buggy
behaviour**, so today they pass, and after a fix the matching probe *fails*. When fixing, port it
into `backend/tests/` or `frontend/e2e/` with the assertions inverted, then delete the probe.

Backend probes are named `probe_*.py`, so a default pytest run never collects them. Run them from
the repo root with the venv active, in one shell:

```bash
source venv/bin/activate && python -m pytest docs/dev/gpt-review/probes/backend \
  -o python_files='probe_*.py' -q -p no:cacheprovider     # 23 collected; 21 pass without torch (31 before grouping (b), 38 before grouping (a), 42 on 2026-10-17)
```

They use `api_env(tmp_path, foreign_keys=True)` via `probes/backend/conftest.py`. Windows file locks
are simulated with `PermissionError(winerror=32)`, which proves the code path, not the OS behaviour.
`spa_raw_asgi_probe.py` (005) is a standalone script and needs a venv with Starlette ≥1.8.0.

Browser specs in `probes/frontend/` import `./helpers` and are written to run from inside
`frontend/e2e/`. Run them from a copy so they never enter the real suite. Copy the specs plus
`frontend/e2e/helpers.ts` into a scratch dir and symlink `frontend/node_modules` there. Then
point a Playwright config at that dir whose `webServer` runs `bash e2e/serve.sh` with
`cwd: '<repo>/frontend'` (a throwaway DB on :8199), and run `npm run build` first.
8 passed on 2026-10-27, after GPT-014/030's three were ported (11 before that, 13 on 2026-10-17). `gpt018.spec.ts` writes scores straight into the e2e harness's
throwaway SQLite DB, and refuses unless that DB is under a temp dir.

## Not verified

- Real Windows file handles (008/009).
- Windows launch and path failures (004).
- Real GPU, model and ComfyUI behaviour (032–034 are mock-level).
- Whether `_release_to_cpu` frees SAM2's own weights (`sam2` is not installed in the container).
- GPT's own "blocked" gaps, which are outside this verification: import/rescan/move/copy failure
  recovery, crash and power-loss interleavings, capacity, and dependency advisories.
