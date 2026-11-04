# ComfyUI Blueprints

A blueprint describes a whole dataset once (*"these characters, in these places, alone
and in pairs, so many images each"*) and turns it into a ComfyUI queue with one folder
per combination. You choose the structure. Crucible makes every combination and writes
every prompt, so each character is described the same way in every image they appear in.

It lives on the **Blueprint** tab of a plan on the ComfyUI page. The plan itself (the
workflow, the pins and running the queue) is described in [ComfyUI Generation](comfyui.md).
Apply needs a pinned parameter marked as the prompt (★).

## The three parts

**Axes** are the things your dataset varies over, such as *character* or *biome*. Each axis
holds entities. An entity has two parts:

- A **label**, such as *Iron Knight*. It becomes a folder name, so it needs at least one
  letter or digit.
- A **descriptor**, such as *a tall knight in blackened plate, red sash*. It is pasted word
  for word into every prompt that names the entity, so write it to stand on its own. A
  blank descriptor uses the label.

**Variations** are sets of interchangeable prompt pieces that change the shot without
changing who or where: *camera* (low angle, wide shot, close-up), *time* (dawn, dusk).
Enter one piece per line.

**Scenarios** combine axes into folders. Each has:

| Field | What it does |
|---|---|
| **Combine** | The axes this scenario uses. **Take** 2 or more on an axis makes groups of different entities together (pairs, trios), never the same one twice. |
| **Mode** | **Full** makes every combination. **Sample K** picks K of them at random, and keeps the same K each time you reopen it. |
| **Images per folder** | How many images each folder should end up holding. |
| **Folder path** | Where each combination is filed, e.g. `{biome}/{character}`. Leave it blank for one folder level per axis, in the order you combined them. Pairs become `iron_knight_and_salt_witch`. |
| **Prompt template** | E.g. `{character}, standing in {biome}, {camera}, {time}`. Leave it blank to list every axis, then one piece of every variation. |
| **Rewrite into prose (LLM)** | Off by default. Tick it to have an LLM turn this scenario's prompts into sentences before they run. See [Rewriting prompts into prose](#rewriting-prompts-into-prose). |

In a template, `{axis}` is the entity's descriptor, `{axis.label}` is its label, and
`{variation}` is one piece from that set. Each folder's prompts walk through the
combinations of variation pieces, so a folder's prompts differ from each other. If a folder
wants more images than there are combinations, the preview warns you that prompts will
repeat. They still make different images, because each run gets its own seed.

Names of axes and variations use letters, digits and `_`, like `main_character`.

Everything saves as you type.

## Drafting a blueprint from a description

**✨ Draft from a description…** asks an LLM to write the blueprint for you. Describe the
dataset in a sentence or two, for example *"a medieval set: six characters, each in four
biomes, alone and in pairs, ten images each"*. Then pick a provider from Settings → LLM
Providers → [details](settings.md), and click **Draft**.

The model invents the characters, their descriptors, the variation pieces and the
scenarios. It never lists the combinations itself; Crucible does that. It follows the
**Instructions** saved in the plan's **Generate prompts** settings when writing the
templates (tags or prose, say), and it never changes them.

- **It runs in the background**, like *Generate until N*. You can close the window or
  leave the page, and a message tells you when the draft is ready.
- **You can review the draft before you use it.** If the window is still open when the
  draft is ready, it shows the draft: every entity with its descriptor, the variations,
  each scenario's template, how many folders and rows it makes, any errors, and a few
  sample prompts. If you closed the window, a green banner waits above the editors.
  Press **Review…** there to see the same view.
- **Nothing changes until you choose.** **Use it** replaces your current blueprint, asking
  first if you have one. Then you edit it like any other: rename entities, cut a
  scenario, fix whatever the preview marks as an error. **Discard** throws the draft away,
  and **Draft again…** lets you change the description and try again.
- The draft never adds rows. Only **Apply** does.

## Preview and Apply

The **Preview** updates as you edit. It shows:

- how many folders the blueprint makes, and how many rows Apply would add
- each folder with its target, the images it already holds (**Kept**), the rows still
  waiting to run (**Open**), the rows to add, and sample prompts for the first 20 folders
- **errors** in red, which you must fix before Apply works: an unknown placeholder, two
  scenarios making the same folder, a label with no letters
- warnings, such as repeating prompts

**Apply** adds only the rows each folder is missing, each with its folder already set, then
switch to **Rows** and run them as usual. Apply is safe to press again: a folder that
already has enough images, or enough rows waiting to run, gets nothing more.

- **Images are what count.** A workflow that makes four images per row needs a quarter of
  the rows. If you delete images from a folder in the gallery, the preview shows it short
  again, and Apply refills it.
- **Waiting and failed rows count as future images**, so pressing Apply before a run does
  not double anything.
- **Big blueprints need a second click.** More than 2,000 new rows at once asks you to tick
  **Allow a larger expansion** (up to 20,000).
- **If the blueprint changed while you were reviewing** (another tab, say), Apply refuses and
  the preview refreshes, so what you approve is what gets added.
- **The Blueprint tab is read-only while the plan is running.**

## Top up

**Top up** fills every folder that came up short in one press. It adds the rows each
folder is missing, puts failed rows back in the queue, and runs everything still waiting.
It uses the **Base folder** and **Prompt as caption** settings from the **Rows** tab's run
bar, and the run shows in the progress bar like any other.

The preview shows how many images each folder is **Short**, and in total. A folder is
short when it holds fewer images than its target, so it is short again after you delete
some of its images in the gallery, perhaps the ones you didn't like.

Pressing Top up when every folder is full does nothing, so it is safe to press whenever
you have finished pruning.

To go on from Top up to scoring, captioning and exporting without pressing each one, start
a run from the **Runs** page. Its first stage is this same Top up. See
[Automated Runs](runs.md).

If a scenario has **Rewrite into prose** ticked and some of its rows have not been
rewritten yet, Top up adds the missing rows and then stops. It tells you how many rows
wait, so they never run as plain templates by mistake. Press **Rewrite**, then Top up
again.

## Rewriting prompts into prose

Template prompts read like tag lists, which suits tag-based models. Models that read
sentences can do better with prose. Tick **Rewrite into prose (LLM)** on a scenario, Apply,
then press **Rewrite N prompts** under the preview. An LLM rewrites each folder's prompts
into sentences that keep every detail, and describes each character the same way
throughout.

- **Pick a provider and model** in the dialog. It starts from the plan's **Generate
  prompts** settings, and follows the **Instructions** saved there. It never changes them.
- **It shows the cost first:** how many prompts and folders, and the most LLM calls it
  will make. It never goes over that number.
- **It runs in the background**, one folder at a time, and saves each batch as it comes
  back. You can stop it at any time and keep what is done.
- **It never loses a prompt.** If a folder fails, its prompts stay as they were, and the
  rest carry on. Press Rewrite again later to retry what is left.
- **It never touches a prompt you edited by hand**, or one that has already run. While it
  runs, the plan's prompts can't be edited, so an edit can't undo a rewrite that just
  landed.
- **It rewrites the scenarios you saw in the dialog.** One you tick while it waits in the
  queue waits for the next rewrite. A scenario you renamed needs an Apply first.

The preview's totals show how many prompts wait for a rewrite, and the most calls that
would take.

## Changing a blueprint after applying it

A folder is identified by its path. Renaming an entity's label, or changing a folder path,
makes new folders. The old folders' rows then belong to nothing: the preview reports how
many rows and images that is, and they count toward no target. Changing it back is safe: a
folder that comes back picks up the rows and images already in it, rather than starting
from zero.

Renaming an axis or a variation updates every scenario that uses it. Tick **Delete their pending
rows** to remove the ones that have not run yet. Rows that already ran, and their images,
are never deleted; tidy them up by hand if you want.

**New sampling seed** changes which combinations a *Sample K* scenario picks and which
prompts the template walk produces. Use it before you apply, not after.
