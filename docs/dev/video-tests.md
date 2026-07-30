# Video tests and the cv2 CI gate

The arc-wide index of which module pins which behaviour, and the rules that keep the suite
runnable both with and without OpenCV. It spans the subject matter of every other video doc —
`docs/dev/video.md` (model, storage, ingest), `docs/dev/video-endpoints.md`,
`docs/dev/video-decode.md`, `docs/dev/video-shots.md`, `docs/dev/video-heuristics.md`,
`docs/dev/video-extract.md` and `docs/dev/video-reextract.md` — which is why it is an index of
its own rather than a section inside any one of them. `docs/dev/video-reextract.md` § Tests and
`docs/dev/video-extract.md`'s coverage notes still live in their own files and are the next
candidates to land here.

## The modules

`test_media_types.py` (allowlist properties, the AVIF gate, codec fallback),
`test_video_probe.py` (the metadata ladder, every untrustworthy frame count, the ingest
gate), `test_video_ingest_http.py` (upload routing, stem collisions, stats separation and
the `GET /datasets/` video columns), `test_video_import_rescan_http.py` (the
`include_videos` default and its preflight, flat landing, the rescan pass),
`test_video_serving_http.py` (range requests, the poster backfill and its stem resolution,
the failure backoff, delete), `test_video_poster.py` (midpoint seek, both fallback rungs,
downscale, the atomic write) and `test_video_rename_http.py` (slugify, both collision
classes, the poster move, the busy 409). `test_http_smoke_crud.py` covers
`/filesystem/preview` for both media kinds, and `test_http_smoke_jobs.py` pins the image
side of the same stem-collision rule.

Extraction (`docs/dev/video-shots.md`, `docs/dev/video-extract.md`) adds four more.
`test_video_frames.py` is pure numpy and needs no fixture at all — cropdetect, combing,
telecine, sharpness, candidate rejection, all documented in `docs/dev/video-heuristics.md`. `test_video_extract.py` is service level, with
`scenedetect` skipped rather than required, and covers `measure_duration_ms` (including a
non-seekable stub), probe sampling and its caps, shot boundaries, the empty-list trap, both
uniform fallbacks, cancellation and `render_shot` geometry. `test_video_extract_http.py`
drives both endpoints end to end. `test_video_lineage_mirrors.py` holds the structural
mirror guard described in CLAUDE.md § Key invariants plus behavioural round-trips through
snapshot/restore, both `duplicate_dataset` branches, cross-dataset copy and move, and video
delete.

`test_frames_from_video_filter.py` pins the `GET /images/?source_video_id=` gallery filter
(`docs/dev/video-ui.md` § the lineage row) on the two properties that are its reason to
exist: the filter survives a frame **moved out of its extraction subfolder**, because the
subfolder stops being a handle the moment curation moves a file while the lineage column does
not, and the FK's `ondelete="SET NULL"` drops a deleted video's frames from the filter
**without destroying the rows** — a frame outlives its video, it just stops being addressable
this way. Both are cv2- and `scenedetect`-gated.

## Fixtures

Two fixtures join `mp4_bytes` in `conftest.py`. `mp4_shots_bytes` writes hard cuts between
distinctly-coloured shots and must stay at **320×240 with ≥24 frames per shot**:
`AdaptiveDetector` auto-sizes its edge kernel from the frame size and finds nothing at all
at 64×48, and `min_scene_len` merges anything shorter into its neighbour. `frame_colour()`
reads a written frame back to say which shot it came from, the video equivalent of
`test_video_poster.py::_grey`. `mp4_corrupt_bytes` is named for what it is rather than what
it was meant to be: truncating an `mp4v` file removes the `moov` atom, which
`cv2.VideoWriter` puts at the **end**, so `isOpened()` returns False and it is a
will-not-open fixture, not the mid-extraction one it was intended as. The circuit breaker is
tested by injecting failures instead, which is deterministic.

The rescan-collision rule is pinned in three separable parts, since each can break alone:
same-stem containers rescanned together get distinct posters; an ordinary rescan renames
*nothing*; and an upload after a disambiguation cannot take the poster stem whose filename
no longer matches it.

The repo ships no sample media, so `conftest.mp4_bytes()` synthesizes a real `.mp4` with
`cv2.VideoWriter`. Use the `mp4v` fourcc: `avc1` needs an h264 encoder the opencv-python
wheel does not carry and its writer silently fails to open. `VideoWriter` cannot write to a
buffer, hence the temp file.

## cv2 in CI, and the skip convention

`backend-tests.yml` installs opencv **and** scenedetect, in a step of its own after the
main install (which is `pip install -r backend/requirements-ci.txt -r backend/requirements-dev.txt`
— the pinned base file all three CI jobs share; cv2 stays out of it because e2e-smoke.yml
wants opencv *without* scenedetect, and nothing else wants either). Before that step
existed it installed neither, so the ~250 tests above had never once run in CI: three
modules errored at *collection* and the rest failed rather than skipped. Two rules keep
both halves working.

**Order and flags are load-bearing.** `pip install "opencv-python-headless>=4.9"`, then
`pip install --no-deps "scenedetect>=0.7.1"`, then its three remaining runtime deps
(`"click!=8.3.0,~=8.0"`, `platformdirs`, `tqdm` — the last two unpinned because scenedetect
itself declares them with no constraint, so a floor here would be invented).
scenedetect hard-requires `opencv-python` — the GUI wheel, ~90 MB,
linking `libGL.so.1`, which a headless runner does not have — and both wheels provide the
same `cv2` package, so this is the only combination that gets shot detection without the
GL-linked one. This is a **deliberate divergence** from `backend/requirements.txt`, which
pins the GUI wheel for real use; "fixing" CI back to it hits libGL. `imageio-ffmpeg` stays
out: no test needs the real `bwdif` path, and both 503 tests monkeypatch `capabilities`.

**Guard placement decides error vs skip.** A `pytestmark = skipif(...)` is consulted only
*after* the module body has run, so it cannot protect a module-level constant like
`SHOTS_MP4 = mp4_shots_bytes()` — those three modules need
`pytest.importorskip("cv2")` on the line immediately before it. Modules that only touch cv2
inside tests (`mp4_bytes` imports it lazily) take the same one-liner after the imports.
Where a module is *mostly* media-free, use a per-test `needs_cv2` mark instead: a
module-level skip in `test_video_lineage_mirrors.py` would drop the structural mirror guard,
and in `test_http_smoke_crud.py` / `test_conflict_paths_http.py` it would drop whole
unrelated sweeps. `test_video_frames.py` is guarded by **neither** — it is pure numpy
against `video_frames.py`, which has no cv2 import at all.

Verify both worlds after touching this:
`python -c "import sys; sys.modules['cv2']=None; import pytest; pytest.main(['backend/tests/','-q'])"`
must report skips and zero failures or errors.
