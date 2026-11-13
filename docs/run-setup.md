# Setting up a run

This page covers the start form of an automated run: the caption and export settings,
checking a run with a dry run before you start it, and saving the form as a recipe. For
what each stage does, how to follow a run and what to do when one stops, see
[Automated Runs](runs.md). To make a run repeat until every folder is full, see
[Repeating a run](repeating-runs.md).

## Caption and export settings

When you switch on **Caption** or **Export**, the form shows the same settings as the
Captioning and Export pages, so you can set them for this run:

- **Caption:** the model (for LM Studio or another provider, pick the model from its
  list), the prompt or one of your saved prompt presets, the style, target resolution,
  how existing captions are handled, quality filters and the post-processing options.
- **Export:** the format, output folder, concept and repeats, caption file, filters,
  resize, image format, stripping metadata, and captions only.

The export's **Has caption** filter is ticked to start with, so an image with no caption
is left out. A custom resize is a whole number from 1 to 16384; a blank one counts as 1024.

Each section has a **Fill from the Captioning page** or **Fill from the Export page**
button. It copies what that page last used, and you can change anything after. If the
Captioning page's **Include current caption** box was ticked, the copied prompt already
contains `{previous_caption}` (see [Building on the current caption](captioning.md#building-on-the-current-caption)). Without
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
- the stages, and what the first pass would do: the rows it generates and the images that
  should make, an estimate of the disk space needed, and about how many rewrite and
  caption calls it makes (marked when the provider is billed);
- each folder, with how many images it holds against its target and how many rows the
  first pass makes for it, and a total.

Press **Close** to hide the report.

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
and the page says what to choose. It is also left empty, with a note saying
*ambiguous*, when two providers here have names that differ only in capital letters.

Each past run also has **Use these settings**, which fills the form with what it ran with
(see [Following a run](runs.md#following-a-run)).
