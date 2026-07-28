# Videos & frame extraction

**Experimental.** Videos are **sources**, not gallery images. A video you add is stored separately from the dataset's images, in its own `videos/` folder, and is kept out of the counts you see on a dataset card: an image count never includes videos, and a dataset's size never includes them either. The point of holding a video is to extract frames from it; those frames become ordinary images and can be scored, captioned and exported like any others — see [Datasets & Gallery](gallery.md) for everything that then applies to them.

## Adding videos

- Drop a video onto the gallery, or pick one with the upload button — `.mp4`, `.mkv`, `.webm`, `.mov` and `.avi` are accepted. A file that cannot be decoded is rejected with a message rather than stored broken
- Tick **Include videos** in the import dialog to bring videos in with a folder import. It is off by default, so importing a mixed folder into an image dataset never quietly copies gigabytes of video. Videos always land flat — the subfolder and **Preserve structure** options apply to images only
- **Rescan** also registers any video dropped straight into the dataset's `videos/` folder, and reports videos whose files have gone missing. Videos are never renamed, even when two of them differ only by extension — their poster frames are given distinct names instead

## Browsing them

A dataset's videos appear in a **Videos** strip above the image grid, each card showing a poster frame taken from the middle of the clip and its length. Collapse the strip with the header arrow — it stays that way for that dataset. A dataset with no videos shows no strip at all. Videos are also counted on the dataset card and in the gallery header, separately from images.

Tick the checkbox on a card to select it; shift-click a second card to select the run between them. With anything selected the strip header offers **Extract frames**, which runs the same settings across every selected video at once. **Clear** drops the selection, and it clears itself when you switch datasets.

Click a card to open the video: it plays inline with a scrubber, and the panel beside it lists dimensions, length, frame rate, codec, file size and licence. A length that the file's header does not record honestly shows **—**, never `0:00`. **←** and **→** move between the dataset's videos, and the pencil beside the filename renames one — the extension is always kept, since it tells the browser how to play the file, and a name already in use gets a numeric suffix rather than overwriting anything.

**Delete video** removes the video and its poster. Frames already extracted from it are ordinary images and are deliberately left alone — the confirmation says how many there are; they keep their files and only lose the link back to the video.

## Extracting frames

**Extract frames** — on the video's own page, or on the strip's header for a selection — opens a two-step dialog. A batch shares one set of settings, so the first step previews the first video and says how many it covers.

**Step 1 · Source** samples the clip and shows a filmstrip; click any sample to bring it into the large preview. Over that preview sits an adjustable crop — drag any of the four edges, or type the numbers underneath. **Use detected** applies the letterbox matte Crucible found, alongside how many of the samples agreed on it, so a weak guess does not read as a certainty; **Clear crop** takes the whole frame. Below are a deinterlacing toggle and a trim bar for cutting a head and a tail — a leader or an end card. Anything worth knowing about the file appears here too: whether it looks interlaced, whether it is telecined (detected, not corrected), and whether some samples failed to decode. A clip whose container will not seek shows the trim bar disabled and says why. Adjusting the trim re-samples the clip after a short pause.

A clip that will not sample at all can still be extracted. The dialog says so, names what is missing — the crop preview, the detected matte and the interlace warnings — and carries on with whatever crop, deinterlacer and trims the video already has.

In a batch, the crop, deinterlacer and trim shown in step 1 belong to the previewed video. Each is applied to the whole batch **only if you change it** — leave a control alone and every video keeps its own setting.

**Step 2 · Extract** decides what is cut and where it lands. Choose how many frames to take from each shot and whether to take the **sharpest** of several candidates or simply the **middle** one, the long edge to resize to, and how sensitive cut detection should be. **Detector tuning** hides the settings that trade accuracy for speed on a long file — the shortest shot worth keeping, how many frames to skip between checks, and a ceiling on the number of shots.

Frames land in a subfolder, and there are three ways to place them:

- **New subfolder** (the default) names one after the video, stepping to `clip_2` if that name is taken
- **Add to …** puts them alongside the frames from this video's previous run
- **Replace** deletes that previous run first — the button says how many frames that is. They are deleted properly, so a snapshot taken beforehand can still restore them

You can type a name instead of taking the automatic one in either mode. **Add to …** additionally lets you pick any subfolder that already holds images — including, through *Automatic*, the dataset root, if that is where this video's last run went. **New subfolder** offers no such list on purpose: it always steps a name that is taken, so choosing an existing folder there would quietly produce `foo_2` rather than adding to `foo`. To put a whole batch into one shared folder, use **Add to …** and name it.

## While it runs, and afterwards

Extraction runs in the background, one job per video, and frames appear in the gallery as they are written rather than all at once at the end. Closing the dialog is safe: the run continues, the video's page keeps showing its progress, and reopening the dialog — or reloading the page — picks the run back up, showing a progress bar above the settings so you can watch or cancel it while configuring another video. A video that is already extracting is not started twice; if it is part of a new selection, its existing run is shown alongside the ones just started.

Once a video has produced frames, its page lists them under **Extracted frames**: how many went into each subfolder, most recent first, each one a link that opens the gallery at that subfolder. Every extracted image records where it came from, and its detail page shows a line naming the video, the timestamp within it and the shot number — so a frame filed away somewhere else can still say where it began.

Above those subfolder rows sits **Show all N frames**. The two are not the same question. A subfolder row answers *where did this extraction land*, and stops being useful the moment you move a frame out of it — which is most of what curation is. **Show all N frames** opens the gallery filtered on the video itself, so it finds every frame that video ever produced no matter which subfolder it now sits in or what it has been renamed to. The same filter is a **Frames from** dropdown in the gallery's own toolbar (only shown for datasets that have videos), and the lineage line on any frame's detail page carries an **all frames** link back to it. It survives a reload; **Reset filters** clears it.

Frames also make one scorer more useful than it is for ordinary images. A triage pass drops hundreds of them into a subfolder, and the first thing you want to know is which are of usable brightness — a video has night scenes, fades to black and blown-out flashes in a way a curated image set usually does not. Running the **Technical** scorer records a brightness value for every image, which shows up as a *Brightness* histogram on [Statistics](statistics.md), a **Brightness** score filter and a sort order in the gallery, and a row on the image detail panel.

## Optional packages

Two things need optional packages that `manage.sh update` / `manage.ps1 update` installs. Without them the dialog says so rather than failing: deinterlacing is switched off and unavailable, and without cut detection frames are sampled at fixed intervals instead of at shot boundaries. A video that had deinterlacing switched on before the package went missing is a special case — the dialog warns that extracting now will run without it *and* forget the setting, so you would switch it back on once the package is installed.
