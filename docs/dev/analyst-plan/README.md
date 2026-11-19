# M12 — Dataset analyst

**Status: transient.** The design plan for issue #129, "Chat with an LLM". Its place in the
order is in [../roadmap.md](../roadmap.md); the rulings behind it are
[decision 21](../decisions/21-dataset-analyst.md). When a phase lands, move its durable
rationale into the analyst docs family (§ Docs) and delete the phase from here. Delete the
folder when every phase is done or dropped.

## What it is

An LLM that acts as an **analyst** for one dataset. Crucible works out the facts itself —
a health report of findings, the dataset's goal, the user's notes — and hands them to the
LLM, which gives feedback, ranks what matters for the user's goal, and suggests actions.
Performing actions comes last, behind confirmation.

The split is deliberate. **Crucible finds the problems; the LLM explains them.** Findings
are deterministic and testable, and they are useful with no LLM at all. The LLM never has to
discover anything on its own, so a small local model with weak tool calling — the main risk
the roadmap named — can still do the job: reading a short list of findings and talking
about it needs no tool calls, and fits a small context window.

## Branch strategy

**Nothing reaches `main` until the whole feature is done**, these docs included (the user's
ruling, decision 21). Everything lives on `feat/analyst`:

- Each phase is its own branch off `feat/analyst` and goes back in as a reviewed PR, with an
  Opus review before each, as #130 did.
- Merge `main` into `feat/analyst` regularly rather than one rebase at the end, and run
  `python scripts/check_migrations.py` after each merge: Alembic heads can fork even when
  no file conflicts.
- Run the branch's migrations only on throwaway databases, never the dev DB (a migration
  the dev DB has applied strands it when `main` is checked out again).
- One final PR takes `feat/analyst` to `main`, and only then is anything released.
- Phase 0, the test harness, landed: the fake LLM server and `openai` in
  `requirements-ci.txt`, both in `docs/dev/backend-tests.md` § The fake LLM server. Every
  analyst call should go through `openai_compat_captioner.make_client`, which the
  `fake_llm` fixture patches.
- Phase 1, the health report with no LLM, landed in three PRs: the backend (#179), the Health
  page, goal fields and card badge (#180), and the gallery `?filter=` deep link (#181). Its
  record is the analyst docs family, `docs/dev/analyst.md`, and the user doc `docs/analyst.md`.
- Phase 2, the chat without tools, landed in six PRs (#183 to #188): the tables, privacy and
  `build_context`; sending; the chat page; note proposals; "Open beside" with the saved pane
  layout; and the model trials. Its record is `docs/dev/analyst/chat.md`, `sending.md`,
  `chat-ui.md`, `proposals.md` and `trials.md`.

## Phase 3 — Read-only tools

For models that handle tool calls; the rest keep phase 2's behaviour.

- A per-provider probe — one scripted tool-call round trip — stored as `tools_ok`. False, or
  never probed, means phase 2.
- A tool registry whose handlers return `ContextItem(category, text)`; only the privacy
  filter turns those into strings, so a tool cannot return captions while captions are off.
- Tools: the findings, captions sampled by a `FilterSpec`, tag stats, the snapshot list, a
  comparison against a snapshot.
- Limits on tool rounds per turn and on result size; every tool round counts toward the
  budget; calls and results are stored as `role=tool` rows so a transcript replays.

## Phase 4 — Actions behind confirmation

- Actions are a typed enum whose parameters are finding ids or `FilterSpec`s. The model never
  supplies a path, a raw filter string or SQL.
- A proposed action is a pending row shown as a confirm card with a preview; confirming calls
  the same services the UI's own routes use, never a parallel path.
- **Every confirmed action takes a snapshot first** through
  `version_service.create_snapshot`, so a bad action is one restore away. When versioning is
  off the action is refused with that reason, and `dataset_busy.ensure_not_busy` applies as
  it does to the UI's routes.
- An idempotency key per action, so a double click does not run it twice.

## Phase 5 — Caption–image agreement (measure first)

Whether a caption describes its image. Measured on one of the owner's real datasets before
anything is built in, the way M7 measured style models (decision 6):

- **Tag captions:** run the WD14 tagger Crucible already has and compare its tags with the
  caption's. Missing or contradicted tags are a direct, readable result.
- **Prose captions:** Long-CLIP (248 tokens, built from CLIP ViT-L/14) and FG-CLIP 2 (196
  tokens, on SigLIP 2). Neither licence was confirmed on 2026-11-17.
- **Slow, high accuracy:** a VQAScore run — ask a local vision model "does this image show:
  {caption}?" and read the probability of "yes". Cloud APIs rarely return that probability.
- Ruled out: jina-clip-v2, which is CC BY-NC 4.0 (non-commercial), since Crucible has other
  users.

The useful output is outliers within a dataset ("these 15 captions match their images
worst"), not pass or fail. What measures well becomes an optional job and one more finding.

## Docs

Each phase PR carries its own docs, in one family from the start, since CLAUDE.md's Map has
room for only a few rows:

- A hub, `analyst.md` in `docs/dev/`, with one Map row, and detail files in a `docs/dev/analyst/`
  folder registered in `SPLIT_INDEXES`.
- A CLAUDE.md key invariant for the one rule a later contributor must not break: everything
  the LLM sees goes through `build_context` and its privacy filter.
- User docs: `analyst.md` in `docs/`, a README Docs-table row, and the Settings tab in
  `docs/settings.md`, growing with each phase.

## Open questions

- Whether the pane system is enough for "visible at all times", or an overlay is needed.
  Panes are the first answer ("Open beside", `docs/dev/analyst/chat-ui.md`); use will show
  whether an overlay is still wanted.
- Which local models are good enough. Partly answered: Gemini 2.5 Flash and Qwen 3.8 27B
  pass the trials (`docs/dev/analyst/trials.md`); smaller local models are untested.
- Whether the system prompt should list Crucible's pages: in the trials Gemini named tools
  that do not exist. A small prompt change, before or with phase 3.
- Whether the system prompt becomes user-editable through the app-stored prompts (M5).
- Chats spanning several datasets ("compare these two"). One dataset per chat until asked
  for.
