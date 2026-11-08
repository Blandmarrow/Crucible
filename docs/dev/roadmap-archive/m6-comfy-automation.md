# M6 — ComfyUI per-row folders and automation

**Status: archive (landed 2026-11-07).** *Record for the M6 line in the
[roadmap](../roadmap.md#landed).* Merged as PR #110, built whole on `feat/m6-comfy-automation`
([decision 7](../decisions/07-m6-built-whole-on-one-branch.md)); the branch is deleted.

Gate: the whole build below, done on `feat/m6-comfy-automation` and merged once
([decision 7](../decisions/07-m6-built-whole-on-one-branch.md)). Plan: a transient design
plan, retired after the merge ([§ The design plan](#the-design-plan)), revised by
[decision 8](../decisions/08-templates-first-filed-by-folder.md) and
[decision 9](../decisions/09-runs-step-forward-on-job-completion.md). Tick each stage when its
gates are green and it is pushed.

- [x] Per-row folders ported (2026-11-03), the comfyui doc splits, §7 the mock ComfyUI, §2a
  blueprints, §2b the outline call, §2c Top up, §3 the rewrite pass, the rebase onto M8
  stages 2–3, §4 the stepping run, §5 the goal loop, §6 the dry run, snapshot and recipes, and
  the review-fix pass (all by 2026-11-04). What each delivered is in the topic files
  `docs/dev/comfyui.md` routes to, and its migration is in the chain below.
- §8, the ComfyUI page rework, so prompts are easier to categorize and the prompt library
  easier to use (the user's call, 2026-11-04: it lands before the merge). Scoped with the
  user on 2026-11-05 and the mockup approved; what the user found wrong, the mockup and the
  rulings are [§ The page rework](#the-page-rework). Its open questions were ruled by the
  user on 2026-11-05.
  - [x] §8a, the layout, and §8b, the plan rail, folder tree and grouped table (2026-11-05).
    What each delivered is `docs/dev/comfyui/page.md` and `docs/dev/comfyui/blueprint-tab.md`.
  - [x] §8c, the library side panel, Copy to plan, the row menu and drag and drop
    (2026-11-05). What it delivered is `docs/dev/comfyui/library-dnd.md`.
  - [x] §8d: the Add menu, the selection bar, the paste dialog on `ModalShell`
    (2026-11-05). What it delivered is the Rows toolbar in `docs/dev/comfyui/page.md`.
  - [x] §8 review fixes (2026-11-05), under four rulings by the user that day:
    [§ Review fixes](#review-fixes-2026-11-05).
- [x] §9, two installs and a 16 GB card (2026-11-06), added so the branch could be tested on
  a second machine before the merge: `CRUCIBLE_PORT`/`CRUCIBLE_VITE_PORT`, the
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

## The design plan

M6 was planned in a transient folder, `comfy-automation-plan/`, scoped on 2026-08-15 and
revised on 2026-11-03. Its lifecycle was to move each stage's durable rationale into a
topic file as the stage landed, then delete the folder. It was deleted on 2026-11-08, after
an audit of every claim in it against the topic files: the reasons for its rejected designs
went into the `docs/dev/comfyui/` files that own the code, and its history is the sections
below. Its §8c drag table was dropped rather than moved: it was out of date (a drop between
rows uses `rows/place`, not `rows/reorder`), and `docs/dev/comfyui/library-dnd.md` covers
every drag.

**The load-bearing idea** was that the LLM invents entities and code does the
combinatorics (`docs/dev/comfyui/structured.md` § The idea), then decision 8 had code write
the prompts too. **Its cut point**, had the arc stopped early, was after Top up: foldered
datasets that refill on request are most of the value.

## What the last session before the merge recorded

Moved from the roadmap's Next session item on 2026-11-07, word for word except where the merge
changed the tense.

- **The hand checks.** The hand checks the milestone owed were run on 2026-11-07 on the second
  machine (a 16 GB card) against a real ComfyUI and a real Strata, with the user's browser
  checks in Firefox. All passed or were fixed that day; the results, check by check, are in
  [§ Hand checks](#hand-checks).
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
- **Migrations**, following `c2b8e4f6a1d7`, each with what it adds (annotated on
  2026-11-08):
  - `e5c1a9d3f7b2`, §1: `comfy_rows.subfolder` and `leaf_id`. Ported as 73198f2,
    cherry-picked from `comfy-per-row-subfolders`' c2d6a6d.
  - `b7d2f4a9c1e3`, §2a: blueprints and leaves, with §2b's `brief` and `draft`.
  - `d4a8c2e6f1b9`, §3: `comfy_rows.prompt_origin`.
  - `f3b9d1a7c5e2`, §4: the `runs` table.
  - `c8e4a6b2d0f7`, §5: the provider's `billed` setting.
  - `a6d2f8c4e0b3`, §6: `Run.cancel_requested`.
  - `b2e7c9a4d1f6`, §6: the `run_recipes` library.
  - `c4a8e2f6b9d3`, §6: the remembered start form.
  - `d5b7f9a1c3e6`, §8b: `comfy_plans.declared_folders`.
  - `e7d1c5a9b3f2`, §8c: nested library categories.
  - `a3c7e9b1d5f2`, §9: the GPU handoff setting.

  Decisions 7–10 came with the merge, so the next decision is number 11.

What stayed open is in the roadmap's Next session item for M6.

## Rulings by the user

Decisions 7–10 are in the log. The rulings below were made on the branch. Each is now
behaviour in a topic file; this list keeps who settled what, and when.

- **2026-11-03.**
  - Decisions 8 (templates, folders, Top up) and 9 (the stepping run).
  - A leaf's target is edited per scenario and stored per leaf.
  - A deleted leaf's rows count toward nothing. The preview reports them, and Apply deletes
    only their pending rows.
  - The rewrite pass is its own job type, `comfy_rewrite`, overturning reuse of
    `comfy_prompts`. Top up refuses rows still waiting for it, `prompt_origin` protects hand
    edits, and leaves are rewritten one at a time, with no concurrency setting.
  - The gallery entity filter on `comfy_leaves` is not built until someone misses it.
- **2026-11-04, §4.** The gate moved to §5 with the loop, and the snapshot stage to §6 with
  the recipe library. A run's settings come from a compact form, stored on the row as its
  recipe. After the first hand runs, the user asked for an LLM to be unloaded after
  captioning, by a stage of its own (`free_caption_model`).
- **2026-11-04, §5.**
  - The rewrite is an optional stage inside the loop.
  - The LLM-call cap applies to paid providers only, through a per-provider `billed`
    setting defaulted from `_is_remote`.
  - The kept rule holds everywhere, Preview and the manual Top up included.
  - Rejects go in one tree under the base folder, keeping the leaf's path.
  - §4's open hand checks ran before the loop was built.
- **2026-11-04, §6.**
  - The dry run is its own button, with its report under the form.
  - Start runs the same checks and refuses on the first error, before writing a run.
    Warnings never block.
  - Saved recipes keep this machine's provider ids and export folder. The JSON export strips
    both, and an import resolves providers by name or blanks them with a note.
  - Three extras: *Use these settings*, raising a cap to carry a run on (`extend_run`), and
    the remembered start form.
- **2026-11-04, the review-fix pass.** A run fails when a generate or caption job failed
  every item, and `gate_rejected` is cleared when an image leaves `rejects/`.
- **2026-11-05:** [§ The page rework](#the-page-rework) and
  [§ Review fixes](#review-fixes-2026-11-05).
- **2026-11-07:** the fixes under [§ Hand checks](#hand-checks).

## The page rework

§8, scoped with the user on 2026-11-05, who asked for it to land before the merge.

**What the user found wrong:**
- Rows were one flat list. Each row had a folder (`subfolder`), but only as a column, and
  nothing grouped by it. The prompt library's categories were flat too.
- Moving prompts between plans was buried in `PromptLibraryModal`'s *Other plans* tab, and
  a copy dropped the row's folder.
- The Blueprint tab scrolled away from its controls. `ComfyPage` was one scroll container,
  and Apply and Top up sat below every editor.
- The Rows toolbar showed eleven buttons in one line, whether or not a row was selected.

**The mockup.** An interactive mockup of the Rows and Blueprint tabs, which the user
approved as the direction, is the private artifact
https://claude.ai/artifact/5Tukrf24L39JBw3YFmgaP1. It is a sketch of the layout and the
gestures, not a spec: its data is invented and it saves nothing.

**The open questions**, ruled on 2026-11-05 when the user approved the §8b plan's
recommendations:
1. **A row's category is its output folder.** A separate grouping label was rejected: two
   trees that disagree.
2. **Moving a blueprint row asks, then unlinks it, and a blueprint folder is locked** in the
   tree (`docs/dev/comfyui/structured.md` § Moving a blueprint row). The mockup unlinked such
   rows instead, but that leaves the leaf's path in place, so the next Apply would quietly
   queue a full target into the old folder name.
3. **Rows dropped on another plan copy by default**, and Shift moves.
4. **Build order** §8a → §8b → §8c → §8d.

**The other rulings that day:**
- §8a: a folder's sample prompts open on demand, not as a column.
- §8c: reorder goes across folders too; categories get a menu *and* drag; library prompts
  dropped on the table land in the folder in view.
- §8d: the selection bar keeps a fixed slot; its actions are buttons opening the existing
  dialogs; Run selected lives in the selection bar only. Smaller departures from the
  mockup: the *Add ▾* menu keeps today's labels rather than the mockup's longer ones, the
  filters moved up into the toolbar's line to pay for the slot, and *Reset failed* stays in
  the toolbar, since it is not a selection action.

**What the reviews found.** An Opus review of §8b found a case-blind `LIKE` that let a
folder delete take another folder's rows (`docs/dev/comfyui/queue.md`). §8c found that a
live `comfy_generate` run did not lock its plan's rows on the server, and fixed it; an Opus
review of §8c found no backend bug, and its five frontend findings were fixed, each pinned
by a test where e2e can reach it.

## Review fixes, 2026-11-05

A review of all of §8 (three Opus reviewers and a doc audit) found no high-severity bug. It
fixed what it found, under four rulings by the user that day:
1. **One lock for deletes.** Plan delete and `rows/delete` refuse whatever folder delete
   refuses (`docs/dev/comfyui/queue.md`). Plan delete had no guard at all.
2. **A live Run's plan takes no new rows.** `rows/copy`, `rows/bulk` and `POST …/rows`
   refuse it, as Apply does. This reversed §8c's "a copy has no guard on the target".
3. **The selection is pruned to the rows on show**, whatever hid them
   (`docs/dev/comfyui/page.md`).
4. **One app-wide guard**, `FileDropGuard`, stops a desktop file dropped outside a drop zone
   from opening in the tab (`docs/dev/frontend-core.md`).

It also:
- refused a folder or category move that would write a path over 512 characters;
- made a category delete under a search quote the whole subtree's count;
- refused drops that change nothing, and drop targets under the sticky header;
- gave `ContextMenu` keyboard focus;
- named the copy response's two skip reasons;
- gave back the Library tab's *Select all* and the Other plans tab's row status;
- refreshed the rail's counts after a cell edit, *Add row* and *Reset failed*.

## Hand checks

**2026-11-04, on Windows.** §3 against a real LLM (`qwen3.8-27b` in LM Studio): a draft and
a rewrite both worked, and the draft review, the label wording and the rewrite count's
refresh came out of that test. §4's full runs against a real ComfyUI and LM Studio: every
stage worked, `/free` really freed ComfyUI's VRAM, and the scorers unloaded after scoring
(measured with the Windows per-process GPU counters).

**2026-11-07, on the box.** The checks the milestone owed by hand, run on the second machine
(a 16 GB card, Linux) in a dev install on its own port with a throwaway database: a real
ComfyUI with a real three-pass workflow, a real Strata server standing in for LM Studio, and
the user in Firefox for what only a browser shows. Prod's data was never written.

| Check | Result |
|---|---|
| Backend suite on Linux | 1805 passed, no skips (torch and cv2 present); `check_migrations.py` clean |
| §3 Stop mid-rewrite; a hand-edited row left alone | Pass. The stop ended the job `cancelled` with the committed rows kept and only the leftovers rewritten next time; the `manual` row came through byte for byte, and edits during the pass got 409 |
| §4 Pause; restart mid-run (clean and `kill -9`); TopBar cancel | Pass. A hard kill wastes one render (now in `docs/dev/comfyui/automation.md`); the real TopBar click exposed the Runs page bug below |
| §5 Two-pass loop to target; a cap stop | Pass. Met in two passes with one snapshot; `max_passes: 1` stopped `short` on `passes` with `can_extend` |
| §6 Dry run; ComfyUI off; Snapshot + Cancel; recipes; a pass cap raised | Pass, except the snapshot's cancelling wait: about 1 s on 834 images, too short to hold open, so it rests on `test_run_snapshot.py` |
| Review fixes: every row rejected; a reject moved back | Pass. A real rejection (the box lacked RES4LYF's `beta57` scheduler) failed the run and Resume retried only those rows; the flag and the Stats count cleared |
| §8b–§8d and the §8 review fixes, in Firefox | Pass after two fixes below |
| §8c category migration on a messy library | Pass on a crafted library: the user's real one held one clean category, so the dev database was built to `d5b7f9a1c3e6`, seeded with 13 hand-made messy rows beside the 35 real ones, and upgraded; every row matched a hand-written expectation |
| §9 handoff run with `nvidia-smi`; a second install beside the first | Pass. Strata 15.3 GB for the rewrite, gone before generate; ComfyUI 6.5–6.8 GB alone; 270 MiB after its handoff; Strata back for captions and gone after. A foreign ComfyUI prompt survived a dev cancel |

Fixed that day, each with a regression test, under rulings by the user:
- **`ContextMenu` keeps a disabled item focusable but inert.** A folder's menu during a run
  had every item disabled, so nothing took focus and the arrows were dead
  (`docs/dev/styling.md`).
- **The library panel's selection actions keep a fixed slot**, as the rows bar's did on
  2026-11-05: the block appeared on the first tick and pushed the tree down
  (`docs/dev/comfyui/library-dnd.md`).
- **The Runs page refetches whenever a job of its dataset changes status.** It polled only
  while its own last fetch held a running run, so a run started anywhere else, and its
  cancel, stayed invisible until the page was reopened (`docs/dev/comfyui/run-start.md`).

## Traps

Each of these fails quietly: no exception, and no visibly wrong number. The plan kept them
as one list; each now lives in the topic file named.
- **`declare_subfolders` commits**, so it is called above an import loop, never inside it
  (`docs/dev/comfyui/run.md`).
- **`advance_run()` runs after the job's commit.** An exception there is logged and
  swallowed, or a finished job looks failed (`docs/dev/comfyui/automation.md`).
- **Shortfall and "kept" are recomputed from the DB**, never accumulated, because dedupe
  deletes rows and stale scores stop counting (`docs/dev/comfyui/structured.md`).
- **A budget is checked before the stage it guards**, not after. This is why the image
  clamp sits in `prompts` (`docs/dev/comfyui/run-budgets.md`).
- **`slugify_filename` answers `"image"`** for an all-punctuation label, a silently wrong
  path segment (`docs/dev/comfyui/structured-expansion.md`).
- **An LLM asked to enumerate a cross product** returns most of it, and describes the same
  entity differently each time (`docs/dev/comfyui/structured.md` § The idea).
- **An empty id list widens to the whole dataset**, so a stage with no ids is skipped,
  never sent `[]` (`docs/dev/comfyui/run-stages.md`).
