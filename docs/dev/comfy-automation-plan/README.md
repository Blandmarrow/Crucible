# M6 plan — ComfyUI structured generation and automated dataset runs

**Status: living.** Scoped in a design session on 2026-08-15 and revised on 2026-11-03, when
the per-row folder work (§1) was ported to `main`'s layout on `feat/m6-comfy-automation`. Two
asks share one spine: generating a *foldered* prompt set from one sentence, and running a whole
dataset end to end without babysitting it. The first is useful on its own; the second depends
on it and holds the architectural risk.

**The whole build stays on one branch and merges once**
([decision 7](../decisions/07-m6-built-whole-on-one-branch.md)). The order of work is
[roadmap.md § M6](../roadmap.md#m6--comfyui-per-row-folders-and-automation); this file owns the
*what* of each stage, and the 2026-11-03 revisions are
[decision 8](../decisions/08-templates-first-filed-by-folder.md) (templates, folders, Top up) and
[decision 9](../decisions/09-runs-step-forward-on-job-completion.md) (the stepping run).

**Lifecycle:** this folder is transient (`TRANSIENT_DIRS` in `scripts/check_docs.py`), so it has
no Map row and no word budget. When a stage lands, move its durable rationale into the topic file
named in § Documentation this arc owes and delete the stage from here; delete the folder when
everything is done.

Stages §1 to §8 are built on this branch; only the merge remains. Read `CLAUDE.md`, `docs/dev/comfyui.md`,
`docs/dev/comfyui/run.md` and `docs/dev/comfyui/prompts.md` before implementing, and prefer the repo's conventions over the
sketches here.

## What this arc is

A plan holds a list of prompt rows, and since §1 each row carries its own destination folder, so
a run files its outputs into a tree. But the rows are still written or generated one flat batch
at a time, so the *structure* is whatever you typed.

After §2 you describe a dataset once — *"a medieval set, N characters, each in M biomes alone
and together, K images per scenario"* — review the taxonomy the model proposes, edit it, and get
a foldered dataset whose structure you chose. Top up then fills any folder that came up short.
After §4 and §5 the same description drives a run that generates, scores, dedupes, gates and
captions on its own, and keeps going until each folder holds K images that pass your bar.

The load-bearing idea is that **the LLM invents entities and code does the combinatorics**.
Asking a model to enumerate 55 leaves of a cross product gets you 50 of them, with the character
described differently in each. Asking it for 10 characters and 5 biomes, then multiplying in
Python, gets you all 55 and the same character every time. Decision 8 takes that one step
further: **code also writes the prompts**, from a template, so the LLM is called once per
blueprint rather than once per leaf.

## Sections

**Section numbers are stable identities, not an order.** The build order is the roadmap's M6
checklist alone. In short: the `docs/dev/comfyui.md` split and §7 (both done 2026-11-03), then
§2a, §2b, Top up (§2c), §3, a rebase after M8 stages 2–3, §4, §5 and §6 (all landed), then
the ComfyUI page rework (§8, landed), then the merge.

**§1 (per-row `subfolder` + `leaf_id`) is ported**, commit 8dee5de on the branch; its rationale
lives in `docs/dev/comfyui/queue.md` (the columns and endpoints) and `docs/dev/comfyui/run.md` (how a run
files each row). The `comfy_leaves` table it left for later was built in §2a, and a blueprint's
Apply is now the writer of `leaf_id` (`docs/dev/comfyui/structured.md`).

**§7 (the mock ComfyUI server) has landed**, so every later stage tests against it:
`backend/tests/fake_comfy.py`, documented in `docs/dev/comfyui/run.md` § Testing the run. pytest
reaches it in-process; Playwright reaches it on a real port. Each stage that adds a journey
extends `frontend/e2e/comfy-run.spec.ts` until the whole arc runs there GPU-free, ComfyUI-free
and LLM-free: hand-authored blueprint → expand → run → foldered gallery → Top up. `POST /free`
is already served and recorded, for §4's VRAM handoff.

**If the arc has to be cut short, cut after Top up.** Foldered datasets that refill themselves
on request are most of the value; a manual "run these stages in order" habit covers the rest.

## Decisions

Recorded so an older draft cannot reintroduce them.

- **Axes, not leaves.** The outline call returns entities and combination rules; expansion is
  deterministic code. See §2a.
- **Templates, not per-leaf calls** (decision 8). The outline also returns variation pieces, and
  code composes every prompt. A per-leaf LLM rewrite is §3, and optional.
- **Folders, not labels** (decision 8). A leaf is filed by its rendered path; `comfy_leaves`
  records which entities it combines, so "which character is this image" stays answerable
  through image → row (`image_ids`) → leaf (`leaf_id`).
- **"Individually and together" is `take: N` on an axis**, expanded with `itertools.combinations`,
  not a separate scenario mode. One mechanism, not two.
- **Per-leaf targets are absolute**, never "N more" — the property the existing `comfy_prompts`
  job already has, applied per leaf. It is what makes Top up fill only what is missing, and it is
  the foundation of resume (§4) and the goal loop (§5).
- **Top up before the goal loop** (decision 8).
- **An automated run is a `Run` row stepped forward on job completion** (decision 9), with stages
  calling service functions.
- **The automated run gets its own dataset-scoped routed page**, not a third `ComfyPage` tab. The
  run spans captioning, scoring, gating and export; only its first stages are about ComfyUI. The
  counter-argument — that a tab would dodge the routed-page checklist in
  `docs/dev/panes-routing.md` — was heard and rejected. It costs that checklist plus
  `PaneHeader`'s `PAGE_OPTIONS`.
- **The gate moves failures to a `rejects/` subfolder. It does not delete.** Reversible,
  inspectable, and it composes with the per-row folders.

### Rejected

- **An LLM call per leaf by default**, and **labels per axis value** — decision 8.
- **A second `JobQueue` with a parent job that polls its children**, and **calling router
  handlers in-process** — decision 9. The traps the old design carried (a long-lived session
  that never sees the child finish, the test harness's loop-bound second queue, a parent
  cancelled while its child runs) went with it.
- **A `pair:K` scenario mode.** Subsumed by `take: N`.
- **A new job type for template expansion.** Expansion runs inline (§2a).
- **The rewrite pass reusing `comfy_prompts`** (overturned by the user, 2026-11-03). Reuse would
  have kept the 409 guards for free, but that job appends rows and reports "N generated", while
  the rewrite updates rows and reports different numbers. The job, `TopBar` and
  `jobReconcile.ts` would all have branched on a mode flag. It is `comfy_rewrite`, added to the
  four guards by hand (§3).
- **Inlining every stage into one giant job.** It would lift each stage's `_run` closure out of
  its router with high regression risk, and throw away every existing preflight check.
- **Making the main queue multi-worker.** The single worker is what serializes GPU work. Two
  workers let captioning and a ComfyUI run hit the same card.
- **A snapshot stage on by default.** It is gated on versioning, which most installs have off,
  and snapshot jobs are deliberately non-cancellable. Opt-in, and it *skips with a recorded note*
  when versioning is off.
- **Dedupe-with-delete on by default.** `resolve_duplicates` deletes rows and unlinks files; an
  unattended loop gets `flag` by default and `resolve` only as a choice.

## 2. Structured generation — axes, templates, Top up

Read `docs/dev/comfyui/prompts.md`.

### §2a — Blueprints, expansion and templates (landed)

Built on 2026-11-03. Everything durable — the tables, the spec, the pure expansion and its
walk, fill accounting, orphans, and Apply's guard rails — now lives in
`docs/dev/comfyui/structured.md`.

Two departures from the sketch that stood here:
- **Expansion runs inline and is never a job.** Its row cap makes every expansion small. The
  mutual exclusion the sketch got from reusing `comfy_prompts` comes from Apply's 409 on a
  live `comfy_generate` or `comfy_prompts` job, plus a per-plan lock.
- **`sample:K` ranks combinations by a hash of their entity ids** rather than seeding
  `random.sample`, so a Python upgrade cannot re-sample a blueprint.

§3's rewrite pass is the slow part, so it is a job of its own, `comfy_rewrite`.

### §2b — The outline call and the review panel (landed)

Built on 2026-11-03. The review panel arrived with §2a, and it is reachable without an LLM,
which is how the e2e journey reaches it. The outline call is the `comfy_outline` job; it saves
a draft on the blueprint for *Use it* / *Discard*. Both are documented in
`docs/dev/comfyui/structured-llm.md` § The outline call and `docs/dev/comfyui/blueprint-tab.md`.

Grouping rows by leaf in `ComfyRowsTable` was optional and is not built: the Folder column
already shows each row's leaf path.

### §2c — Top up (landed)

Built on 2026-11-03: `POST …/blueprint/top-up` is Apply, then reset the blueprint's failed
rows, then run its pending ones through `_start_run`. It is documented in
`docs/dev/comfyui/structured.md` § Top up. §5's loop is this, repeated with gates; §4 calls the
same service functions (`blueprint_service.apply`, `prepare_top_up`).

Rows whose leaf a blueprint edit deleted were settled with §2a (the user's ruling,
2026-11-03): they count toward nothing, the preview reports them, and Apply can delete their
pending rows only. Top up reuses Apply, so it inherits that. Shortfall counts images; pending,
running and failed rows count as future images; and Top up resets failed rows to pending
before running (`docs/dev/comfyui/structured.md` § Fill accounting).

## 3. The optional LLM rewrite pass (landed)

Built on 2026-11-03, with the outline's instruction-order fix. Everything durable is in
`docs/dev/comfyui/structured-llm.md` § The rewrite pass; `prompt_origin` is in
`docs/dev/comfyui/queue.md`, and Top up's refusal in `docs/dev/comfyui/structured.md` § Top up.

The user settled four points that day: the pass is its own job type (`comfy_rewrite`), Top
up refuses to run rows still waiting for it, hand edits are protected by `prompt_origin`,
and leaves are rewritten one at a time.

Departures from the sketch that stood here, all from the review before it merged:
- **A strict parser of its own**, `parse_rewrite`, instead of `parse_prompts`. That parser's
  line-splitting fallback accepted a reply cut off at the token limit as one prompt.
- **Any 4xx other than 401, 403 or 404 fails one leaf rather than the run**, and a refused
  schema never fixes the mode by itself.
- **The row routes that write `values` 409 while a rewrite is live.** The table PATCHes a
  whole cached row, so an edit during the pass undid it.
- **The job rewrites only the scenarios the user reviewed**, stored in its config.

## 4. The stepping run (landed)

Built on 2026-11-04. Everything durable — the `Run` row, `advance_run` and the worker's
`_after_terminal` hook, cancel, pause, resume and restart — is in `docs/dev/comfyui/automation.md`; the stages
and the VRAM handoff are in `docs/dev/comfyui/run-stages.md`, and the Runs page in
`docs/dev/comfyui/run-start.md`. The start functions §4 needed
(`start_score_job`, `start_caption_job`, `start_export_job`, `fill_blueprint`) sit beside
their job closures in their routers, as `_start_run` already did.

The user settled three points before it was built (2026-11-04): the **gate** moves to §5,
with the loop; the **snapshot** stage moves to §6, with the recipe library; and a run's
settings come from a **compact form** on the Runs page, stored on the row as its recipe.

Departures from the sketch that stood here:
- **No separate dedupe stage.** Duplicate flagging is already the tail of a technical score
  (`_flag_duplicates`, dataset-wide), so the score stage always runs technical. `resolve`
  is never run.
- **Rows waiting for a rewrite pause the run** rather than starting a `comfy_rewrite` job:
  the job rewrites only the scenarios the user reviewed, and an unattended run cannot
  review them.
- **Every stage that does work is a job**, the VRAM handoff included (`run_handoff`), so
  `advance_run` itself only records and enqueues, and nothing slow runs inside the hook.
- **The hook also runs on the cancelled-while-queued path**, which skips `_finish`.
- **An LLM is unloaded after captioning, by a stage of its own** (`free_caption_model`, the
  user's request after the first hand runs, 2026-11-04). LM Studio's OpenAI-compatible API has
  no unload, but its native REST API (0.4 and later) does. The handoffs before generating
  still leave an LLM alone.
- **Export takes no id list**: it exports the dataset through its filters, duplicates left
  out by default, which keeps it clear of the `[]` trap below.

## 5. Goal loop, gates, budgets (landed)

Built on 2026-11-04. Everything durable — the body and the tail, `next_pass` and the
abandon rule, the rewrite stage, the Runs page's sections — is in
`docs/dev/comfyui/run-loop.md`; the gate and `rejects/` are in `docs/dev/comfyui/run-gate.md`,
and the budgets, the paid-call cap and the seed check in `docs/dev/comfyui/run-budgets.md`. The
kept rule and the kept-based yield are in `docs/dev/comfyui/structured.md` § Fill
accounting; the per-provider `billed` setting is in `docs/dev/llm-providers.md`.

The user settled five points that day: the rewrite pass is an optional stage inside the
loop; the LLM-call cap applies to paid providers only, through a per-provider "billed"
setting defaulted from `_is_remote`; the kept rule holds everywhere, Preview and the
manual Top up included; rejects go in one tree, `<base>/rejects/<leaf path>`; and §4's
three open hand checks are run before the loop is.

Departures from the sketch that stood here, most from an Opus review of the design:
- **The tag stays `"<index>:<key>"`.** A pass's entries are inserted before the tail, so a
  started stage never changes index, and the pass number in the tag was not needed.
- **Budgets are recomputed from the run's child jobs**, not stage entries: resume and the
  restart reconcile wipe a failed stage's numbers.
- **The image clamp sits in `prompts`**, not `generate`, so the rewrite is never paid for a
  row the clamp then drops; the rewrite also gets `max_requests` from the cap.
- **A rewrite inside a run completes when folders fail**, even every folder; only a
  provider or write error, or a cancel, fails it.
- **The seed check reads the workflow**, not just the pins: an unpinned seed repeats too.
- **`style_sim_min` is not a gate setting**: the score stage never writes that score.
- **Apply and Top up 409 while a run on the plan is live.**

## 6. Dry run, snapshot stage and the recipe library (landed)

Built on 2026-11-04. Everything durable is in the docs: the snapshot stage, the shared
start check and the dry run in `docs/dev/comfyui/run-start.md`;
the head stage in `docs/dev/comfyui/run-loop.md`; carrying on past a cap in
`docs/dev/comfyui/run-budgets.md`; the library, the document, the
form converters and the remembered form in `docs/dev/comfyui/recipes.md`.

The user settled four points that day:
- the dry run is its own button, and its report shows under the form;
- Start runs the same checks and refuses on the first error before writing a run;
  warnings never block;
- saved recipes keep this machine's provider ids and export folder, the JSON export
  strips both (providers by name), and an import resolves providers by name or blanks
  them with a note;
- three extras are in: **Use these settings** on a past run, raising a cap to carry a run
  on (the second "Still open" point below), and remembering the start form.

Departures from the sketch that stood here:
- **The dry run counts with `blueprint_service.fill_plan`**, which is `_compute` on the
  stored blueprint, and not Preview, which lists at most 500 leaves and counts rewrite
  calls the run would not make. It shares `round_robin_counts` with the image cap's clamp.
- **A first pass over the row cap is now a start refusal**, not a run that fails at its
  first stage.
- **The snapshot is a head stage** with no pass number, since a body stage is copied into
  every pass. Its cancel waits through `Run.cancel_requested` (migration `a6d2f8c4e0b3`).
  `start_snapshot_job` was lifted out of the snapshot route, which had a closure. A resume
  never takes a second snapshot once one is done: a killed snapshot commits nothing.
- **Stored recipes are `RunRecipeDraft`s**, whose machine-specific fields may be empty, so
  an import saves and is finished in the form. The start validates the strict recipe.
- **The model check lists the provider's models through `_LLM_TRANSPORT`**, and an autouse
  fixture now refuses every test's LLM-server call, because the tests' providers sit on LM
  Studio's real port.
- **A carried-on run's new tail captions only its new passes** (`_tail_rows`), and the
  rewrite's caption reserve counts captions already made.

## 8. The ComfyUI page rework (landed)

Scoped with the user on 2026-11-05, who asked for it to land before the merge. An
interactive mockup of both screens, which the user approved as the direction, is the
private artifact https://claude.ai/artifact/5Tukrf24L39JBw3YFmgaP1 (the Rows tab and the
Blueprint tab, linked by their tabs). It is a sketch of the layout and the gestures, not a
spec: its data is invented and it saves nothing. Read `docs/dev/comfyui/page.md` first;
every component named below is described there.

**What the user found wrong:**
- **Rows are one flat list.** Each row already has a destination folder (`subfolder`), but
  it shows only as a column, and nothing groups by it. The prompt library's categories are
  flat too.
- **Moving prompts between plans is buried** in `PromptLibraryModal`'s *Other plans* tab
  (dataset → plan → tick → Copy/Move), and a copy carries the prompt text only, dropping the
  row's folder.
- **The Blueprint tab scrolls away from its controls.** `ComfyPage` is one scroll
  container, so the plan bar, the tabs and the run bar leave the screen, and Apply and Top
  up sit below every editor.
- **The Rows toolbar** shows eleven buttons in one line whether or not a row is selected.

### §8a — A layout that stays put

**Landed 2026-11-05** (`docs/dev/comfyui/page.md` § The layout, and the Blueprint tab's half in `docs/dev/comfyui/blueprint-tab.md` § The layout). The narrow preview table
shows a folder's sample prompts on demand, not as a column: the user's call that day.

- **A sticky header** on every tab: the plan name, the tabs and `ComfyRunBar`. The run bar
  then shows on Blueprint too, where Top up already uses its Base folder and Prompt as
  caption read-only.
- **Blueprint in two columns.** The editors scroll on the left; `BlueprintPreview` sits on
  the right, sticky with its own scroll, with Apply, Top up and Rewrite at its top. On a
  narrow window it stacks under the editors and the action buttons pin to the bottom.
- **Collapsible editor sections** (Axes, Variations, Scenarios), each with a count, jump
  links and collapse/expand all. The open state is saved per plan.

No backend change. It needs none of the open questions below.

### §8b — A left rail: plans, then the plan's folder tree

**Built 2026-11-05** (`docs/dev/comfyui/page.md` § The plan rail, the folder tree and the
grouped table), under the rulings below. One departure from the mockup: a blueprint folder
cannot be renamed, moved or deleted from the tree (question 2's ruling).

- **Plans become a list** instead of the `<select>`, each with its row count and a dot for
  running or failed. Rename, Duplicate and Delete in a menu. **Duplicate is new**: the
  workflow, the pins and optionally the rows.
- **Under it, the selected plan's folder tree**, built from the rows' `subfolder` values
  plus the plan's empty folders. Click filters the table; a menu creates, renames, moves and
  deletes. The gallery's sidebar is the model (`docs/dev/gallery/subfolders.md`).
- **The rows table groups by folder** under collapsible headers with pending, failed and
  done counts. A text filter and status chips sit above it, and completed rows get a small
  thumbnail (the row already links its image).
- **The last plan and tab are remembered** per dataset; today they reset on leaving the
  page.

Backend: a rename/move of a folder path across a plan's rows (the shape of the gallery's
`repath_subfolder`, on `comfy_rows`), a per-plan list of empty folders (one JSON column on
`comfy_plans`, so one migration) and a plan duplicate endpoint.

### §8c — A library side panel, and drag and drop

**Built 2026-11-05** (`docs/dev/comfyui/library-dnd.md`; the endpoints in
`docs/dev/comfyui/queue.md` § Backend), under three rulings by the user that day: reorder
goes across folders too, categories get a menu *and* drag, and library prompts dropped on the
table land in the folder in view. Three departures from the text below, all deliberate:
- **A copy carries only the target's per-row values**, not "every value whose alias exists".
  `patch_workflow` applies any alias in `values`, so a value carried under a run-default pin
  would silently override it, with no column to show it.
- **`PATCH` on a library prompt edits text only.** Re-filing stays on `library/move`, so there
  is one set of merge rules.
- **A drop between rows uses `rows/place`, not `rows/reorder`.** A drop can also change the
  row's folder, which `rows/reorder` cannot do, so one call places and re-files.

It also fixed what it found: a live `comfy_generate` run now locks its plan's folders and rows
on the server. `rows/delete` had no guard, while the run commits `status` on the rows it loaded
at start, and the folder lock was the page's alone. An Opus review found no backend bug; its
five frontend findings were fixed, each pinned by a test where e2e can reach it.

- **The library becomes a toggled panel** on the right of the Rows tab instead of a modal.
  Categories nest through `/` paths shown as a tree, with search and **editing a saved
  prompt's text** (no endpoint does that today). *Other plans* moves into the panel as a
  browse-and-drag view.
- **Every drag also has a button or menu route** doing the same thing, as in the gallery:
  it keeps the page usable without a mouse, and e2e has no drag idiom
  (`docs/dev/gallery/drag-drop.md` § Dragging a subfolder onto another).

| Drag | Onto | Does |
|---|---|---|
| Rows (the whole selection if the row is selected) | a folder in the tree or a group header | set their folder |
| Rows | another plan in the rail | copy; Shift moves |
| Rows | a library category | save to the library |
| Library prompts | the table or a folder | add as rows there |
| Library prompts | another category | re-file them |
| A folder | another folder, or *(root)* | re-nest it with its rows |
| Rows | between rows | reorder (`rows/reorder` exists) |
| `.txt` files from the desktop | the table | import, one prompt per file |
| A workflow `.json` from the desktop | *Workflow & Pins* | load it |

Backend: a copy/move endpoint for rows between plans that **keeps the folder** and every
value whose alias exists in the target plan; `PATCH` for a library prompt's text; and
`ComfyLibraryPrompt.category` may need widening past 100 characters for nested paths.

### §8d — Toolbar cleanup

**Built 2026-11-05** (`docs/dev/comfyui/page.md`, the Rows toolbar and
`ComfyRowsSelectionBar`), under three rulings by the user that day, each a departure from the
mockup:
- **The selection bar keeps a fixed slot**, a hint when nothing is ticked, so the table never
  moves under the pointer on the first tick. The mockup showed it only with a selection.
- **Its actions are buttons opening the existing dialogs**, not the mockup's drop-downs, which
  would have lost Set folder's blueprint ask, Copy's Move and a new library category.
- **Run selected lives in the selection bar only**; the sticky run bar keeps Run all and Run
  pending.

The menu keeps today's labels rather than the mockup's longer ones. The filters moved up
into the toolbar's line, which pays for the slot. *Reset failed* stays in the toolbar, as it
is not a selection action. The menu is a new shared `MenuButton`.

- One **Add ▾** menu: Add row, Paste prompts, Import .txt, Generate, Edit prompts.
- Actions on selected rows move to a selection bar in a fixed slot (a hint while nothing is
  ticked): move to folder, copy to plan, save to library, run, delete.
- The paste dialog moves onto `ModalShell`; today it is a hand-built overlay with no Escape
  or focus handling.

### The open questions, ruled

All four were ruled by the user on 2026-11-05, approving the §8b plan's recommendations.

1. **A row's category is its output folder.** The tree is `subfolder`, so it means what the
   gallery's tree means. A separate grouping label was rejected: two trees that disagree.
2. **Moving a blueprint row asks, then unlinks it; a blueprint folder is locked.**
   - *The premise needed a fix.* A leaf counts its rows by `leaf_id`, not by folder
     (`blueprint_service._compute`, `prepare_top_up`). So a moved row kept counting toward
     its leaf while its images went elsewhere, and Top up re-ran it into the new folder.
     Clearing `leaf_id` is the only move that keeps the figures honest.
   - "Linked" means `leaf_id` names a *current* leaf. Orphans keep their `leaf_id` as
     provenance and move freely.
   - Set folder offers *Move and unlink*, *Move hand-made rows only* and *Cancel*; the
     server refuses a linked move by default (`linked: "refuse"`, a 409).
   - A folder whose subtree holds linked rows cannot be renamed, moved or deleted from the
     tree (409, "rename it in the Blueprint tab"). The mockup unlinked them instead, but
     that leaves the leaf's path in place, so the next Apply would quietly queue a full
     target into the old folder name.
3. **Rows dropped on another plan: copy by default, Shift to move.** A copy keeps the
   folder, resets the row to pending and clears `leaf_id`; a move also refuses running
   rows. Built in §8c.
4. **Build order: §8a → §8b → §8c → §8d** (the roadmap holds the order).

Each sub-stage owes `docs/dev/comfyui/page.md` and the user guide (the Rows tab's half is
`docs/comfy-queue.md` since the 2026-11-05 split, the rest `docs/comfyui.md`) in the
same change, and e2e coverage through the button routes in `frontend/e2e/comfy.spec.ts`.

### Review fixes, 2026-11-05

A review of all of §8 (three Opus reviewers and a doc audit) found no high-severity bug.
It fixed what it found, under four rulings by the user that day:
1. **One lock for deletes.** `DELETE /comfy/plans/{id}` and `rows/delete` refuse whatever
   folder delete refuses: a live generate, prompt or rewrite job, or a running or paused
   automated Run. Plan delete had no guard at all. The page disables both Deletes then.
2. **A live Run's plan takes no new rows.** `rows/copy`, `rows/bulk` and `POST …/rows` refuse
   a target whose automated Run is running or paused, as Apply does. This reverses §8c's "a copy has no
   guard on the target".
3. **The selection is pruned to the rows on show**, whatever hid them, not only on a filter
   change.
4. **One app-wide guard** stops a desktop file dropped outside a drop zone from opening in
   the tab (`FileDropGuard`).

Also fixed:
- A folder or category move that would write a path over 512 characters is refused.
- A category delete under a search now quotes the whole subtree's count.
- Drops that change nothing are refused, and so are drop targets covered by the sticky header.
- `ContextMenu` gained keyboard focus.
- The copy response names its two skip reasons.
- The rail's counts refresh after a cell edit, *Add row* and *Reset failed*.
- The Library tab got back *Select all*, and the Other plans tab got back each row's status.

Where each lives: `docs/dev/comfyui/queue.md` § Backend, `docs/dev/comfyui/page.md`,
`docs/dev/comfyui/library-dnd.md`, `docs/dev/styling.md` and `docs/dev/frontend-core.md`.

## Landed stages

Moved here word for word from the roadmap's § M6 checklist on 2026-11-05, when the roadmap
went over budget writing every stage twice. The roadmap now ticks each stage in one line;
what a stage delivered, and what was checked by hand, is recorded here. Hand checks still
owed are the one list in the roadmap's § Next session.

- [x] Port per-row output folders from `comfy-per-row-subfolders`: c2d6a6d. Ported 2026-11-03 as
  8dee5de, with the migration re-parented onto `c2b8e4f6a1d7` and the plan moved into its own
  folder and rewritten. The old branch is deleted.
- [x] Split `docs/dev/comfyui.md` along its two recorded seams. Done 2026-11-03: the run body
  moved to `docs/dev/comfyui/run.md` and the page to `docs/dev/comfyui/page.md`, each with a Map row.
- [x] §7: the mock ComfyUI server. Done 2026-11-03: `backend/tests/fake_comfy.py`, in-process
  for pytest (`test_comfy_run_body.py`) and on port 8198 for Playwright (`comfy-run.spec.ts`).
  Every later stage tests against it.
- [x] §2a: blueprints, leaves, the pure expansion service and its guard rails, and prompts
  composed from templates. Done 2026-11-03: migration `b7d2f4a9c1e3` (which also carries §2b's
  `brief`/`draft` columns), the *Blueprint* tab, and `docs/dev/comfyui/structured.md`.
- [x] §2b: the outline call and the review panel, reachable without an LLM. Done 2026-11-03:
  the `comfy_outline` job saves a draft for *Use it* / *Discard*.
- [x] §2c: Top up. The plan's cut point if the arc stops early. Done 2026-11-03: Apply, reset
  failed rows, run pending ones, in one press.
- [x] Split comfy-structured.md before §3 grows it. Done 2026-11-03, re-cut three ways, with
  the ComfyUI family moved behind the hub `docs/dev/comfyui.md`.
- [x] §3: the optional LLM rewrite pass, and the outline's instruction-order fix. Done
  2026-11-03: the `comfy_rewrite` job, migration `d4a8c2e6f1b9` (`comfy_rows.prompt_origin`),
  Top up's refusal while rows wait, and `docs/dev/comfyui/structured-llm.md` § The rewrite
  pass. Verified by hand against a real LLM in part (2026-11-04, `qwen3.8-27b` in LM
  Studio): a draft and a rewrite both worked, and the draft review, the label wording and
  the rewrite count's refresh came out of that test.
- [x] Rebase onto `main` once M8 stages 2 and 3 have landed there. Needed by §4. Done
  2026-11-04, after PR #109.
- [x] §4: the stepping run (`Run` row, `advance_run()`), its page and the VRAM handoff. Done
  2026-11-04: start functions lifted out of the score, caption and export routes and Top
  up, migration `f3b9d1a7c5e2` (`runs`), `run_service.py`, the worker's `_after_terminal`
  hook, the Runs page and `docs/runs.md`. The user moved the gate to §5 and the snapshot
  stage to §6 that day. Full runs checked by hand against a real ComfyUI and LM Studio the
  same day: every stage worked, `/free` really freed ComfyUI's VRAM, and the scorers
  unloaded after scoring (measured with the Windows per-process GPU counters).
- [x] §5: the goal loop, the gate and budgets. Done 2026-11-04: the body/tail split and
  `next_pass`, the gate (`run_gate.py`, `rejects/`, `gate_rejected`), the rewrite stage,
  the caps and the paid-call cap, the per-provider `billed` setting (migration
  `c8e4a6b2d0f7`), the kept rule applied everywhere, and `docs/dev/comfyui/run-loop.md`.
  An Opus review's twelve findings were fixed.
- [x] §6: the dry run, the snapshot stage and the recipe library. Done 2026-11-04 with
  every gate green: `plan_run` behind both the dry run and the
  start, the head `snapshot` stage and its waiting cancel (migration `a6d2f8c4e0b3`), the
  `run_recipes` library (`b2e7c9a4d1f6`) and the remembered form (`c4a8e2f6b9d3`),
  `extend_run`, and `docs/dev/comfyui/recipes.md`. The user's three extras came with it: Use
  these settings, raising a cap to carry a run on, and the remembered start form.
- [x] Review-fix pass. Done 2026-11-04: a branch review (four code reviewers and two doc
  audits) and its fixes, each with a regression test. Two rulings by the user: a run fails
  when a generate or caption job failed every item (`docs/dev/comfyui/automation.md`), and
  `gate_rejected` is cleared when an image leaves `rejects/` (`services/gate_flags.py`,
  `docs/dev/comfyui/run-gate.md` § The gate). The rest: the gate honours a cancel and needs
  no plan, a second Cancel finishes a stuck snapshot wait, a stale job cannot step a resumed
  stage, an empty pass stops the loop, `kept` counts completed rows only, Top up's error
  after a committed fill says so, id lists in the score and caption starts are chunked,
  recipe import refuses an ambiguous provider, and blueprint and export inputs are bounded.
  The M6 docs were audited against the final code.

§8:

- [x] §8a: the sticky header and run bar, Blueprint in two columns with the preview
  sticky, collapsible editor sections. Done 2026-11-05, frontend only: the page is its own
  bounded scroller, a container query stacks the columns in a narrow pane, and the
  preview's sample prompts open per folder (the user's call). The wide and narrow layouts
  were checked by screenshot. See `docs/dev/comfyui/page.md` § The layout and
  `docs/dev/comfyui/blueprint-tab.md` § The layout.
- [x] §8b: the plan rail, the folder tree and rows grouped by folder, plan duplicate,
  the remembered plan and tab. Done 2026-11-05: migration `d5b7f9a1c3e6`
  (`comfy_plans.declared_folders`), the folder and duplicate endpoints, the `linked` mode
  on folder moves, and `docs/dev/comfyui/page.md` § The plan rail, the folder tree and the
  grouped table. `page.md` was split first, as queued: the Blueprint tab is now
  `docs/dev/comfyui/blueprint-tab.md`. The user ruled the four open questions by approving
  the recommendations, and an Opus review's findings were fixed, among them a case-blind
  `LIKE` that let a folder delete take another folder's rows.
- [x] §8c: the library side panel, Copy to plan, the row menu and drag and drop. Done
  2026-11-05; what it delivered is § 8c above.
- [x] §8d: the *Add ▾* menu, the selection bar in a fixed slot, Run selected moved into it,
  and the paste dialog on `ModalShell`. Done 2026-11-05, frontend only: `MenuButton` (with
  `ContextMenuItem` extracted from `ContextMenu`), `ComfyRowsSelectionBar`, and e2e for the
  menu's disabled reasons, the dialog's Escape and draft, the slot holding the table still,
  and Run selected against the fake ComfyUI.

## Documentation this arc owes

`docs/dev/comfyui.md` went over budget at ~4,440 words after §1 and was split on 2026-11-03:
the run body is now `docs/dev/comfyui/run.md` and the page `docs/dev/comfyui/page.md`. Later stages
that edit the run body document it in `docs/dev/comfyui/run.md`. Since 2026-11-03 the
family sits behind the hub `docs/dev/comfyui.md`, so each new file goes in
`docs/dev/comfyui/` with a row in the hub's tables, not a Map row:

- Structured generation, written and then split three ways on 2026-11-03:
  `docs/dev/comfyui/structured.md` (blueprints, fill accounting, orphans, Apply, Top up),
  `docs/dev/comfyui/structured-expansion.md` (the spec, expansion and its guard rails) and
  `docs/dev/comfyui/structured-llm.md` (the outline call, and §3's rewrite pass since
  2026-11-03). §1's two columns stay documented beside the row model;
  cross-reference them rather than moving them.
- `docs/dev/comfyui/automation.md`, written with §4 (2026-11-04), and
  `docs/dev/comfyui/run-loop.md`, written with §5 (2026-11-04). §6 extended both and added
  `docs/dev/comfyui/recipes.md` (2026-11-04). The 2026-11-04 splits moved the stages to
  `docs/dev/comfyui/run-stages.md`, the start check and the Runs page to
  `docs/dev/comfyui/run-start.md`, the gate to `docs/dev/comfyui/run-gate.md` and the budgets
  to `docs/dev/comfyui/run-budgets.md`.

`docs/dev/backend-infrastructure.md` § After a job ends: the run hook, written with §4. User docs are owed in the same change as each feature — `docs/comfyui.md`
or a new page, plus a row in `docs/features.md` and whatever `README.md` needs. Run
`python scripts/check_docs.py` and `python scripts/check_migrations.py` on every stage.

## Still open

- Whether an entity filter (built on `comfy_leaves`, scoped to a blueprint) is wanted in the
  gallery. Not built until someone misses it (decision 8).
- ~~Whether a run stopped by a cap can have the cap raised and be resumed.~~ Settled by the
  user on 2026-11-04: it can (§6, `extend_run`).

Settled 2026-11-03 by the user: per-leaf targets are edited per scenario in the UI and stored
per leaf in the DB; a deleted leaf's rows count toward nothing (see §2c). Also settled that day:
§3 rewrites one leaf at a time, with no concurrency setting.

## Traps worth restating

- **`declare_subfolders` commits.** Called inside the import loop it commits a half-written row
  and breaks the per-row rollback. §1 hoists it above the loop; §2c and §4 are the next places
  that could reintroduce it.
- **`advance_run()` raising into the worker** (§4). It runs after the job's commit; an exception
  there must be logged and swallowed, or a finished job looks failed.
- **An accumulated counter** (§2c, §5). Shortfall and "kept" are recomputed from the DB, because
  dedupe deletes rows and stale scores stop counting.
- **Checking a budget after the stage it was meant to prevent** (§5).
- **A silently wrong path segment from a slug fallback** (§2a). `slugify_filename` answers
  `"image"` for an all-punctuation label.
- **An LLM asked to enumerate a cross product** (§2a). It returns most of it, and describes the
  same entity differently each time.

- **An empty id list that widens to the whole dataset** (§4). `score_quality`, style
  similarity, `run_captioning`, `run_pipeline` and the exports test `if body.image_ids:`, so a
  stage that passes `[]` scores or captions every image in the dataset. A stage with no ids is
  skipped, never sent `[]` (the cleanup plan's § Found during stages 2 and 3 records it).

Every one of these fails quietly. None produces an exception or a visibly wrong number.
