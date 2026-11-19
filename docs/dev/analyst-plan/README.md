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
  budget; calls and results are stored so a transcript replays.

Rulings of 2026-11-19 (the user's, recorded as a decision in 3f):

- **Tool calls get their own table, `chat_tool_calls`, hung off the reply row**, not
  `role=tool` rows in `chat_messages`. The reply row is stored before the call, so tool rows
  between it and the user's would move it mid-turn and break resume, the "since your last
  reply" delta and `message_count`. One reply row per turn stays true.
- **A later turn does not resend earlier tool results.** History carries the reply text, as
  in phase 2; the model calls a tool again if it needs the data.
- **`_apply_image_filters` stays in `routers/images.py`.** `feat/m4-rating-port` edits its
  body, so a move now would clash; the captions tool imports it lazily, and M8 stage 8 (split
  `images.py`) gives it a service home.

The PRs, each a branch off `feat/analyst` with an Opus review:

- **3a**, the system prompt names the fix pages from `health_service.FIX_PAGES`. Merged as
  PR #189 (record: `docs/dev/analyst/chat.md` § The system prompt).
- **3b**, the stream and the probe. `stream_chat` takes `tools` and assembles streamed
  `tool_calls` by index (a missing id is made up); a tool-call delta ends the first-token
  limit. A per-model probe, `openai_providers.tools_probe` (`{model: {ok, checked_at,
  detail}}`, cleared where a `base_url` or billed change clears chat privacy), set by
  `POST /providers/{id}/probe-tools`: one round trip through a `crucible_echo` tool, under the
  provider slot, its usage recorded and the daily budget applied. The fake LLM answers a
  `tool` message with text and calls `crucible_echo` itself, since e2e cannot script replies.
- **3c**, on `feat/analyst-p3c-tools` (record: `docs/dev/analyst/tool-registry.md`):
  `ContextItem(category, text)`, `analyst_context.tool_specs(privacy)` (a tool is
  offered only when its categories are on) and `render_tool_result` (the filter, `_clean`,
  `<dataset-data>`, a size cap), and the registry in a new module, analyst_tools.py in `backend/services/`:
  `list_snapshots` (first, argument-free, so `[[fake:tool]]` hits a real tool), `get_finding`,
  `sample_captions` (a finding or a `FilterSpec`), `tag_stats`, `compare_snapshot` (the
  findings delta against now, image counts between the snapshot and the branch head). The
  phase 2 sentinel test extends to every tool.
- **3d**, on `feat/analyst-p3d-loop` (record: `docs/dev/analyst/tool-loop.md`): the loop in
  `chat_runner`, only when the chat's model passed the probe: usage
  committed per round, the budget re-checked before each round, a cap on rounds and on result
  characters and then one last round with no tools, the provider slot per round, `tool_call`
  and `tool_result` events. The reply's manifest takes in every category a tool sent, so the
  history rule withholds a reply that quoted it once that category is off.
- **3e**, on `feat/analyst-p3e-ui` (record: `docs/dev/analyst/chat-ui.md` § Tool calls), the UI: chips on a reply that expand to the exact text sent, the offered tools in
  "What gets sent", "Check tool support" in Settings → Analyst, the user docs, an e2e spec.
- **3f**, a tool conversation in `scripts/analyst_trials.py`, live runs on Gemini 2.5 Flash
  and Qwen 3.8 27B, the decision entry, and this section moved into the analyst docs.

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
- Whether the system prompt becomes user-editable through the app-stored prompts (M5).
- Chats spanning several datasets ("compare these two"). One dataset per chat until asked
  for.
