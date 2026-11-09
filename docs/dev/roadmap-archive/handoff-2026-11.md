# Handoff notes, 2 to 9 November 2026

**Status: archive (landed 2026-11-09).** *Record for the handoff line in the
[roadmap](../roadmap.md#landed).*

These are the closed items of the roadmap's "Next session" block, moved here on 2026-11-09
word for word, with links repointed to this folder. The only new words are three notes
saying where something went: the PM-035 follow-up, M10's place before M7, and M6's loose
ends. Nothing here is open. The items were moved so the block could hold what comes next. Open work is in the [roadmap](../roadmap.md); M6's
own closed loose ends went to
[m6-comfy-automation.md](m6-comfy-automation.md#loose-ends-closed-after-the-merge).

## Merged on 2026-11-09

- **GPU work on one thread (PM-035):** merged on 2026-11-09 as PR #131; the branch is deleted. Every
  load and inference call now goes through `device.run_on_gpu`, guarded in CI. Before it,
  a long scoring run left ~3 GB of VRAM on the card after every model had unloaded
  ([ml-models.md](../ml-models.md) § One GPU thread). Production on both machines needs a pull and a
  restart to drop what the old process holds. Its follow-up (a first-run model download
  inside a loader holds up every other GPU call) is now a stage of M10 in the roadmap.
- **Copy from plan (2026-11-09):** pushed straight to `main` at the user's request, no PR.
  The Workflow & Pins and Blueprint tabs each copy from another plan in any dataset,
  through one dialog and the existing GET/PATCH/PUT routes ([comfyui/page.md](../comfyui/page.md)
  § Frontend, `CopyFromPlanModal`). Nothing open.
- **Instruction presets for Generate prompts (2026-11-09):** merged as PR #132; the branch is
  deleted. Named saves of the modal's *Instructions* text, in their own table
  (`prompt_gen_presets`, migration `c3e7a9d1f5b2`), apart from the caption presets
  ([comfyui/prompts.md](../comfyui/prompts.md) § Frontend). It moved the migration head, so any
  branch holding a migration re-parents onto `c3e7a9d1f5b2`. Nothing open.

## M6's loose ends, M8's "any time" items and M7 stage 2b

**M6's loose ends are all closed.** On 2026-11-08 the Ctrl+C hand check
passed, the folder-segment whitespace fix merged as PR #121 (decision 12, migration
`b8d2f4a6c1e3`), and the M6 plan folder was retired by PR #122, a docs-only PR. **After
M9, M7 is next**: the CLIP QuickGELU plan or Stage 3, neither of which
waits on anything. (Since 2026-11-09, M10 comes between M9 and M7:
[decision 14](../decisions/14-job-throughput-is-its-own-milestone.md).)

**M8's two "any time" items** (the coverage config and CLAUDE.md § Data flow) merged on
2026-11-08 as PR #120; the branch is deleted. `test_restart_in_dev_mode_touches_the_reloader_trigger_and_never_signals`
failed once in PR #120's CI and passed on a rerun: it reads the trigger file as soon as it
exists, which can be before `write_text` has filled it. Fixed the same day on `main`: it
now polls for the text. **M7 Stage 2b** (the layer labels) merged on
2026-11-08 as PR #119; the branch is deleted. What is left in M7 that needs nothing else:
the CLIP QuickGELU plan and Stage 3.

## The M7 port

**The M7 port merged on 2026-11-03 as PR #108**: layer 7 at 0.30/0.70 is the style
default, each run records its blend weights, and the layer picker warns before a run that
must fail. The user had both `style-embedding-measurement` and `feat/m7-style-port`
deleted afterwards, and `exp/dinov3-test` and `exp/style-models` too: all three research
runs live on `exp/style-media`, which contains the other two and is the record the docs
cite. Its plan is
now [style-embedding-plan/README.md](../style-embedding-plan/README.md), and the research
folder's findings moved into `docs/dev/quality/style-evaluation.md`. What is left in M7 is
in the roadmap.

## M6's merge

**M6 merged on 2026-11-07 as PR #110**, and `feat/m6-comfy-automation` is deleted. Its
checklist and the last session's notes are archived in
[m6-comfy-automation.md](m6-comfy-automation.md). What stayed open was all closed by
2026-11-08, and is listed in that record.

## M8's follow-ups

M8's follow-ups 8–11 merged on 2026-11-07 (PRs #114 and #115); both branches are deleted.
On 2026-11-08 follow-up 14 (a copy-mode thumbnail guard) merged as PR #116 and stage 4
(the `DatasetsPage` dialogs) as PR #117; both branches are deleted. Follow-up 15, `images.py`'s two
single-image copies, waits for M4. Batch move and copy, the rest of item 9, must wait for
M4 to merge. Items 7, 12 and 13 wait for stage 6, which replaces that code. M8 stages 5 and
6 now wait only for M4, since M7 has merged. What stages 2 and 3 found and left alone is in
[cleanup-plan/README.md](../cleanup-plan/README.md) § Found during stages 2 and 3, left as
they were.
