# Analyst — the dataset health report, notes, goal and chat

M12, the dataset analyst ([decision 21](decisions/21-dataset-analyst.md)): Crucible works out
the facts about one dataset itself, and an LLM (phase 2 onward) explains them and suggests
what to do. This file is the family's entry point; the tables below route from a code path to
the file in `docs/dev/analyst/` that owns it. The design and the phases still to build are in
[analyst-plan/README.md](analyst-plan/README.md). The `GET /images/` filters a finding links
through are `docs/dev/image-filters.md`; the Stats cache and index pin the report reuses are
`docs/dev/statistics.md`; the snapshot row it records on is `docs/dev/versioning/storage.md`. How a user reads the page is `docs/analyst.md`.

## The family

| File | Covers |
|---|---|
| [health.md](analyst/health.md) | `health_service.py`: the findings catalogue and its constants, `FilterSpec`, the cache and its key, the card-badge write-back, dismissals and resurfacing, the snapshot delta |
| [notes.md](analyst/notes.md) | Dataset and app-wide notes: the three tables, soft delete and history, the size caps, a dismissal as a note, the `/notes` and `/app-notes` routes, the goal fields on `Dataset` |
| [ui.md](analyst/ui.md) | The frontend: `HealthPage`, the notes editor, the dialogs' goal fields, the card badge, their query keys and routing |
| [chat.md](analyst/chat.md) | The chat (phase 2): the `chats`, `chat_messages` and `llm_usage` tables, the token budget settings, privacy defaults and overrides, `build_context` and its categories, the preview route |
| [sending.md](analyst/sending.md) | Sending a message: the order of a send, the streamed call and its two time limits, usage before the reply, the SSE events and resume, budgets, cancel and the sweeps, the provider limit shared with captioning |
| [proposals.md](analyst/proposals.md) | Note proposals: the `crucible-notes` block and its parser, numbers resolved through the reply's manifest, `chat_proposals`, accept, reject and undo, the notes' chat columns, the suggestion cards and "Ask it to note this" |
| [trials.md](analyst/trials.md) | The model trials: `scripts/analyst_trials.py`, its three conversations and what passes, the results per model, what the trials showed |
| [tools.md](analyst/tools.md) | Tool calls (phase 3): the stream's `tools` and `ToolCallBuffer`, the per-model tool-call probe, `tools_probe` and `tools_ok`, when a verdict is stored and when it is cleared |
| [tool-registry.md](analyst/tool-registry.md) | The tools (phase 3c): `ContextItem` and `render_tool_result`, the tools' privacy filter; the registry, `offered`, `tool_specs` and `run_tool`; each tool and what it sends; adding a tool |
| [tool-loop.md](analyst/tool-loop.md) | The tool loop (phase 3d): who gets tools, rounds and their caps, usage per round, the budget between rounds, `chat_tool_calls`, the manifest merge that history reads, the `tool_call` and `tool_result` events |
| [chat-ui.md](analyst/chat-ui.md) | The chat page: `chatStore` and the stream outside React, resume, rendering a reply (markdown, no images, finding chips, the strip), the controls that save as you use them, Settings → Analyst |

## Where things live

| Editing… | Read |
|---|---|
| `backend/services/health_service.py`, `backend/routers/health.py`, `backend/schemas/health.py` (`Finding`, `FilterSpec`, `HealthReport`) | `docs/dev/analyst/health.md` |
| `backend/services/version_service.py` (`create_snapshot`'s `findings_summary`) | `docs/dev/analyst/health.md` § The delta against the last snapshot; + `docs/dev/versioning.md` for the rest |
| `backend/models/notes.py`, `backend/services/notes_service.py`, `backend/routers/notes.py` | `docs/dev/analyst/notes.md` |
| `backend/models/dataset.py` (`goal`, `target_resolution`, `health_summary`), `backend/routers/datasets.py` (`_apply_goal_fields`) | `docs/dev/analyst/notes.md` § The goal fields; + `docs/dev/dataset-operations.md` for the rest of the routes |
| `frontend/src/pages/HealthPage.tsx`, `frontend/src/components/health/`, `frontend/src/api/health.ts`, `frontend/src/api/notes.ts` | `docs/dev/analyst/ui.md` |
| `frontend/src/components/datasets/GoalFields.tsx`, `HealthBadge.tsx` | `docs/dev/analyst/ui.md`; + `docs/dev/dataset-operations.md` for the dialogs, `docs/dev/datasets-page.md` for the card |
| `backend/models/chat.py`, `backend/schemas/chat.py`, `backend/routers/chats.py`, `backend/services/analyst_context.py`, `openai_providers.analyst_privacy` | `docs/dev/analyst/chat.md`; + `docs/dev/llm-providers.md` for the rest of the provider |
| `backend/services/chat_runner.py`, `backend/ml/chat_stream.py`, `backend/services/llm_limiter.py` | `docs/dev/analyst/sending.md`; + `docs/dev/captioning/jobs.md` for the caption side of the limiter; + `docs/dev/analyst/tool-loop.md` for the tool loop in `_execute` |
| `backend/models/chat.py` (`ChatToolCall`), `TOOLS_PROMPT` and `tools_offered` in `build_context` | `docs/dev/analyst/tool-loop.md` |
| `backend/services/note_proposals.py`, `backend/models/chat.py` (`ChatProposal`), `frontend/src/components/chat/ProposalList.tsx` | `docs/dev/analyst/proposals.md` |
| `scripts/analyst_trials.py` | `docs/dev/analyst/trials.md` |
| `backend/services/analyst_tools.py`, `render_tool_result` and `ContextItem` in `backend/services/analyst_context.py` | `docs/dev/analyst/tool-registry.md` |
| `backend/services/tool_probe.py`, `POST /providers/{id}/probe-tools`, `openai_providers.tools_probe`, the tool calls in `backend/ml/chat_stream.py` | `docs/dev/analyst/tools.md` |
| `frontend/src/components/chat/ToolCalls.tsx`, `ToolSupport` in `AnalystPanel.tsx` | `docs/dev/analyst/chat-ui.md` § Tool calls and § Settings → Analyst |
| `frontend/src/pages/ChatPage.tsx`, `frontend/src/components/chat/`, `frontend/src/store/chatStore.ts`, `frontend/src/api/chats.ts`, `frontend/src/components/settings/AnalystPanel.tsx` | `docs/dev/analyst/chat-ui.md` |
| `frontend/src/components/health/FindingCard.tsx`, `useFindingActions.ts`, `useHealthReport.ts` (shared by Health and the chat) | `docs/dev/analyst/ui.md`; + `docs/dev/analyst/chat-ui.md` for the chat's use |
| `frontend/src/components/common/FilteredImagesModal.tsx` | `docs/dev/statistics-panels.md`; + `docs/dev/analyst/ui.md` for the Health page's use |

## The rule a later phase must not break

Crucible finds the problems; the LLM explains them. A finding is computed here, tested here,
and useful with no LLM at all: a later phase may rank, explain or act on findings, but never
invent one. Phase 2 adds the second rule, a CLAUDE.md key invariant: everything an LLM sees
about a dataset is built by `analyst_context.build_context`, and its privacy filter is applied
there and nowhere else (`analyst/chat.md` § `build_context`: the one choke point).
