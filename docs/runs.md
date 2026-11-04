# Automated Runs

A run takes a ComfyUI plan all the way to a training-ready export in one press. It fills
every folder of the plan's blueprint that is short, generates the images, scores them,
captions them and exports the dataset, one stage after another, while you do something
else. It can also repeat: generate, check each image against a quality bar, and go round
again until every folder holds enough images that pass (see
[Repeating until every folder is full](#repeating-until-every-folder-is-full)).

Open it from **Runs** in a dataset's sidebar. A run works on a plan you have already set
up on the ComfyUI page, with a workflow, a pinned prompt (★) and either queued rows or a
blueprint. See [ComfyUI Generation](comfyui.md) and [ComfyUI Blueprints](comfy-structured.md).

## The stages

They always run in this order. Switch off any you do not want.

| Stage | What it does |
|---|---|
| **Snapshot the dataset** | Off by default. Takes a version of the dataset before the run changes anything, so you can go back to it on the Versions page. It runs once, before the first pass. With versioning off in Settings it is skipped, and the run says so. |
| **Fill the blueprint** | The same as **Top up** on the Blueprint tab: adds the rows each short folder needs, and puts failed rows back in the queue. With no blueprint it is skipped, and the run generates the plan's pending rows instead. |
| **Rewrite prompts** | Off by default. Rewrites the new template prompts into prose with a language model before generating, for the scenarios you tick (see below). |
| **Unload the rewrite model** | Frees the GPU from the model that rewrote, so ComfyUI has it. Only runs with Rewrite. |
| **Free VRAM for ComfyUI** | Unloads Crucible's own models (captioners, scorers), so ComfyUI has the GPU. |
| **Generate** | Runs this run's rows through ComfyUI, filing each image in its row's folder, under the base folder if you set one. |
| **Free ComfyUI's VRAM** | Asks ComfyUI to unload its models, so Crucible's scorers and captioners have the GPU. If your ComfyUI cannot do that, the run notes it and carries on. |
| **Score** | Scores the images this run made. Technical scores always run, because they are what finds duplicates. The aesthetic score is optional. |
| **Gate** | Off by default, and only with Score. Checks each new image against the bar you set and moves the ones that fail to a `rejects` folder (see below). |
| **Caption** | Captions the images this run made, with the settings below. **Caption images that already have a caption** is on by default, because a ComfyUI run already saves each prompt as the image's caption. |
| **Unload the caption model** | Frees the GPU from the model that captioned: an LM Studio model, an Ollama model, or one of Crucible's own captioners. On by default, and it only runs with Caption. LM Studio needs version 0.4 or later for this; with an older one the run notes that the model stays loaded and carries on. |
| **Export** | Exports the whole dataset to the folder you choose, with the settings below. Duplicates, watermarked images and images a gate rejected are left out by default. |

A stage with nothing to do is **skipped**, not run on everything. If a run made no new
images, scoring and captioning are skipped, and no other image in the dataset is touched.

A language model is unloaded only after captioning, by the stage above. If one is already
loaded in LM Studio when the run starts, it stays loaded through generating and scoring.

## Caption and export settings

When you switch on **Caption** or **Export**, the form shows the same settings as the
Captioning and Export pages, so you can set them for this run:

- **Caption:** the model (for LM Studio or another provider, pick the model from its
  list), the prompt or one of your saved prompt presets, the style, target resolution,
  how existing captions are handled, quality filters and the post-processing options.
- **Export:** the format, output folder, concept and repeats, caption file, filters,
  resize, image format, stripping metadata, and captions only.

Each section has a **Fill from the Captioning page** or **Fill from the Export page**
button. It copies what that page last used, and you can change anything after. Without
it, the form starts from the usual defaults. A run keeps its settings once started, so
changing those pages later does not change a run in progress.

Loss masks, the license and label filters, and limiting the export to some subfolders are
only on the Export page for now.

## Checking before you start

Press **Dry run** to see what the run would do without doing it. Nothing is written and
nothing is generated. The report shows:
- every **problem** that would stop the start, such as ComfyUI not answering, a blueprint
  over the row limit, or a caption model your provider does not list;
- **warnings** that do not stop it, such as versioning being off with Snapshot on;
- the stages, and how many rows and images the first pass makes in each folder.

**Start run** makes the same checks and refuses on the first problem, before anything is
written. If you change the form after a dry run, the report says it is out of date.

## Saving the form as a recipe

The form is remembered between visits. Its plan and base folder are remembered for each
dataset.

To keep a set of settings, type a name under **Recipe** and press **Save as recipe**.
Recipes are shared by every dataset. Pick one and press **Load** to fill the form, or
**Rename** or **Delete** it. Loading keeps the plan and run name you have chosen.

**Export** saves a recipe as a file to share, and **Import…** reads one. A file never
carries your export folder, and it names each LLM provider instead of pointing at yours.
On import, a provider with the same name here is used. Otherwise that field is left empty,
and the page says what to choose.

Each past run also has **Use these settings**, which fills the form with what it ran with.

## Repeating until every folder is full

Tick **Repeat until every folder is full** to make the run go round in passes. Each pass
fills the blueprint, rewrites (if on), generates, scores and gates. When a pass ends, the
run counts each folder again. If a folder is still short, it starts another pass that
makes just what is missing. Captioning, unloading the caption model and the export run
once, after the last pass.

Only images that count fill a folder. An image flagged as a **duplicate**, or **rejected
by the gate**, does not count, so the next pass makes another. The ComfyUI page's preview
and its **Top up** button count the same way. Because some images fail, a pass makes a
few more than the folder is short, based on how many passed last time.

A repeating run needs:
- **Fill the blueprint** and **Generate** on, and a blueprint on the plan.
- **A random seed.** If the workflow has a `seed` or `noise_seed` input, pin it on the
  ComfyUI page with **Random**. Otherwise every pass would make the same pictures again,
  and they would all be rejected as duplicates. The run refuses to start and names the
  node.

### The caps

Every repeating run has hard limits, so it cannot run forever:

| Cap | What it limits |
|---|---|
| **Most passes** | How many times the run goes round. |
| **Most images** | How many images the run may make in all, rejects included. A pass that would go over makes fewer rows, shared out across the folders. A workflow that saves several images per prompt can go over on the first pass. |
| **Most minutes of work** | Time spent working. Time paused, or Crucible being shut down, does not count. It is checked before each costly stage, so a long generate stage can run a little over. |
| **Most paid LLM calls** | Shown only when a **billed** provider rewrites or captions. It counts rewrite requests and captioned images. When captioning is billed, it must be at least **Most images**, so there are always calls left to caption every image. |

A provider is **billed** when you tick that box for it in Settings → LLM Providers. Left
alone, a provider on another machine counts as billed and one on this machine does not.
Ollama and Crucible's own captioners are never billed.

When a cap runs out, the run stops making new images, still frees ComfyUI's VRAM, runs
captioning and the export, and says which cap stopped it. If the paid-call cap cannot
cover every image, the run captions as many as it can and says how many it left out.

To carry on, press **Raise caps and continue** on the run under *Past runs*. Raise the cap
that stopped it; no cap can go down. The run adds another pass for the folders still
short, then captions and exports again. Only the new images are captioned, and what the
run has already used still counts against the caps.

### Giving up on a folder

If a folder gains no images that count in two passes in a row, the run gives up on it and
fills the others. This catches a folder whose prompts never make a picture that passes.
A folder that simply got no rows in a pass, because the image cap was shared out, is not
given up on. The finished run lists each folder still short, and why.

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
gallery by that flag. Moving an image back by hand does not clear the flag.

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
  again, and a generated row is never generated twice.
- **Cancel** stops the current stage's job, and no later stage starts. Images already made
  stay in the dataset. A snapshot cannot stop partway, so a cancel during one waits for it
  to finish, and the run reads *cancelling* until then.

A dataset can have one run going or paused at a time. While a run on a plan is going or
paused, that plan's **Apply** and **Top up** buttons are refused, so nothing adds rows
between its passes. A repeating run shows its stages grouped by pass.

## When a run stops on its own

- **A stage failed.** The run says which stage and why. Fix the cause, then press
  **Resume** on it under *Past runs*.
- **You cancelled one of its jobs from the top bar.** The run treats that as a failure, so
  you can resume it.
- **Crucible restarted.** The run comes back **paused** at the stage it was on. Press
  **Resume** when you are ready; it never resumes by itself.
- **Rows wait for a rewrite.** If a scenario of the blueprint has its prose rewrite on, the
  run pauses before generating, so no template prompt is generated by mistake. Run the
  rewrite on the ComfyUI page, then resume the run.

## Limits for now

- A run that gave up on its folders, rather than stopping at a cap, cannot carry on. Start
  a new run: it picks up where the folders stand, and makes only what is still missing.
- A snapshot is taken once, at the start. A run resumed after its snapshot failed takes
  a new one.
