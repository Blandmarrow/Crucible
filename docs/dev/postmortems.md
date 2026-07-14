# Postmortem index

This file indexes past incidents as one-line rows so code reviews and bug investigations
can check new code against failure classes we have already been burned by. Each real
incident gets a detail file under `docs/dev/postmortems/` (copy
`docs/dev/postmortems/PM-000-template.md` to `PM-NNN-short-slug.md`, next free number).

**Usage note**: treat LIVE and MITIGATED entries as an active checklist for their code
class. STRUCTURAL entries are kept for history only — a refactor made that class of bug
impossible — and should not drive review attention. Keep the Symptom column greppable:
phrase it with the words someone would actually search for when the bug resurfaces.

| ID | Symptom | Root-cause category | Status | Detail |
|---|---|---|---|---|
| PM-EX (EXAMPLE — delete me) | Timestamps in job history shifted by the browser's UTC offset ("job ran 3h ago") | Naive datetime serialized without timezone | STRUCTURAL | `docs/dev/postmortems/PM-000-template.md` (illustrative — real rows link their own PM-NNN file) |
