# PM-000: <short incident title>

<!-- Template — copy to PM-NNN-short-slug.md and fill in every section. Keep the
     index row in docs/dev/postmortems.md in sync with Status here. -->

### Symptom

Full description of what went wrong and how it surfaced: what the user or developer
observed, the error text or misbehaviour verbatim where possible (greppable), and where
it showed up (which page, endpoint, job, script).

### Root cause

The actual mechanism — not "X was broken" but *why* it broke: the interaction, ordering,
or assumption that produced the symptom.

### Generalizable rule

The reusable red flag a reviewer can apply to code they have never seen. Phrase it as an
instruction, e.g. "flag any path that writes `caption_text` via raw SQL instead of ORM
assignment" — this is the most important field; if it only restates the specific fix, it
is not general enough yet.

### Why it wasn't caught the first time

The missing test, review question, or assumption that let it through. This is what
improves the process, not just the record — name the gap concretely enough that a future
review can close it.

### Fix

What was changed, with a link to the commit/PR if available (e.g. commit `abc1234`).

### Status & date

LIVE | MITIGATED | STRUCTURAL — must match the index row in `docs/dev/postmortems.md`.
Last reviewed for staleness: YYYY-MM-DD.
