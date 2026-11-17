# M11 — Open enhancement issues

**Status: archive (landed 2026-11-18).** *Record for the M11 line in the
[roadmap](../roadmap.md#landed).* Its boxes merged between 2026-11-13 and 2026-11-15, PRs
#157 to #172. It was archived on 2026-11-18, when #129 was expanded into M12 under
[decision 21](../decisions/21-dataset-analyst.md); its one open box, #48, moved to the
roadmap's Waiting section, since it waits on its reporter.

The section as it stood when archived, word for word. Its relative links have been
rewritten for this folder.

## The section

Ordered first by the user on 2026-11-13, ahead of M7's Stage 3 (now under Waiting). It holds
every open GitHub issue labelled `enhancement` except #90, #9 and #16, which stay parked
under Later.
Each issue holds the *what*; a box adds only the code pointer found when the issues were
reviewed. Gate: each box ships with its user docs (`README.md` and the matching
`docs/*.md`), on its own branch unless it says otherwise. The two design-first items get a
plan folder and are expanded here before any code. The tiers are the order.

Tier 1, small:

- [x] #24 Remove captions: a `clear` operation for caption bulk edit, merged 2026-11-13 as
  PR #157, which closed the issue. The user chose to delete a cleared caption's
  `.txt`, and to make that the rule for every path that empties a caption
  (`docs/dev/image-import.md` § Caption sidecars).
- [x] #105 Re-caption with the existing caption: merged 2026-11-13 as PR #159, which closed
  the issue. `{previous_caption}` now works on every pipeline step and on `/run`, the
  detail page's AI panel has a Fix current mode, and the Captioning page's step 1 has an
  "Include current caption" toggle (`docs/dev/captioning/jobs.md` § The `{previous_caption}`
  placeholder).
- [ ] #48 Toggle thumbnails: ask the reporter what the problem is first. The likely fix is a
  thumbnail size setting (today `thumbnail_size = 256` in `backend/config.py`, set only by
  environment variable) plus the existing Rebuild thumbnails job. Full resolution in the
  gallery as an opt-in at most; thumbnails stay on, since other views depend on them.

Tier 2, medium:

- [x] #19 Light mode: merged 2026-11-14 as PR #162, which closes the issue. The user picked
  "Studio grey" from five drafts that day, with a Dark / Light / Match system choice in
  Settings → UI Behavior, dark the default. A second token block under
  `:root[data-theme="light"]`, set before first paint, plus `--accent-text` and
  `--on-accent`; about 60 files swept onto tokens (`docs/dev/styling.md` § Two themes).

Tier 3, captioning helpers, one batch: three PRs in this order, by the user's choice of
2026-11-14. Each helper sits in the detail page's caption panel and changes only the
unsaved draft.

- [x] #45 Other images' captions: merged 2026-11-14 as PR #163, which closes the issue. An
  *Other captions* section in the detail page's caption panel, ranked by shared tags,
  detected objects, the stored CLIP vector or recency, inside the current dataset only (the
  user's ruling of 2026-11-14) (`docs/dev/captioning/editor.md` § Other captions panel).
- [x] #50 Check for a better tag: merged 2026-11-14 as PR #164, which closes the issue.
  A **Check tags on Danbooru** button in the caption panel, press-only, through a new
  `POST /booru/check`. It flags aliases, rare tags (under 100 posts with a close tag holding
  at least 100 times more, the user's rule of 2026-11-14) and unknown ones, and an outage
  is a 502 rather than every tag "not found". Danbooru only, by the user's choice. The same
  PR moves the Booru page's Safebooru search to danbooru.donmai.us with a User-Agent, after
  safebooru.donmai.us answered every API request with a 403 (`docs/dev/captioning/editor.md`
  § Check tags).
- [x] #52 Synonyms: merged 2026-11-15 as PR #165, which closes the issue. An offline
  WordNet lookup (`docs/dev/captioning/dictionary.md`), a Settings toggle, and a card under the
  caption box (`docs/dev/captioning/editor.md` § Synonym card), the user's choice over a
  popover at the word, 2026-11-14. The install runs outside the job queue and irregular
  forms come from WordNet's own lists (decision 18). Licence checked
  2026-11-14: Open English WordNet 2025, core edition, which is CC BY 4.0 on top of the
  Princeton WordNet licence. Both allow any use. Credit both projects, keep Princeton's
  notice and disclaimer with every copy, note our conversion, and never use Princeton's
  name in advertising. The app downloads the official JSON zip (about 10 MB) with a pinned
  hash and converts it locally, so the repo carries none of the data. Skip the "2025+"
  edition, whose proper nouns come from Wikidata.

Tier 4, design first:

- [x] #130 Better comparisons: the Compare page, merged 2026-11-15 under
  [decision 20](../decisions/20-compare-view.md), one PR per phase, each after an Opus review
  whose findings were fixed in the same PR. User doc `docs/compare.md`, dev doc
  `docs/dev/gallery/compare.md`; the design plan was deleted when the last phase landed.
  The five phase boxes (PRs #168 to #172) are in
  [handoff-2026-11-15.md § #130's phase boxes](handoff-2026-11-15.md#130s-phase-boxes).
- [ ] #129 Chat with an LLM: a plan folder. Read-only tools first, writes behind a
  confirmation, and the paid-call cap applies; weak tool calling in local models is the main
  risk. Large enough to become its own milestone when it is expanded.
