# External review (GPT_Findings) — verified findings awaiting remediation

An external review of commit `c685ab5` (run on native Windows, report `GPT_Findings.md`, not
checked in) claimed 34 defects. Every claim was independently re-verified on 2026-10-17 in the
Linux dev container, using throwaway databases only. **All 34 hold**: 29 were reproduced at
runtime, 5 were confirmed by reading the code, and none were refuted. Fixed so far, on branch `fix/gpt-review`: 021, 027 and 015; then 025, 022, 023, 024 and the restore half of 016 (grouping (a), PM-026); then 012, 013, 014 and 030 (grouping (b), PM-027); then 009, 008, 011, 010 and the resize/crop half of 016 (grouping (d), PM-013's fourth amendment); then 003 and 005 (PM-028); then 017, 018, 019, 020, 028, 029, 031 and 035 (grouping (e)).
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
| `docs/dev/gpt-review/jobs-ml.md` | 032, 033, 034 |
| `docs/dev/gpt-review/infra.md` | 001, 002, 004, 007 |

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
| P2 | 032, 033, 001 |
| P3 | 026, 034, 002, 004 |
| Decision | 007: unauthenticated API on `0.0.0.0`. This is a deliberate LAN-access choice, so revisit it as policy rather than as a bug. |

Suggested grouping for remediation branches:
- **(a) Versioning integrity:** done — 025, 022, 023, 024 and the restore half of 016 (PM-026). 026 is left, pending a contract decision.
- **(b) Job queue:** done — 012, 013, 014 and 030 (PM-027).
- **(d) File-mutation ordering:** done — 009, 008, 011, 010 and the resize/crop half of 016 (PM-013's fourth amendment).
- **(e) Small correctness items:** done — 017, 018, 019, 020, 028, 029, 031 and 035. Five new gaps
  surfaced and were fixed alongside: the same zero-size 500 on crop (GPT-020's sibling), a
  cross-dataset subfolder move that renamed files into the wrong dataset's folder, the File
  size histogram showing only its first bar (client labels lacked the server's " MB"), an
  invalid regex *replacement template* (the other half of GPT-031), and stale docs claiming
  GalleryPage has a page-level Escape handler.
- **(f) CI and tooling:** 001, 002, 004. 003+005 are done (PM-028).
- **(g) ML lifetime:** 032, 034, 033.

## Already known before the review

Already known before the review:
- **007**: deliberate, and recorded in project memory.
- **026**: `docs/dev/bulk-image-jobs.md`, "Known limitation: a version-restore … does not remap detections".
- **034**: `docs/dev/detection-inference.md`, process-lifetime cache.

Docs whose text these findings **prove wrong**. Fix them alongside the bug:
- `docs/dev/comfyui.md` claims that the ComfyUI queue is "never stacked" and that nothing of ours waits in it (033).

## Probes

`docs/dev/gpt-review/probes/` holds the verification probes. **Every probe asserts the buggy
behaviour**, so today they pass, and after a fix the matching probe *fails*. When fixing, port it
into `backend/tests/` or `frontend/e2e/` with the assertions inverted, then delete the probe.

Backend probes are named `probe_*.py`, so a default pytest run never collects them. Run them from
the repo root with the venv active, in one shell:

```bash
source venv/bin/activate && python -m pytest docs/dev/gpt-review/probes/backend \
  -o python_files='probe_*.py' -q -p no:cacheprovider     # 4 collect without torch (probe_caption_unload needs it to collect, probe_gdino to run); 3 pass (12 before grouping (e), 23 before (d), 31 before (b), 38 before (a), 42 on 2026-10-17)
```

They use `api_env(tmp_path, foreign_keys=True)` via `probes/backend/conftest.py`. Windows file locks
are simulated with `PermissionError(winerror=32)`, which proves the code path, not the OS behaviour.

No browser probes remain: grouping (e) ported the last five (018, 028, 029 ×2, 031) into
`frontend/e2e/`, and `probes/frontend/` is gone.

## Not verified

- Windows launch and path failures (004).
- Real GPU, model and ComfyUI behaviour (032–034 are mock-level).
- Whether `_release_to_cpu` frees SAM2's own weights (`sam2` is not installed in the container).
- GPT's own "blocked" gaps, which are outside this verification: import/rescan/move/copy failure
  recovery, crash and power-loss interleavings, capacity, and dependency advisories.
