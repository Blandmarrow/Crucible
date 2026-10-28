# Frontend workflow findings

These are the verified findings reproduced in real Chromium against the e2e harness, which uses a
throwaway DB on :8199. The specs are in `docs/dev/gpt-review/probes/frontend/`; see `docs/dev/gpt-review/README.md` for how to run them.
Read `docs/dev/image-detail.md`, `docs/dev/gallery.md`, `docs/dev/frontend-jobs.md` and
`docs/dev/styling.md` as relevant.

## GPT-031 — [P3] An invalid bulk-caption regex is reported as "No captions matched"

**Verdict:** CONFIRMED. NEW.

**Observed** (`gpt031.spec.ts`): Find & Replace with regex `(` returns 200
`{affected:0, skipped:1}`. The modal closes, the selection is cleared, and the toast says "No
captions matched". A valid non-matching regex such as `zzz+` gives an identical response and toast.

**Where:**
- `services/caption_service.py:110-112` catches the compile error and returns `skipped=N`.
- `components/caption/BulkEditForm.tsx:64-66` treats `affected===0` as "no match".

**Fix notes.**
- Return a 4xx from `compile_user_regex` failures with the error detail.
- Keep the modal open with the pattern and the selection intact.
- Check the other bulk regex endpoints (`docs/dev/bulk-ops.md`) for the same swallow.
