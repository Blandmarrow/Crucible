# Re-extracting frames at full resolution

**Experimental.** Re-extraction is the second pass over a video's frames. Once you have curated a triage [extraction](video-extraction.md), it cuts the frames you kept again from the original video, this time at full size. This page covers where it is offered, which frames it skips and why, its format and size settings, and what happens to the frames' captions and scores. It needs the same optional packages as extraction; see [Optional packages](video-extraction.md#optional-packages). The videos themselves are covered in [Videos](video.md).

Extraction is meant to be done twice. The first pass writes small frames on purpose — a
long episode can produce hundreds, and a 1024px frame is enough to score it, spot
duplicates and decide what is worth keeping. Once you have thrown the rest away,
**Re-extract** goes back to the video and cuts the survivors again at full size.

It is not an upscale. Crucible recorded the exact moment each frame came from, so it seeks
back to that moment in the original file and decodes it fresh, replaying the same crop and
deinterlacer the first pass used. The frames are replaced in place: same images, same
subfolder, same captions and scores, just bigger.

Three places offer it: select frames in the gallery and press **Re-extract**; open a single
frame and use the **re-extract** link on the line that names its source video; or, on a
video's page, use the scissors beside any row under **Extracted frames** to do a whole
batch at once.

The dialog first says what it can actually do — *"38 frames from 2 videos will be
re-extracted · 3 skipped (already edited in place)"* — because not everything selected can
be. A frame is skipped when it did not come from a video, when its source video has been
deleted or moved off disk, when it has since been cropped, upscaled or graded in place —
those pixels are no longer the extracted frame, and re-cutting would silently throw the
edit away — when Crucible has no recorded timestamp for it, or when its source video is
already being extracted, which you will hit by starting an extraction and then re-extracting
that video's frames. Selecting ordinary images alongside frames is harmless; they are simply counted
as skipped.

Two choices: **JPEG** or **PNG** (lossless, and larger), and a **max long edge** left empty
for the video's native resolution. Choosing PNG changes the file extension; nothing else
moves — captions and thumbnails stay attached, and the old file is removed.

One run covers at most **5000 frames**. Past that the dialog asks you to narrow it — to a
single subfolder, or to a selection made in the gallery — rather than starting a job whose
size nothing on screen could report honestly.

Closing the dialog while a run is going is safe, and the dialog says so. Escape, the **✕**
in its corner and the **Close** button all work mid-run; the re-extraction is a background
job and carries on without the window. Reopening it on the same frames picks the progress
bar back up, and whether or not you are watching, the gallery, the open image and the
video's **Extracted frames** list all refresh themselves when the run finishes.

Quality scores are deliberately left alone, since they were measured on the small frames.
The dialog says so and so does the completion message; re-run scoring if you want numbers
that describe the full-resolution images. If you took a snapshot beforehand, restoring it
brings the original small frames back.
