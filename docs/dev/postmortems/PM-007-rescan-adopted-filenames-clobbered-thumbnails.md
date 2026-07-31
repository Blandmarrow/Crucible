# PM-007: rescan-registered files shared one thumbnail/poster

### Symptom

Two files whose names differ only by extension, dropped into a dataset folder by hand and
picked up by **Rescan**, ended up sharing a single derived image:

- `a.png` and `a.jpg` in `images/` both registered with `thumbnail_path` =
  `thumbnails/a.webp`. Whichever `rglob` reached second overwrote the first's thumbnail, so
  the gallery grid showed the same picture for two different images.
- `clip.mp4` and `clip.mkv` in `videos/` both registered with `poster_path` =
  `videos/thumbnails/clip.webp`. One strip card showed the other video's frame, and
  `DELETE /videos/{id}` on either unlinked the poster the survivor still pointed at.

Reproduced at the service level before the fix:

```
rescan result: {'videos_added': 2, 'videos_missing': []}
  clip.mkv -> .../videos/thumbnails/clip.webp
  clip.mp4 -> .../videos/thumbnails/clip.webp
distinct posters: 1 of 2
files in thumbnails: ['clip.webp']
```

No error, no failed job — both rescans reported success.

### Root cause

Thumbnails and posters are `.webp` keyed by the media file's stem, so two files differing
only in extension map to one derived path. Every creation path in the app avoids this by
**picking** a free name through `unique_filename_with_thumb`, which rejects a candidate
whose stem is already occupied.

The two rescan functions do not pick a name — they **adopt** whatever is on disk, because
they are reconciling files the user placed. The "pick a name" step that naturally hosts the
guard does not exist there, so neither ever acquired one. `rescan_dataset` derived the
thumbnail with a bare `thumbnail_path_for(f)`; `_rescan_videos` built
`videos/thumbnails/{f.stem}.webp` inline and never constructed an occupied-stem set at all.

The image half had been latent since rescan existed. The video half was dormant through
Phase 0 — no posters were written, so nothing collided — and went live the moment Phase 1
started writing them at ingest.

### Generalizable rule

**Flag any path that writes a stem-keyed derived artifact (thumbnail, poster, sidecar)
without a collision guard — and note that "picks a filename" and "writes a derived file"
are different sets.** A reviewer checking only that `unique_filename_with_thumb` is used
will pass a path that never picks a name. Ask instead: *does this code write a file whose
name is derived from another file's stem, and could two inputs produce the same stem?*

Two follow-on rules:

- The fix direction depends on what re-derives the path. If many sites re-derive the
  derived path from the filename (images: eleven do), the **file** must be renamed so the
  stems stay equal. If every consumer reads a stored column (videos: `Video.poster_path`),
  rename the **derived file** and leave the user's file alone.
- A path registering a file that is *already in place* must pass `disk_exclude={f.name}`
  to `unique_filename`, or every file collides with itself and gets renamed to `_001`.

### Why it wasn't caught the first time

The occupied-stem reasoning was written down and then applied by pattern-matching on the
wrong feature. The arc's roadmap (since retired into the `docs/dev/video*.md` files)
recorded poster-stem collisions as a solved problem, and
`docs/dev/video.md` stated the rule as *"every site that picks a video filename does this:
upload, folder import and rename"* — which is literally true and complete for the sites it
names. Nobody asked whether a site could write a poster **without** picking a filename.

No test covered stem collisions across either rescan path; `test_rename_collisions_http.py`
covers only the HTTP rename/uniquifier paths, which were never broken. The failure is also
invisible in a passing suite and in normal use — it needs two files sharing a stem, which
only arises from hand-placed input, exactly the case rescan exists to serve.

### Fix

- `video_service.claimed_poster_stems(rows, poster_dir, exclude_id)` — one source of truth
  for occupied poster stems, unioning stored `poster_path` stems, `Video.filename` stems
  and on-disk `*.webp`. Replaced three hand-rolled constructions and supplied the two sites
  that had none.
- `utils.poster_path_for` / `utils.unique_poster_path` — the derivation and the
  counter-stepping resolver for paths that adopt a filename.
- `_rescan_videos` and `GET /videos/{id}/poster` resolve the poster stem through
  `unique_poster_path`; video files are never renamed.
- `rescan_dataset`'s image walk runs adopted names through `unique_filename_with_thumb`
  with `disk_exclude={f.name}` and renames the file, reporting the count as `renamed` in
  the job result and both rescan toasts. Scoped to flat `images/` files — nested files
  carry separate pre-existing defects and are untouched.
- Tests: `test_video_import_rescan_http.py` (three cases — collision, no-collision,
  upload-after-divergence), `test_video_serving_http.py` (backfill stem resolution),
  `test_http_smoke_jobs.py` (the image half).

**Recurrence, 2026-07-31 — the stem set was right but asked about the wrong directory.**
`routers/lut.py` and `routers/upscaling.py` built `occupied_thumb_stems` as one **flat** set
from `images[0]`'s `thumbnails/` dir, while `dest_images` is chosen per image inside the
loop and both jobs select on `Image.id.in_(...)` with **no dataset constraint**. A selection
spanning two datasets therefore asked one dataset's thumbnail directory about the other's
stems, and copy mode wrote a `.webp` over a live sibling's in whichever dataset did not seed
the set. `db_names` was already per image, which is why the derived *filename* looked right
while the thumbnail was clobbered — the same names-are-fine, bytes-are-wrong signature as
PM-017. Both now use the `occupied_by_dir` / `planned_by_dir` dicts that
`routers/detection.py` already had; tests in `test_lut_replace_extension_http.py` and
`test_upscale_png_fallback_http.py`. The review question this adds: when a guard is built
*once before* a loop but the thing it guards is chosen *inside* it, ask what makes them
agree — here, nothing did.

### Status & date

MITIGATED — the guard now exists at both rescan paths, but the class is still reachable by
any *new* path that writes a derived file without picking a name; only review catches that.
Found in code review of the `experimental-video-support` branch, not in production.
Last reviewed for staleness: 2026-07-27.
