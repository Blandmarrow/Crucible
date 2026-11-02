# Decision 4: Rating keys and old snapshots

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-02.
- **Status:** **Decided** (2026-11-02, by the user).
- **Decision:** two rulings made while porting M4's keep/cut rating onto `main`, where label
  hotkeys and the `detections` mirror had landed since the rating was written.
  - **A label wins a shared key, and the rating keys are rebindable.** Label hotkeys accept
    any of a–z and 0–9, and the rating used fixed digits, so one keypress on the detail page
    could toggle a label and write a rating. When a label hotkey owns the key, the label
    fires and the rating does not. The five rating keys live in settings, default to
    4/3/2/1 and 0, and are rebound in Settings → Gallery, which warns about a clash in both
    directions.
  - **A snapshot older than the rating leaves ratings alone.** The mirror's NULL means "not
    recorded", as for `detections`, and unrated is stored as `0`. Restoring such a snapshot
    keeps the current ratings.
- **Rationale:**
  - Keys: neither binding should break the other. Labels came first on `main` and can already
    be bound to digits, so the rating yields and gets a way to move instead.
  - Snapshots: every snapshot anyone holds predates the rating. Reading its NULL as
    "unrated" would clear every rating made since on restore, and a rating is hand-made work
    nothing can recompute.
- **Rejected alternatives:**
  - **Reserving 0–4 for the rating.** It would break any label already bound to a digit, and
    the label charset is deliberately structural rather than a reserved list
    ([labels-ui.md](../labels-ui.md)).
  - **Both firing on one key.** Simple, but one keypress doing two writes is a surprise.
  - **Clearing ratings on an old restore, with the confirm dialog's count as the warning.**
    The branch's design; it relied on the user reading a number before a destructive step.
- **How it's verified:** `backend/tests/test_rating_old_snapshots.py` and
  `backend/tests/test_settings_rating_keys.py`, and the e2e case that binds a label to a
  rating digit on the detail page.
