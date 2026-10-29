# External review (GPT_Findings): one deferred item left

An external review of commit `c685ab5` (run on native Windows, report `GPT_Findings.md`, not
checked in) claimed 34 defects. Every claim was independently re-verified on 2026-10-17 in the
Linux dev container, using throwaway databases only. **All 34 held**: 29 were reproduced at
runtime, 5 were confirmed by reading the code, and none were refuted.

**All 34 are fixed on branch `fix/gpt-review`**, except one deferred half of 007. The fixes, in
the order they landed:
- 021, 027 and 015.
- 025, 022, 023, 024, and the restore half of 016 (grouping (a), PM-026).
- 012, 013, 014 and 030 (grouping (b), PM-027).
- 009, 008, 011, 010, and the resize/crop half of 016 (grouping (d), PM-013's fourth amendment).
- 003 and 005 (PM-028).
- 017, 018, 019, 020, 028, 029, 031 and 035 (grouping (e)).
- 032, 033 and 034 (grouping (g), PM-030).
- The final group:
  - 007's request guard and its default to localhost-only binding (PM-031).
  - 026: snapshots now mirror detections (PM-032).
  - 001: the Node floor is 20.19 / 22.12.
  - 002: `api_env` enforces foreign keys by default.
  - 004: the test harnesses are portable, and a Windows backend job runs in CI.

**Lifecycle.** This folder is transient. What remains is a single deferred item:

| File | Item |
|---|---|
| `docs/dev/gpt-review/infra.md` | 007 step 3: a shared token for LAN mode. Deferred by decision. |

Delete the folder once that item is either built or explicitly dropped. Any durable rationale
goes into `docs/dev/backend-infrastructure.md` first. The folder is registered in
`TRANSIENT_DIRS` in `scripts/check_docs.py`: files here need no Documentation Map row and
carry no word budget, but their links and paths are checked.

The verification probes are all gone. Each one was ported into `backend/tests/` or
`frontend/e2e/` with its assertions inverted, then deleted.

## Not verified

- **Windows launch behaviour of `manage.ps1`.** The Windows CI job runs only the backend suite,
  plus a parse check of the script.
- **Real GPU, model and ComfyUI behaviour.** The 032–034 fixes are tested at mock level only.
- **The `sam2_model` tenant's assumption** that upstream `SAM2ImagePredictor.model` is the
  network. `sam2` is not installed in the container. The loader reads the attribute with
  `getattr`, so a miss frees nothing but breaks nothing.
- **GPT's own "blocked" gaps,** which were outside this verification: import, rescan, move and
  copy failure recovery; crash and power-loss interleavings; capacity; and dependency advisories.
