# M6 — ComfyUI per-row folders and automation

**Status: archive (landed 2026-11-07).** *Record for the M6 line in the
[roadmap](../roadmap.md#landed).* Merged as PR #110, built whole on `feat/m6-comfy-automation`
([decision 7](../decisions/07-m6-built-whole-on-one-branch.md)); the branch is deleted.

Gate: the whole build below, done on `feat/m6-comfy-automation` and merged once
([decision 7](../decisions/07-m6-built-whole-on-one-branch.md)). Plan:
[comfy-automation-plan/README.md](../comfy-automation-plan/README.md), revised by
[decision 8](../decisions/08-templates-first-filed-by-folder.md) and
[decision 9](../decisions/09-runs-step-forward-on-job-completion.md). Tick each stage when its
gates are green and it is pushed.

- [x] Per-row folders ported (2026-11-03), the comfyui doc splits, §7 the mock ComfyUI, §2a
  blueprints, §2b the outline call, §2c Top up, §3 the rewrite pass, the rebase onto M8
  stages 2–3, §4 the stepping run, §5 the goal loop, §6 the dry run, snapshot and recipes, and
  the review-fix pass (all by 2026-11-04). What each delivered is the plan's § Landed stages.
- §8, the ComfyUI page rework, so prompts are easier to categorize and the prompt library
  easier to use (the user's call, 2026-11-04: it lands before the merge). Scoped with the
  user on 2026-11-05 and the mockup approved; the *what* is
  [comfy-automation-plan/README.md § 8](../comfy-automation-plan/README.md#8-the-comfyui-page-rework-landed).
  Its open questions were ruled by the user on 2026-11-05.
  - [x] §8a, the layout, and §8b, the plan rail, folder tree and grouped table (2026-11-05).
    What each delivered is the plan's § Landed stages.
  - [x] §8c, the library side panel, Copy to plan, the row menu and drag and drop
    (2026-11-05). What it delivered is the plan's § 8c.
  - [x] §8d: the Add menu, the selection bar, the paste dialog on `ModalShell`
    (2026-11-05). What it delivered is the plan's § 8d.
  - [x] §8 review fixes (2026-11-05), under four rulings by the user that day: the plan's
    [§ Review fixes](../comfy-automation-plan/README.md#review-fixes-2026-11-05).
- [x] §9, two installs and a 16 GB card (2026-11-06): `CRUCIBLE_PORT`/`CRUCIBLE_VITE_PORT`, the
  orphan sweep's refusal on a mismatched tree, and the Strata GPU handoff
  (`docs/dev/comfyui/gpu-handoff.md`, [decision 10](../decisions/10-unload-strata-while-comfyui-generates.md));
  running several installs is `docs/dev/multiple-installs.md`. Its migration `a3c7e9b1d5f2` is
  the branch's new last.
- [x] Merge (2026-11-07, PR #110): no rebase was needed, since `main` had not moved, and M4
  had not merged, so nothing re-parented. Every gate green, the non-blocking Windows job
  included (1808 passed). CI's first run of the branch failed nine run tests that a torch-free
  runner exposed (`conftest.stub_aesthetic_scorer`, `docs/dev/backend-tests.md`), and CodeQL's
  two `py/path-injection` alerts on the dry run's export probe were dismissed as by design
  after the probe was put behind `sanitize_abs_path`. An end-of-branch doc audit fixed 14
  claims overtaken by later code.

## What the last session before the merge recorded

Moved from the roadmap's Next session item on 2026-11-07, word for word except where the merge
changed the tense.

- **The hand checks.** The hand checks the milestone owed were run on 2026-11-07 on the second
  machine (a 16 GB card) against a real ComfyUI and a real Strata, with the user's browser
  checks in Firefox. All passed or were fixed that day; the results, check by check, are in
  [the plan's § Hand checks on the box](../comfy-automation-plan/README.md#hand-checks-on-the-box-2026-11-07).
  Three failures found there were fixed under rulings by the user: `ContextMenu` keeps a
  disabled item focusable, the library panel's selection actions keep a fixed slot, and the
  Runs page refetches on any job of its dataset. LM Studio itself was not used (Strata stood
  in); its handoff path was checked by hand on 2026-11-04.
- **Not held open: §6c's "cancelling after the snapshot" state.** On the box a snapshot of
  834 images took about 1 s, too short to hold the wait open by hand, so its UI text and a
  second Cancel during it rest on `test_run_snapshot.py`'s gated snapshot.
- **What the box checks found beyond the owed list was fixed the same day**, under the
  user's rulings: a run that ends `failed` or `cancelled` frees ComfyUI and any local LLM it
  left loaded, and a `TopBar` cancel of a queued child steps its run at once
  (`docs/dev/comfyui/automation.md` § Cancel, pause, resume, restart); a paused run shows an
  amber rail dot and a `TopBar` chip, and the page greys what the server would refuse
  (`docs/dev/comfyui/page.md`); and Restart and Shutdown work under `manage dev`'s `--reload`
  (`docs/dev/http-edge.md`), a bug `main` had too. That Restart was confirmed live on Linux.
  The owed Windows check, run on 2026-11-07 in a real console window, **failed**: `Cmd-Dev`
  ran uvicorn in a `Start-Job`, where the reloader's Ctrl+C never reached its worker, so every
  reload hung, Restart or a saved `.py` alike (PM-033, on `main` since May). Fixed the same
  day: the backend now runs in a console window of its own, and the check passed (two
  Restarts back in ~2 s each, Shutdown, the window closed when Vite exits).
- **Migrations.** `e5c1a9d3f7b2` → `b7d2f4a9c1e3` → `d4a8c2e6f1b9` → `f3b9d1a7c5e2` →
  `c8e4a6b2d0f7` → `a6d2f8c4e0b3` → `b2e7c9a4d1f6` → `c4a8e2f6b9d3` → `d5b7f9a1c3e6` →
  `e7d1c5a9b3f2` → `a3c7e9b1d5f2`, following `c2b8e4f6a1d7`. Decisions 7–10 came with the
  merge, so the next decision is number 11.

What stayed open is in the roadmap's Next session item for M6.
