# Model trials

**Status: living.** The end of M12 phase 2: scripted conversations run against real models,
to set the "minimum model we recommend" line in `docs/analyst.md`. This file covers the
trial script, what it checks, and the results so far. The chat it drives is
[chat.md](chat.md) and [sending.md](sending.md); the notes block it checks is
[proposals.md](proposals.md); the family hub is [../analyst.md](../analyst.md).

## The script

`scripts/analyst_trials.py` runs three fixed conversations about one synthetic dataset
through the real app, in-process against a throwaway database (the backend tests'
`api_env`), and prints a Markdown report. Nothing touches the real database or datasets
folder, and the dataset is made up (coloured squares, invented captions about a fox
mascot), so nothing private reaches a remote provider. The key is read from the
environment or the dev database and never printed.

```text
python scripts/analyst_trials.py --from-db Gemini --pause 8
python scripts/analyst_trials.py --base-url http://localhost:1234/v1 --model qwen3-8b --out trial.md
```

`--from-db NAME` reads a saved provider's address, key and default model from
`dataset_manager.db`, read-only. `--pause` spaces the conversations for a rate-limited
free tier.

**Every trial chat shares everything** (`SHARE_ALL`, through the chat's privacy override).
The trial measures the model, not the privacy settings: under a billed provider's default
of numbers only, the model sees neither the notes nor the goal, so "remove open_item 1" is
a guess Crucible rightly drops, and the first Gemini run failed that way (2026-11-19).

## What passes

| Conversation | Asks | Passes when |
|---|---|---|
| `rank` | What to fix first, and why | The reply finished, names at least one real finding as `[[finding:id]]`, and invents none |
| `note-decision` | Record a decision the user states | A finished reply yields an `add decision` proposal |
| `note-remove` | Remove an open item the user names in words | A finished reply yields a `remove open_item` proposal, which needs the model to map the words onto the numbered listing |

"Finished" means `status: complete` with no `error_kind`, so a reply cut off at Max tokens
fails, which is the point: a reasoning model that needs more room than the provider allows
is not usable as configured.

## Results

| Date | Model | Max tokens | rank | note-decision | note-remove | Notes |
|---|---|---|---|---|---|---|
| 2026-11-19 | Gemini 2.5 Flash (OpenAI-compatible endpoint) | 16,000 | pass | pass | pass | Hidden reasoning: about 500 output tokens for a short ranked answer |
| 2026-11-19 | Qwen 3.8 27B, local (LM Studio, `qwen3.8-27b`) | 16,000 | pass | pass | pass | Three valid references in `rank`; 19 s for the first reply, about 2 s for the short ones, on one desktop GPU |
| 2026-11-19 | Gemini 2.5 Flash, after phase 3a's prompt | 16,000 | pass | pass | pass | `rank` names the real fix, the Gallery page's "Review or upscale them", and no page that does not exist |

Smaller local models are untested so far. The user doc's model line names what passed and
says so; a new result goes in this table and, when it changes the advice, in that line.

## Observations

- **Gemini names tools that do not exist** ("Image Review", "Image Editor"). The system
  prompt tells it to suggest Crucible's tools but never lists them, and the finding cards'
  fix buttons are the real ones. Qwen 3.8 27B pointed at "the card" instead. Since M12
  phase 3a the prompt names the fix pages and points at the cards
  ([chat.md](chat.md) § The system prompt). Gemini's re-run on it named no invented page;
  Qwen has not been re-run.
- **Under a billed provider's default privacy, the analyst cannot see the goal or the
  notes.** It asked what the goal was although one was set. That is the default working as
  designed, and `docs/analyst.md` § What gets sent now says what the analyst cannot know
  under it.
