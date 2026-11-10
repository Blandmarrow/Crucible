# Automated Runs

A run takes a ComfyUI plan all the way to a training-ready export in one press. It fills
every folder of the plan's blueprint that is short, generates the images, scores them,
captions them and exports the dataset, one stage after another, while you do something
else. It can also repeat: generate, check each image against a quality bar, and go round
again until every folder holds enough images that pass (see
[Repeating until every folder is full](repeating-runs.md)).

Open it from **Runs** in a dataset's sidebar. A run works on a plan you have already set
up on the ComfyUI page, with a workflow, a pinned prompt (★) and either queued rows or a
blueprint. See [ComfyUI Generation](comfyui.md) and [ComfyUI Blueprints](comfy-structured.md).

The start form's caption and export settings, the dry run and saved recipes are on
[Setting up a run](run-setup.md). Repeating a run, with its caps, is on
[Repeating a run](repeating-runs.md).

## The stages

They always run in this order. A new form starts with the blueprint fill, the two VRAM
stages, Generate and Score on. **Caption** and **Export** start off, so switch them on
when you want them. Switch off any stage you do not want.

| Stage | What it does |
|---|---|
| **Snapshot the dataset** | Off by default. Takes a version of the dataset before the run changes anything, so you can go back to it on the Versions page. It runs once, before the first pass. With versioning off in Settings it is skipped, and the run says so. |
| **Fill the blueprint** | The same as **Top up** on the Blueprint tab: adds the rows each short folder needs, and puts failed rows back in the queue. With no blueprint it is skipped, and the run generates the plan's pending rows instead. |
| **Rewrite prompts** | Off by default, and it needs **Fill the blueprint**, because it rewrites the rows the fill adds. Rewrites the new template prompts into prose with a language model before generating, for the scenarios you tick (see below). |
| **Unload the rewrite model** | Frees the GPU from the model that rewrote, so ComfyUI has it. Only runs with Rewrite. A Strata server is unloaded only with Settings → ComfyUI's **Unload a local Strata server** on ([details](comfyui.md#setup)); it loads the model again by itself on the next pass's rewrite. |
| **Free VRAM for ComfyUI** | Unloads Crucible's own models (captioners, scorers), so ComfyUI has the GPU. With Settings → ComfyUI's **Unload a local Strata server** on, it also unloads a Strata LLM server on this computer. |
| **Generate** | Runs this run's rows through ComfyUI, filing each image in its row's folder, under the base folder if you set one. |
| **Free ComfyUI's VRAM** | Asks ComfyUI to unload its models, so Crucible's scorers and captioners have the GPU. If your ComfyUI cannot do that, the run notes it and carries on. |
| **Score** | Scores the images this run made. Technical scores always run, because they are what finds duplicates. The aesthetic score is optional. |
| **Gate** | Off by default, and only with Score. Checks each new image against the bar you set and moves the ones that fail to a `rejects` folder (see below). |
| **Caption** | Captions the images this run made, with the settings in [Setting up a run](run-setup.md#caption-and-export-settings). **Caption images that already have a caption** is on by default, because a ComfyUI run already saves each prompt as the image's caption. |
| **Unload the caption model** | Frees the GPU from the model that captioned: an LM Studio model, an Ollama model, or one of Crucible's own captioners. On by default, and it only runs with Caption. LM Studio needs version 0.4 or later for this; with an older one the run notes that the model stays loaded and carries on. |
| **Export** | Exports the whole dataset to the folder you choose, with the settings in [Setting up a run](run-setup.md#caption-and-export-settings). Duplicates, watermarked images and images a gate rejected are left out by default. |

A stage with nothing to do is **skipped**, not run on everything. If a run made no new
images, scoring and captioning are skipped, and no other image in the dataset is touched.

A language model is unloaded by the two stages above: after rewriting, and after
captioning. If one is already loaded in LM Studio when the run starts, it stays loaded
through generating and scoring unless Rewrite is on.

## The gate

The gate decides which images count. Choose what it rejects:
- **Blurry, noisy, near-uniform or duplicate** images. These four are ticked by default.
- **Watermarked** or **NSFW** images. Ticking these makes the score stage run those
  checks too.
- **An aesthetic score below** a number, when the aesthetic score is on.

An image whose pixels changed after it was scored always fails.

A rejected image is **moved, not deleted**. It goes to `rejects/` under the run's base
folder, keeping its folder's path: `knights/iron_knight` becomes
`rejects/knights/iron_knight`. It is also flagged **Rejected by a run's gate**, which is
what keeps it out of its folder's count, the captions and the export. You can filter the
gallery by that flag. The flag goes when the image leaves `rejects/`: move it back to a
folder, copy it to another dataset, or rename or delete the `rejects` folder, and the
image counts again.

## Rewriting inside a run

If some of the blueprint's scenarios have **Rewrite into prose** on, switch on **Rewrite
prompts** and tick the scenarios the run may rewrite. Ticking them is your review, the
same as in the Rewrite dialog. Pick the provider and model. Each pass rewrites only the
rows it is about to generate.

If a folder's rewrite fails, that folder sits out the pass: its rows wait as template
prompts and are never generated as they are. In a repeating run the next pass tries again,
and a folder that keeps failing is given up on like any other. A run that does not repeat
fails if the rewrite changed nothing, so you can fix the provider and resume. A scenario with rewrite on that you did not
tick still pauses the run, as before.

## Following a run

While a run is going, the page shows each stage with its status (pending, running, done,
skipped, failed or cancelled) and a short summary, such as *4 rows generated* or *12 images
exported to D:\train\knights*. Each stage's job also shows in the top bar, with the run's
name in front of its label.

- **Pause** lets the current stage finish, then stops before the next one.
- **Resume** goes on from the first stage that is not done. Nothing already done runs
  again, and a generated row is never generated twice. A Caption stage that stopped
  part-way captions only the images it had not reached, plus any whose caption failed;
  an image that still carries its caption from that model is not sent again. A caption
  you wrote or fixed by hand in the caption editor is never replaced, so you can correct
  captions before you resume.
- **Cancel** stops the current stage's job, and no later stage starts. Images already made
  stay in the dataset. A snapshot cannot stop partway, so a cancel during one waits for it
  to finish, and the run reads *cancelling after the snapshot* until then. Cancel stays
  available meanwhile: if the snapshot has already finished or is gone, pressing it again
  ends the run at once.

A paused run holds no job, so two things show it. On the ComfyUI page its plan gets an
amber dot in the plan list, and a chip in the top bar ("Run paused") links to the Runs page
on every page of the app. Crucible pauses every running run when it restarts, so look for
the chip after a restart.

A dataset can have one run going or paused at a time. While a run on a plan is going or
paused, that plan's **Apply** and **Top up** buttons are refused, so nothing adds rows
between its passes. The ComfyUI page greys out the other things that would be refused, with
the reason as a tooltip: **Delete** on the plan, **Add row**, **Paste prompts**, **Import .txt**,
deleting rows, **Add to plan** in the library, and copying or moving rows into the plan. A repeating run shows its stages grouped by pass.

Finished runs are listed under *Past runs*. Each has **Delete**, **Use these settings**,
**Resume** if it failed, and **Raise caps and continue** (see [Repeating a run](repeating-runs.md)) if a cap stopped it. The last
three are not offered while a run is going, since the start form is hidden then. A
run that could not start its first stage shows its error instead of "Run started".

## When a run stops on its own

- **A stage failed.** The run says which stage and why. Fix the cause, then press
  **Resume** on it under *Past runs*.
- **Every item of a stage failed.** If Generate failed every row, or Caption failed every
  image, the run fails with a message such as *Generate: every row failed (5 of 5)* and the
  first row's error. A stage where only some items failed still counts as done. **Resume**
  tries the failed rows again.
- **You cancelled one of its jobs from the top bar.** The run treats that as a failure, so
  you can resume it.
- **Crucible restarted.** The run comes back **paused** at the stage it was on. Press
  **Resume** when you are ready; it never resumes by itself.
- **Rows wait for a rewrite.** If a scenario of the blueprint has its prose rewrite on, the
  run pauses before generating, so no template prompt is generated by mistake. Run the
  rewrite on the ComfyUI page, then resume the run.

## Limits for now

- A run that gave up on its folders, rather than stopping at a cap, cannot carry on (see
  [Giving up on a folder](repeating-runs.md#giving-up-on-a-folder)). Start a new run: it picks up where the folders stand, and makes only what is still missing.
- A snapshot is taken once, at the start. A run resumed after its snapshot failed takes
  a new one.
