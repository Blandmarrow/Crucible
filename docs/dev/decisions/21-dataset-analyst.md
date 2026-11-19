# Decision 21: The LLM chat is a dataset analyst built on Crucible's own findings

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-18.
- **Status:** **Decided** (2026-11-18, by the user) for the rulings below, made in a design
  discussion on 2026-11-17 and 2026-11-18 while designing issue #129. The implementation
  details marked **Leaning** were proposed in the plan and are safe to build on, but the
  user has not ruled on them one by one. The plan is
  [analyst-plan/README.md](../analyst-plan/README.md).
- **Decision:**
  - **An analyst first, actions later.** Crucible gives the LLM facts about one dataset; the
    LLM gives feedback and suggests actions. Performing actions comes last, behind a
    confirmation.
  - **A health report computed by Crucible, with no LLM**: a list of findings, each with its
    numbers, a link to the affected images and a pointer to the tool that fixes it. Findings
    can be dismissed with a reason; changes since the last snapshot are shown.
  - **An optional goal per dataset**, separate from its description, so the user does not
    restate the aim in every chat. Never required.
  - **Chats are saved per dataset**, and deleted with it.
  - **Dataset notes**, a simplified version of this repository's own docs (preferences,
    decisions, open items), plus app-wide notes about the user, which the LLM proposes edits
    to and the user approves.
  - **Crucible enforces what the LLM can see**, with toggles per category and a view of what
    gets sent.
  - **Budgets in tokens**, per chat and per day, with an estimate before sending to a billed
    provider; local providers are uncapped.
  - **The chat can stay visible**, tried first with the split-view panes, an overlay later if
    panes fall short.
  - **Nothing reaches `main` until the whole feature is done**, the design docs included:
    everything lives on `feat/analyst`, one reviewed PR per phase into it, and one final PR
    to `main`.
  - **Leaning:** the phase order (a fake LLM server, the health report, the chat without
    tools, read-only tools, actions, caption–image agreement); a dismissal stored as a note
    item; notes stored as separate items rather than one document; the `[[finding:id]]`
    references and the `crucible-notes` block as the output protocol; one `build_context`
    function as the only place context is assembled; every confirmed action taking a
    snapshot first.
- **Rationale:**
  - Crucible finds the problems and the LLM explains them. Deterministic findings can be
    tested, are useful with no LLM, and spare a weak local model the tool calling that the
    roadmap named as the main risk; a short list of findings also fits a small context.
  - A list of findings, not one score: a single number hides the reason, and what counts as
    healthy depends on the goal (a style LoRA wants consistency, a concept dataset variety).
  - Notes carry the continuity between chats, as `CLAUDE.md` does here, so a new chat loads
    the notes and the current findings instead of every old transcript.
  - Crucible builds the context, so it can decide exactly what leaves the machine. Captions
    and dataset names sent to a cloud provider are the user's data.
  - Tokens need no price list kept current by hand.
  - The user does not want any part of the feature on `main` before all of it is ready.
- **Rejected:**
  - A single 0–100 health score.
  - Reusing the dataset's `description` as the goal: it usually says where the images came
    from, not what they are for.
  - A mini-chat under the sidebar's menu: the sidebar is 240px wide and its nav already
    scrolls.
  - Budgets in dollars, with a price per provider.
  - Landing the design docs on `main` ahead of the code.
  - jina-clip-v2 for caption–image agreement: its CC BY-NC 4.0 licence forbids commercial
    use, and Crucible has other users.
- **Revisit when:** panes prove too awkward for an always-visible chat (the overlay), local
  models turn out unable to use the output protocol, or users ask for one chat across
  several datasets.
- **Amendments:**
  - **2026-11-19, by the user: phase 3's tool calls.** Three rulings made while planning the
    read-only tools, all built in PRs #189 to #194:
    - **A tool call is a row of its own table, `chat_tool_calls`, hung off the reply**, not
      `role=tool` rows in `chat_messages` as the plan first said. The reply row is stored
      before the first call, so rows between it and the user's would move it mid-turn and
      break resume, the "since your last reply" delta and the message count. One reply row
      per turn stays true, and the transcript still replays exactly.
    - **A later turn does not resend earlier tool results.** History carries the reply's
      text only, and the model calls a tool again if it needs the data. That keeps the
      context small for local models; and so that tool data cannot reach the model through
      history after a privacy toggle goes off, each reply's manifest records the categories
      its tools sent, which the history rule reads.
    - **The image filter builder stays in `routers/images.py`.** `feat/m4-rating-port`
      edits its body, so moving it into a service now would clash with the held branch; the
      captions tool imports it late, and M8 stage 8 (split `images.py`) gives it a home.
    - Rejected: `role=tool` rows; replaying earlier tool exchanges within the history
      budget, withheld per category.
