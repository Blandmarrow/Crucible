# PM-006: scraped strings interpolated into CREDITS.md could forge license sections

### Symptom

`export_service._write_credits` built `CREDITS.md` by f-string interpolation of
`source_name`, `source_url` and `attribution` straight from the database:

```python
lines.append(f"- **{source}**{f' — <{url}>' if url else ''} ({len(srows)} image(s))")
for attribution in sorted({r["attribution"] for r in srows if r["attribution"]}):
    lines.append(f"  - {attribution}")
```

An `attribution` containing a newline and `## CC0 1.0 (no rights reserved)` therefore
produced a second license heading in the finished document — a *legal attribution file*
shipped with a published dataset, asserting rights the export did not carry. A
`source_url` of `javascript:…` became a bare `<autolink>`. In `licenses.csv`, a cell
beginning `=`, `+`, `-` or `@` was executed as a formula when opened in a spreadsheet
(quoting was already correct; the leading character was not guarded).

No exploit was needed: these values come from scraper sidecars and EXIF tags, so a booru
post whose uploader field contains markdown is enough.

### Root cause

Provenance fields were treated as trusted because they are "our" database columns, when
in fact every one of them originates outside the application — gallery-dl sidecar JSON,
EXIF `Artist`/`Copyright`, a scraped page. The output format (markdown, CSV) has its own
syntax, and nothing translated between "arbitrary text" and "a value in that syntax".

The value is *also* uncapped on the way in, which is the same mistake in the other
direction (see the `clamp_provenance` fix): an unbounded scraped string was written to a
`String(64)` column, stored fine on SQLite, and then failed the response schema on every
subsequent read — making that image's provenance permanently uneditable.

### Generalizable rule

- **Flag any f-string or `"".join` that interpolates a database string into a generated
  document** — markdown, CSV, HTML, YAML, a filename, a shell command. Ask where the
  string originally came from, not which table it currently lives in. If any ingest path
  fills it from a file, a scraper, or an upload, it is untrusted and needs an
  escape/normalise step for the target syntax.
- **Newlines are the highest-value injection in a line-oriented format.** Any escaper for
  markdown/CSV/log output must collapse whitespace *and* control characters, not just
  escape the format's visible metacharacters.
- **A URL from user/scraped data is not a link target** until its scheme is checked. Route
  every such value through one shared validator (`utils.safe_external_url`) rather than
  trusting the renderer.
- **CSV formula injection is about the leading character**, independent of quoting. A
  library that quotes correctly does not protect a spreadsheet.
- **Ingest truncates, the API rejects.** A capture path must not fail on a bad input, and
  an API must not silently accept data it cannot store; getting these backwards produces
  rows that can be written but never edited.

### Why it wasn't caught the first time

The manifest tests asserted only that the *right* values appeared (`"CC BY-SA 4.0" in
credits`). No test fed adversarial input, so nothing distinguished "the value is present"
from "the value is present as data". The feature was reviewed as a data-modelling change
— inheritance, resolution, filters — and the document-generation step at the end was read
as formatting rather than as an output boundary.

### Fix

- `_md_inline` (collapse whitespace/control chars, escape ``\ ` * _ [ ] < > # |``, bound
  length), `_md_link` (link only when `utils.safe_external_url` accepts the scheme;
  otherwise inert escaped text) and `_csv_cell` (prefix `'` to `=`/`+`/`-`/`@`) in
  `backend/services/export_service.py`; every interpolated value goes through one of them.
- `licenses.clamp_provenance` applied at the end of `merge_provenance`, so every ingest
  path truncates to the column width; `licenses.normalize_license_input` as a Pydantic
  validator that normalizes *then* length-checks, so the API rejects instead.
- Adversarial tests in `backend/tests/test_provenance.py` (embedded newlines, markdown
  specials, `javascript:` URL, leading `=` in a CSV cell) and a round-trip test in
  `backend/tests/test_provenance_http.py` that imports an over-long sidecar value and then
  edits that image's provenance successfully.

### Status & date

MITIGATED — the escapers exist and are used, but nothing structurally prevents a new
interpolation site from skipping them. The CLAUDE.md key invariant is the review hook.
Last reviewed for staleness: 2026-07-22.
