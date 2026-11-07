# ComfyUI — the run gate

The gate is the check a run puts after scoring, before any next pass (`BODY_STAGES`,
`docs/dev/comfyui/run-loop.md`): it tests each new image against the bar the recipe sets and
files the failures under `rejects/`. This file covers `services/run_gate.py` and its
`run_gate` job, `services/gate_flags.py` and the flag's clearing, and the gate's section on
the Runs page. How the loop counts kept images and `next_pass` are
`docs/dev/comfyui/run-loop.md`; the stage's place among the others is
`docs/dev/comfyui/run-stages.md`; the caps are `docs/dev/comfyui/run-budgets.md`. The user
guide is `docs/runs.md` § The gate.

## The gate (`services/run_gate.py`, job type `run_gate`)

`gate_reasons(img, gate)` is pure:
- a truthy value of any flag in `exclude_flags` fails;
- an `aesthetic_score` below `aesthetic_min` fails, and so does a NULL one;
- `scores_stale` always fails: the pixels changed since they were scored.

`GateSettings.exclude_flags` may only name `GATE_FLAGS`, the flags a run's score stage can
set. `has_ai_artifacts` is set by nothing a run does, and the score stage never writes
`style_similarity_score`, so neither is offered. The recipe also refuses a gate without
the score stage, and an aesthetic minimum without aesthetic scoring.

`apply_gate` judges the images of the pass's completed rows, skipping any already
rejected. For each failure it copies `quality_flags`, sets `gate_rejected: true` and
`gate_reasons` (a payload key, not allowlisted, like `duplicate_of`), reassigns the dict,
and files the image under `join_subfolder(base, "rejects", row.subfolder)`. That is one
tree for every reject (the user's ruling, 2026-11-04), with the leaf's path kept. The
move goes through `services/image_move.move_to_subfolder(..., rename_on_move=False,
commit=False)`. Subfolders are DB labels, so no file moves, and the flags, the moves and
the job's numbers commit once.

**The flag, not the folder, is what keeps a reject out** of its leaf's kept count, the
caption tail and the export. **It goes when the image leaves `rejects/`** (the user's
ruling, 2026-11-04), so an image moved back by hand counts again.
`services/gate_flags.py` holds `REJECTS_FOLDER` (`run_gate` re-exports it) and
`clear_gate_flags_outside_rejects(db, dataset_id)`. The helper copies and reassigns
`quality_flags` without `gate_rejected` and `gate_reasons` for every flagged image of the
dataset whose subfolder has no `rejects` segment, and it does not commit. Every writer of
`Image.subfolder` calls it after the write: `image_move.move_to_subfolder` (both
branches; the rename branch does it as its own step after the loop, so nothing fallible
sits between a rename and its commit, PM-013), `dataset_service.repath_subfolder` and
`delete_subfolder`, and `images.py`'s `batch_move_dataset` and `batch_copy_dataset` for
the destination dataset. **The gate itself passes `clear_gate_flags=False`**: it flags
every reject before its first move, and the later targets' flags would otherwise be
cleared while still outside `rejects/`. Version restore deliberately does not clear:
it restores flags and subfolder together. `test_gate_flag_clearing.py` has a test per
writer. A new writer of `Image.subfolder` belongs in that list.

**The gate job honours a cancel.** `_stage_gate`'s job checks `raise_if_cancelled` before
the work and again before its one commit (rolling back first), so a cancel commits nothing.
**The stage needs no plan**, unlike the ComfyUI stages (`prompts`, `generate`, `handoff_to_local`) and `rewrite` and `next_pass`, which refuse a deleted plan.

**A known edge:** the duplicate scan does not know about the gate. A group's root can be
a rejected image, which leaves that group with nothing kept. That is accepted.

## The Runs page's Gate section

The Gate section of `components/runs/RunLoopFields.tsx` (the other sections are
`docs/dev/comfyui/run-loop.md` § The Runs page) holds the `GATE_FLAGS` checkboxes (from `FLAG_OPTIONS`) and the aesthetic minimum,
disabled while aesthetic scoring is off.

The gate's `run_gate` job is in `TopBar`'s `IMAGE_MODIFYING_JOB_TYPES` and the Stats
page's live set, so the gallery and the `gate_rejected` count refresh when it ends.
