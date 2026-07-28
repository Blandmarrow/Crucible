# Video frame heuristics

The judgement calls frame extraction stands on: where the black bars are, whether the source
is interlaced or telecined, how sharp a frame is, and which of several candidates to keep.
All of it lives in `services/video_frames.py` and is **pure numpy** — an `ndarray` in, a
number or a rect out, no decoder — which is why it gets its own module and its own file here.
That is the whole point of the boundary: every rule below is testable in milliseconds against
synthetic arrays (`test_video_frames.py`), which is the only way heuristics like these get
tested at all. Everything that needs cv2, PySceneDetect or ffmpeg — sampling, shot detection,
rendering, the endpoints and the job — is in `docs/dev/video-extract.md`.

A crop rect is `(x, y, w, h)`, matching the `Video.crop_*` column order — note that
PySceneDetect's `SceneManager.crop` is *inclusive corners* `(x0, y0, x1, y1)` instead, and
`video_extract` converts at that boundary. `clamp_crop` is the single source of truth for the
even-snap and the no-op rule, and it is called twice on the way to a pixel: once against the
header dimensions, once per frame against `frame.shape` — see `docs/dev/video-extract.md`
§ Rendering a shot.

**Cropdetect.** `edge_profiles` takes the **95th percentile** luma per row and per column,
not the max: one hot pixel or a stuck chroma sample inside an otherwise black bar pins that
row as content, and one such pixel per bar defeats detection entirely on real files.
`merge_profiles` accumulates across samples by **elementwise max**, so a dark shot can only
grow the content rect, never shrink it — averaging would let one night scene crop away real
picture in every other shot. `crop_rect_from_profiles` returns **None** rather than a rect
whenever the evidence is weak: nothing clears the threshold (a fade-to-black sample set),
the surviving content is under half of either axis, or the bars are thinner than
`min_bar_frac` (1.5% of the axis, measured as the two bars *combined*). Each axis decides
independently, so a pillarboxed 4:3 insert in a 16:9 frame is handled. All four edges snap
to even coordinates: chroma subsampling wants it, and `bwdif` needs an even `y` or it
deinterlaces with the field parity inverted.

**Combing.** `combing_ratio` is `d1/d2`, where `d1 = mean|L[1:] − L[:-1]|` compares
neighbouring rows — which in interlaced material come from two fields captured 1/50 s apart
— and `d2 = mean|L[2:] − L[:-2]|` compares rows of the *same* field and so measures ordinary
vertical detail. Progressive sits around 0.5–0.65; interlaced with motion exceeds ~0.9. It
returns 0.0 when `d2` is essentially zero, and that is not a divide-by-zero guard: identical
same-parity rows mean there is no second field for the first to disagree with, so a
synthetic 1-pixel stripe pattern reads as *no evidence* rather than as an unbounded ratio.

`interlace_from_series` needs **two** samples over threshold, not one — a single combed
sample is far more often a pinstripe shirt or a picket fence than a source — and counts
rather than averages, because a static interlaced shot has no field mismatch at all and
would drag a mean below threshold on an obviously interlaced file. Frame height and fps
corroborate in the **warning text only**: a 1080p25 file is not interlaced for being
1080p25, and folding that into the verdict would flag a large class of progressive material.

**Telecine** needs *consecutive* frames, so the probe runs it as a short second pass (20
frames from one seek) — a period-5 pattern is invisible to samples taken seconds apart. It
is gated on ~29.97 fps, binarizes the ratios, and requires a duty cycle in [0.3, 0.5] and a
lag-5 autocorrelation over 0.6. An all-combed run has zero variance and so zero
autocorrelation, which is the right answer: that is plain interlace, and reporting it as
telecine would recommend the wrong fix. It drives a **warning string only** — `bwdif` is the
one filter this phase ships.

**Sharpness** is Laplacian variance of the luma plane measured at a fixed resolution, and
**the downscale before the Laplacian is a correctness fix, not an optimization**. Raw
variance on a full-resolution frame ranks *noise* as sharpness, so a grainy candidate
outscores a crisp one — exactly backwards for a "pick the sharpest frame" policy. Averaging
into a fixed grid first removes the per-pixel component and leaves real edges, and it makes
scores comparable between a 4K source and a 480p one. `test_video_frames.py` pins the
ordering with pure Gaussian noise; deleting that line fails a test.

**`pick_index` rejects before it ranks.** First anything `is_degenerate` flagged (mean luma
under 8 or over 247, or standard deviation under 3 — black and white flashes, fades, flat
slates; a slate is often the *sharpest* thing in a shot, so without this the policy actively
prefers it). Then any candidate whose brightness deviates more than 40% from the candidate
set's median — that one is not about picture quality, it means the detector missed a cut
*inside* the window and keeping the outlier would file a frame from the next scene under
this shot's index. If everything is rejected it returns the middle rather than nothing: a
shot that is entirely a fade still owes the caller one frame, and a "no pick" return grows
a second, untested branch in every caller.
