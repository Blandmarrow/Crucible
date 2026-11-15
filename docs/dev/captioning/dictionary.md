# The synonym dictionary

This file covers the offline WordNet dictionary behind the caption editor's synonym card
(#52): the install task in `backend/services/dictionary_service.py`, the `/dictionary`
router, the archive guards, the SQLite schema and the morphy port that finds a word's base
form. The card itself, its selection state and the swap guard are in
`docs/dev/captioning/editor.md` § Synonym card. The pinned download is `backend/downloads.py`,
shared with LaMa and indexed in `docs/dev/shared-utilities.md`. The licence text is in
`THIRD_PARTY_NOTICES.md`.

## The data

**Open English WordNet 2025, core edition**, never the `-plus` edition, whose proper nouns
come from Wikidata. The JSON zip is the GitHub release asset of tag `2025-edition`. It is
9,986,555 bytes, and its SHA-256 is `ZIP_SHA256`, which GitHub publishes for the asset.
en-word.net serves byte-identical copies. The archive was checked on 2026-11-14:

- It holds 73 flat members, 72.4 MB unpacked: `entries-{0,a..z}.json`, `frames.json`, four
  `adj.*`/`adv.*` files and the `noun.*`/`verb.*` lexfiles. No licence file is inside, so
  ours come from `backend/resources/wordnet/`, copied byte for byte from the tag.
  Upstream's `WNDB_License.txt` really does carry line-number prefixes.
- An entry is `word → POS key → {sense: [{id, synset, …}], form?: [...]}`. A POS key can
  be `n-1`/`n-2` for a homograph, so `_pos` takes its first letter.
- A synset is `{members, definition: [...], partOfSpeech, similar?, …}`. An adjective head
  (`a`) and its satellites (`s`) point at each other through `similar`.
- **The irregular forms are the entries' `form` lists**: `geese` on `goose`, `ran` on
  `run`, `better` on `good` and `well`, about 4,470 in all. These are WordNet's exception
  files folded into the release, so no second download is needed. The user chose WordNet's
  own lists over a hand-written table (decision 18).

## Install

The install is **not a `BackgroundJob`**. The job queue runs one job at a time, so a 10 MB
download would wait behind a long scoring run, and the toggle would look broken
(decision 18). Instead there is one module-level `asyncio.Task` and a `_Progress` record,
which Settings and the card poll through `GET /dictionary/status` once a second, and only
while the state is `downloading` or `converting` (`useDictionaryStatus`). Nothing appears
in the global progress bar or the Logs page.

- `start_install()` is idempotent: it joins a running task, and does nothing when the file
  is installed. Settings fires it whenever the toggle turns on without a dictionary.
- **The worker thread owns the whole install, cleanup included** (`_install_sync`, sent to
  the CPU pool with `asyncio.to_thread`; the GPU-thread guard only flags executor calls
  that reach torch code). Its `finally` deletes the zip and the `.tmp` database after the
  zip is closed, and `download_verified` deletes the `.part`. Cleanup on the asyncio side
  would run while the thread still held the zip open, which Windows refuses.
- **Stopping is the `_stop` event, never a cancel first.** The download checks it between
  chunks and the conversion between members. `shutdown()` sets it and waits up to 5 s for
  the task, so the thread ends cleanly and the task records no exception. It cancels only
  past that grace. A stalled server can hold the thread in `read()` for `_READ_TIMEOUT`
  (20 s), and a shutdown that catches it there waits that long (the PM-027 class).
  `main.py`'s lifespan calls it before stopping the job queue.
- `sweep_leftovers()` at startup removes what a killed process left behind, and logs
  rather than raises on a file the OS will not release. A leftover is never trusted.
- Failures land in `_progress.error` as one sentence the UI shows as it is: a hash
  mismatch, a conversion error, a network error, a disk error, or anything else. **The
  sentence is fixed per exception type and never interpolates the exception** — it
  reaches the browser through `status()`, an `OSError`'s text names local paths, and
  CodeQL's `py/stack-trace-exposure` flagged the `{e}` it once carried (alerts #111–#113).
  The log keeps the detail. A failed state lasts until the next install or removal.
- **Remove unlinks the database first**, through `utils.unlink_retrying`, then the rest
  of the folder. On Windows a lookup or status read holding the file open blocks the
  delete, and the `FileInUseError` becomes `main.py`'s 409. The folder used to go first:
  `rmtree` deleted the licence notices, then failed on the database, which left a working
  dictionary without its notices.
- An install and a removal cannot overlap: each refuses with 409 (`Busy`) while the other
  runs, and `_removing` covers the removal's own awaits.

Installed means `data/dictionary/oewn-2025.sqlite` exists and its `meta.schema` equals
`SCHEMA_VERSION`. A future schema change bumps that constant, and every install then reads
as missing until it is rebuilt.

## The archive guards

The hash pin already makes the bytes trusted, so these are cheap second guards. `build`
reads members in memory and never extracts to disk. It refuses any member name outside
`_MEMBER_RE`, which rules out a path or a zip-slip, and refuses an archive that declares
more than `_MAX_UNPACKED` (300 MB). It then loads one member at a time into SQLite, so the
72 MB of JSON is never all in memory. On the real archive this takes about 1 s and gives
a 24 MB file. A real install, download included, took 5 s on 2026-11-14.

## Schema

- `synset(id, pos, definition, members, similar)`: the first definition only, members and
  similar ids as JSON.
- `sense(word, pos, spelling, rank, synset_id)`: `word` is the lookup key (lowercase, one
  space between words), `spelling` the entry as written.
- `form(form, pos, lemma)`: the irregular forms.
- `meta`: the schema version, edition, source URL and hash.

**Rank restarts at 0 for each entry, so `cat` and `CAT` interleave unless the query orders
by spelling first.** Both fold to the key `cat`. Without that order, the CAT-scan meaning
came first for "cats". The entry spelled as the user typed it wins (`CAT` in "CAT scan"),
then the lowercase entry, then each entry in its own WordNet order.

## Lookup

`normalize` turns a selection into a key: lowercase, `_` as a space, at most 4 words and
40 characters, letters, combining marks and `' - .` only. A mark counts because `İ`
lowercases to `i` + U+0307. Anything else raises `ValueError`, which the route answers with
422. The frontend's `captionWords.selectedWord` applies the same limits, and stricter ones
on punctuation.

`base_forms` is morphy as Princeton and NLTK run it, for each POS in turn:

1. An irregular form is used alone when there is one: the word itself plus its `form`
   lemmas.
2. Otherwise the regular suffix rules in `_RULES` apply. `women` comes from `men → man`,
   and `ice creams` from `s → ''` on the whole phrase.

Every candidate must be a known lemma, and the word itself sorts first.

`synonyms` returns `{word, lemmas, senses}`. A sense's `words` are its synset's members
minus the word and the lemma. For an adjective, the members of its `similar` synsets follow
in upstream order, because that is where most adjective synonyms live: `shiny` gains
`glossy` through its satellite. A sense with no words is dropped, so the bird sense of
`goose` vanishes and the "silly person" sense stays. There are at most 8 senses and 12
words a sense. Each call opens one read-only `sqlite3` connection and takes about 1 ms.

## Testing

`backend/tests/test_dictionary.py` builds a tiny archive in the real shape and serves it
through `downloads._open`. The conftest autouse fixture `_no_real_download` makes that
call refuse in every other test (`docs/dev/backend-tests.md` § No real LLM server). The tests
pin the install and its cleanup, hash and member refusals, the offline failure, the
leftover sweep, morphy, the spelling order, adjective `similar` words and the route codes.
`frontend/e2e/detail-synonyms.spec.ts` fakes the routes and pins the card. No gate runs
the real archive, so a change to the converter or the pin wants one real install (the
`run-app` skill, on a throwaway instance).
