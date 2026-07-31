# Videos & frame extraction

**Experimental.** Videos are **sources**, not gallery images. A video you add is stored separately from the dataset's images, in its own `videos/` folder, and is kept out of the counts you see on a dataset card: an image count never includes videos, and a dataset's size never includes them either. The point of holding a video is to extract frames from it; those frames become ordinary images and can be scored, captioned and exported like any others — see [Datasets & Gallery](gallery.md) for everything that then applies to them.

## Adding videos

- Drop a video onto the gallery, or pick one with the upload button — `.mp4`, `.mkv`, `.webm`, `.mov` and `.avi` are accepted. A file that cannot be decoded is rejected with a message rather than stored broken
- Tick **Include videos** in the import dialog to bring videos in with a folder import. It is off by default, so importing a mixed folder into an image dataset never quietly copies gigabytes of video. Videos always land flat — the subfolder and **Preserve structure** options apply to images only
- **Duplicate dataset** offers a **Copy N videos** checkbox showing how much disk that costs. It is off by default, unavailable when duplicating from a snapshot — snapshots do not capture videos — and refused up front if there is not room
- **Rescan** also registers any video dropped straight into the dataset's `videos/` folder, and reports videos whose files have gone missing. Videos are never renamed, even when two of them differ only by extension — their poster frames are given distinct names instead

## Browsing them

A dataset's videos appear in a **Videos** strip above the image grid, each card showing a poster frame taken from the middle of the clip and its length. A clip whose frames will not decode still lists and plays; its card shows a film glyph instead of a poster, and Crucible tries again a few minutes later. Collapse the strip with the header arrow — it stays that way for that dataset. A dataset with no videos shows no strip at all. Videos are also counted on the dataset card and in the gallery header, separately from images.

Tick the checkbox on a card to select it; shift-click a second card to select the run between them. With anything selected the strip header offers **Extract frames**, which runs the same settings across every selected video at once. **Clear** drops the selection, and it clears itself when you switch datasets.

Click a card to open the video: it plays inline with a scrubber, and the panel beside it lists dimensions, length, frame rate, codec, file size and licence. A length that the file's header does not record honestly shows **—**, never `0:00`; so does anything longer than 24 hours, which Crucible treats as unmeasured. **←** and **→** move between the dataset's videos, and the pencil beside the filename renames one — the extension is always kept, since it tells the browser how to play the file, and a name already in use gets a numeric suffix rather than overwriting anything.

A video stays in the dataset it was added to. **Move to dataset** carries images only, so a video changes dataset by being added again.

**Delete video** removes the video and its poster. Frames already extracted from it are ordinary images and are deliberately left alone — the confirmation says how many there are; they keep their files and only lose the link back to the video.

## Extracting frames

**Extract frames** — on the video's own page, or on the strip's header for a selection — opens a two-step dialog. A batch shares one set of settings, so the first step previews the first video and says how many it covers. One run covers at most **50 videos**; select fewer if the strip holds more.

**The first step** samples the clip and shows a filmstrip; click any sample to bring it into the large preview. Over that preview sits an adjustable crop — drag any of the four edges, or type the numbers underneath — a number you type is left exactly as you typed it until you leave the field, which is when it settles to the nearest allowed value. **Use detected** applies the letterbox matte Crucible found, alongside how many of the samples agreed on it, so a weak guess does not read as a certainty; **Clear crop** takes the whole frame. Below are a deinterlacing toggle and a trim bar for cutting a head and a tail — a leader or an end card. Anything worth knowing about the file appears here too: whether it looks interlaced, whether it is telecined (detected, not corrected), and whether some samples failed to decode. The trim handles take keyboard focus and move with the arrow keys — 500 ms a press, 5 seconds with Shift. A clip whose container will not seek shows the trim bar disabled and says why. Adjusting the trim re-samples the clip after a short pause. If a trim saved from an earlier run no longer fits the clip — its measured length having since been corrected downwards — that is said here rather than left to fail at extraction time.

A clip that will not sample at all can still be extracted. The dialog says so, names what is missing — the crop preview, the detected matte and the interlace warnings — and carries on with whatever crop, deinterlacer and trims the video already has.

In a batch, the crop, deinterlacer and trim shown in step 1 belong to the previewed video. Each is applied to the whole batch **only if you change it** — leave a control alone and every video keeps its own setting. A crop can only cover a batch whose videos are all the **same size**: mix resolutions, or mix sampled with never-sampled videos, and Crucible names the offending files and asks you to extract them separately or clear the crop.

**Next** moves to the second step, which decides what is cut and where it lands (**Back** returns). Choose how many frames to take from each shot and whether to take the **sharpest** of several candidates or simply the **middle** one, the long edge to resize to, and how sensitive cut detection should be. **Detector tuning** hides the settings that trade accuracy for speed on a long file — the shortest shot worth keeping, how many frames to skip between checks, and a ceiling on the number of shots. Every number here behaves the way the crop numbers do: what you type is left exactly as typed until you leave the field, which is when it settles to the nearest allowed value — so typing `2048` into **Long edge** gives you 2048.

Frames land in a subfolder, and there are three ways to place them:

- **New subfolder** (the default) names one after the video, stepping to `clip_2` if that name is taken
- **Add to …** puts them alongside the frames from this video's previous run
- **Replace** deletes that previous run first — the button says how many frames that is. They are deleted properly, so a snapshot taken beforehand can still restore them

You can type a name instead of taking the automatic one in either mode. **Add to …** additionally lets you pick any subfolder that already holds images — including, through *Automatic*, the dataset root, if that is where this video's last run went. **New subfolder** offers no such list on purpose: it always steps a name that is taken, so choosing an existing folder there would quietly produce `foo_2` rather than adding to `foo`. To put a whole batch into one shared folder, use **Add to …** and name it.

## While it runs, and afterwards

Extraction runs in the background, one job per video, and frames appear in the gallery as they are written rather than all at once at the end. Two things can quietly change what it cuts, and the progress line says so when they do: a clip with no detectable cuts — a single long take, a static camera — or one shot running longer than two minutes is sampled at **fixed intervals** instead; and a clip whose container reports no length at all yields one window, so only a single frame comes out of it whatever *frames per shot* says. Closing the dialog is safe: the run continues, the video's page keeps showing its progress, and reopening the dialog — or reloading the page — picks the run back up, showing a progress bar above the settings so you can watch or cancel it while configuring another video. A video that is already extracting is not started twice; if it is part of a new selection, its existing run is shown alongside the ones just started.

Once a video has produced frames, its page lists them under **Extracted frames**: how many went into each subfolder, most recent first, each one a link that opens the gallery at that subfolder. Every extracted image records where it came from, and its detail page shows a line naming the video, the timestamp within it and the shot number — so a frame filed away somewhere else can still say where it began.

Above those subfolder rows sits **Show all N frames**. The two are not the same question. A subfolder row answers *where did this extraction land*, and stops being useful the moment you move a frame out of it — which is most of what curation is. **Show all N frames** opens the gallery filtered on the video itself, so it finds every frame that video ever produced no matter which subfolder it now sits in or what it has been renamed to. The same filter is a **Frames from** dropdown in the gallery's own toolbar (only shown for datasets that have videos), and the lineage line on any frame's detail page carries an **all frames** link back to it. It survives a reload; **Reset filters** clears it.

Frames also make one scorer more useful than it is for ordinary images. A triage pass drops hundreds of them into a subfolder, and the first thing you want to know is which are of usable brightness — a video has night scenes, fades to black and blown-out flashes in a way a curated image set usually does not. Running the **Technical** scorer records a brightness value for every image, which shows up as a *Brightness* histogram on [Statistics](statistics.md), a **Brightness** score filter and a sort order in the gallery, and a row on the image detail panel. Brightness is newer than the rest of the Technical scorer, so a dataset last scored before it existed has none of it recorded: the histogram, the filter and the sort all come up empty until you run Technical over that dataset again. Statistics says so on the panel rather than leaving you with a bare "No data".

## Re-extracting at full resolution

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

## Optional packages

Two things need optional packages that `manage.sh update` / `manage.ps1 update` installs. Without them the dialog says so rather than failing: deinterlacing is switched off and unavailable, and without cut detection frames are sampled at fixed intervals instead of at shot boundaries. A video that had deinterlacing switched on before the package went missing is a special case — the dialog warns that extracting now will run without it *and* forget the setting, so you would switch it back on once the package is installed. **Re-extraction inherits the same requirement**: a video whose saved deinterlacer needs the ffmpeg package is refused with a message rather than re-cut without it.
