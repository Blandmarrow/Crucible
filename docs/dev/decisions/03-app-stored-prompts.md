# Decision 3: App-stored prompts

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-10-30.
- **Status:** **Decided** (2026-10-30, by the user).
- **Decision:** user-written LLM text and empty category names move from browser storage to
  the database: caption prompt presets, ComfyUI "Generate prompts" settings, the captioning
  workflow blob and declared categories. Existing browser data is copied over by a one-time
  import that keeps the old keys for one release. Per-browser preferences and short-lived
  state stay in the browser. The design is in
  [server-state-plan/README.md](../server-state-plan/README.md).
- **Rationale:** a user reported the data being tied to one browser, where clearing browser
  data loses it and `localhost` and a LAN IP hold separate copies. Only the workflow blob
  needs a synchronous read at page start, so a loading gate on the two pages that read such a
  value keeps any failure local to that page.
- **Rejected alternatives:**
  - **Moving all browser storage.** Page sizes, collapse state and gallery position are
    per-browser by nature, and the gallery's hand-written persistence makes that move costly
    for little benefit.
  - **Loading everything at app startup through a key/value table.** It would keep
    `loadPersisted` synchronous but needs a startup wait, an in-memory cache, a mode that
    disables writes when the load fails, and a key allowlist. A failed load would affect the
    whole app rather than one page.
  - **A localStorage journal of unsent writes.** Replaying it later could overwrite newer data
    from another browser, to protect only the last 350 ms of edits to a very large blob.
- **Accepted costs:** the data is shared per install with no per-user separation, so across
  tabs or `--lan` the last save wins.
