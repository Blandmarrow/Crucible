# The analyst's tools and their privacy filter

**Status: living.** M12 phase 3c: the read-only tools a model that passed the tool-call
probe can call, how a tool's result is built, and the filter it goes through. The probe
and the stream's tool calls are [tools.md](tools.md); the context and its filter, which
this one mirrors, are [chat.md](chat.md) § `build_context`: the one choke point. The loop
that offers the tools and calls them is phase 3d, planned in
[../analyst-plan/README.md](../analyst-plan/README.md) § Phase 3.

## Where things live

- `backend/services/analyst_context.py`: `ContextItem`, `ToolRender` and
  `render_tool_result`, the tools' privacy filter. It sits beside `build_context` on
  purpose: these two are the only code that turns dataset data into text an LLM sees.
- `backend/services/analyst_tools.py`: the registry `TOOLS`, `offered`, `tool_specs` and
  `run_tool`.

## A tool returns items, never text

A handler returns `ContextItem`s, each in one privacy category, and `render_tool_result`
alone turns them into the string the model gets. So a tool cannot send what the chat's
privacy holds back, whatever its handler does. An item has:

- `text`, cleaned by `_clean` when rendered, unless `own`.
- `own=True` for Crucible's own words, sent as written: a count, an id, a date, a
  finding's line (`finding_line`, which drops tag names from `numbers`). **Only a
  `numbers` item may be `own`**, and the filter raises on any other; never put dataset
  or user text in one.
- `label` and `label_category`: dataset or user text shown before the text (a file name
  before its caption, a snapshot's name before its counts), sent only while its own
  category is on, and cleaned.
- `prefix` and `suffix`: Crucible's words around it, such as `added: ` or ` (42)`.

## The filter

`render_tool_result(title, items, privacy, error=None) -> ToolRender`:

- **An item whose category is off is left out and counted.** The result ends with "Not
  shared in this chat: 3 captions", so the model can tell the user why. A label whose
  category is off is dropped from its line, and the line still goes.
- **Dataset and user text is cleaned** by `_clean`, as the context's is: one line, no
  format characters, NFKC-folded, no angle brackets, the delimiter's name broken.
- **Tags, captions and file names sit inside one `<dataset-data>` block**, which the
  system prompt calls data, never instructions. A line goes there when its category, or
  a label it shows, is one of `DATA_CATEGORIES`. Identity and notes are the user's own
  words: cleaned, but outside the block, as in the context.
- **A result stops at `TOOL_RESULT_MAX_CHARS`** (6,000), each item at
  `TOOL_ITEM_MAX_CHARS` (300), and says how many items were cut.
- **An error is rendered too**, cleaned, since it may quote an argument the model sent;
  it carries no category.

`ToolRender.categories` lists the categories the text carries. The loop (3d) merges them
into the reply's manifest, so the history rule withholds a reply that quoted a tool's
captions once captions are turned off ([chat.md](chat.md) § History).

## The registry

Each `Tool` in `TOOLS` has:

- **A hand-written JSON schema** of plain types, with no `anyOf` and no `null`, since
  servers accept different amounts of JSON Schema.
- **A pydantic model** (`extra="forbid"`) that validates what the model sent. Every
  number is bounded and every string capped, the nested filter included (`ToolFilter`,
  a `FilterSpec` with limits), so a wild value is an error for the model and never an
  overflow in SQLite. `test_each_schema_names_its_models_fields` checks the schema and
  the model agree, and that the filter's `score_field` and `quality_flag` enums are the
  values the images router accepts (`_ALLOWED_SCORE_FIELDS`, `ALLOWED_FLAG_KEYS`).
- **`needs`**, the categories it reads. `offered(privacy)` keeps a tool only while all of
  them are on, and `tool_specs` is what a request offers. That is the first layer; the
  filter is the second.
- **A read-only handler**, `(db, dataset, args, privacy) -> Outcome(items, error)`. The
  privacy is for what a tool may *query* by (below); its output goes through the filter
  whatever the handler does.

`run_tool(db, ds, name, arguments, privacy)` is the one entry point. An unknown tool, one
not offered under this privacy, arguments that are not a JSON object (nested deep enough
to exhaust the parser included) or fail validation, and a handler that raises all become
a short error the model can act on; a raise is also logged. Nothing a tool does writes to the database.

| Tool | Arguments | Needs | Reads | Sends |
|---|---|---|---|---|
| `list_snapshots` | `limit` ≤ 20 | numbers | `DatasetVersion` rows, the branch head (`health_service._baseline`) | id, date, image count, source, pinned, latest on the branch, findings recorded (numbers); name (identity label); description (identity) |
| `get_finding` | `finding_id` | numbers | `health_service.find`, dismissed findings included | the finding's line and its count at the latest snapshot (numbers); the dismissal's date and count (numbers) and reason (notes); the tags in `tag_everywhere` and `tags_once` (tags) |
| `sample_captions` | `finding_id`, or `filter` (a `FilterSpec`), or neither; `limit` ≤ 20 | captions | the finding's `filter_spec` or the filter, through `_apply_image_filters`; a stratified pick by caption length (`_stratified_positions`), seeded on the dataset and the filter | how many match and how many were picked (numbers); each caption (captions) with its base file name (file_names label) |
| `tag_stats` | `contains`, `limit` ≤ 100 | tags | `health_service.caption_summary`'s `ranked`, its 500 most used tags | the tag-style and prose counts (numbers); each tag with its count (tags) |
| `compare_snapshot` | `version_id` | numbers | the snapshot's `findings_summary` against the live report (`summary_changes`); `version_service.diff_versions` between it and the branch head | image counts then and now, each finding's change, images added, removed and changed, changes by column (numbers); the snapshot names (identity labels); up to 5 added and 5 removed file names (file_names) |

Notes on the choices:

- **`list_snapshots` comes first and takes no arguments**: the e2e suite's
  `[[fake:tool]]` calls the first tool offered with `{}`.
- **`sample_captions` uses the images router's filter builder**, imported late inside
  the handler. `feat/m4-rating-port` edits that function's body, so moving it now would
  clash; M8 stage 8 (split `images.py`) gives it a service home. A filter it refuses
  (a bad `quality_flag`, say) is an error for the model, not a 400.
- **`search` is refused while file names are not shared.** It matches the original file
  name as well as the caption, so with File names off a model could still find names
  one guess at a time from the match count. It is a case-insensitive `LIKE`, so `%` and
  `_` match any text; the schema says so.
- **`tag_stats` counts as the context's Tags category does**: tag-style captions only,
  once per image, ties by name, from the cached caption pass. That pass keeps a `ranked`
  list of `TAG_STATS_MAX` (500) beside its `top` 30 for this. `caption_service.get_tag_stats`
  was not used: it splits prose captions on commas too, so its counts would disagree with
  the context's.
- **`compare_snapshot` compares findings with now, but images only with the latest
  snapshot**: there is no snapshot-against-the-live-dataset diff. When the snapshot is
  the latest, it says changes since are not recorded image by image. `diff_versions`
  reads every image state of both snapshots, which is fine for one call; 3d's cap on tool
  rounds bounds how often a reply can ask. The example file names are sorted, since the
  diff builds them from sets.

## Adding a tool

1. A pydantic args model on `_Args`, a schema built with `_object`, plain types only.
2. A handler returning `ContextItem`s: dataset or user text never `own`, numbers `own`.
3. Its `needs`: every category it cannot do without. A category it only sometimes sends
   (a label) need not be in `needs`; the filter handles it.
4. Add calls to `_calls` in `test_analyst_tools.py`, so the sentinel test runs them
   under all 64 combinations, and seed a sentinel for any new kind of text.

## Tests

`backend/tests/test_analyst_tools.py`:

- **The sentinel test**: a dataset with a sentinel in every category (a snapshot's name
  and description, a dismissal reason, captions, tags, file names), and ten tool calls
  under all 64 combinations of toggles. A tool needing an off category is not offered and
  refuses to run. One that runs sends no disabled category's sentinel, no absolute path,
  and none of the provenance, licence or subfolder sentinels, and its `categories` are
  all on. With everything on, every sentinel arrives somewhere.
- The schemas against their models, and what each privacy offers.
- The filter on its own: withheld counts, a dropped label, the data block, delimiter
  tricks, the size cap, an `own` item outside numbers refused, an error cleaned.
- Each tool's output on the seeded dataset, a sample repeating for the same call, and the
  errors for bad calls: deep nesting, unbounded numbers, a NaN, an overlong search, and a
  search while file names are off.
