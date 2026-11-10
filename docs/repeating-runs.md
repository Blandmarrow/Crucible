# Repeating a run

A run can go round in passes until every folder of the plan's blueprint holds enough images
that pass a quality bar. This page covers how a repeating run works, what it needs, the
limits (caps) that stop it, and when it gives up on a folder. For the stages of a run and
the gate that sets the bar, see [Automated Runs](runs.md). For the rest of the start form,
see [Setting up a run](run-setup.md).

## Repeating until every folder is full

Tick **Repeat until every folder is full** to make the run go round in passes. Each pass
fills the blueprint, rewrites (if on), generates, scores and gates. When a pass ends, the
run counts each folder again. If a folder is still short, it starts another pass that
makes just what is missing. Captioning, unloading the caption model and the export run
once, after the last pass.

Only images that count fill a folder. An image flagged as a **duplicate**, or **rejected
by the [gate](runs.md#the-gate)**, does not count, so the next pass makes another. The ComfyUI page's preview
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
| **Most paid LLM calls** | Shown only when a **billed** provider rewrites or captions. It counts every rewrite request and every caption request the run has sent, failed ones included, since the provider was still asked. Calls made by a stage that failed or was cancelled still count after you resume. When captioning is billed, it must be at least **Most images**, so there are always calls left to caption every image. |

A provider is **billed** when you tick that box for it in Settings → LLM Providers. Left
alone, a provider on another machine counts as billed and one on this machine does not.
Ollama and Crucible's own captioners are never billed.

When a cap runs out, the run stops making new images, still frees ComfyUI's VRAM, runs
captioning and the export, and says which cap stopped it. If the paid-call cap cannot
cover every image, the run captions as many as it can and says how many it left out.
One call may go uncounted if Crucible is killed, or shut down, while that call is still
waiting for its answer.

To carry on, press **Raise caps and continue** on the run under *Past runs* (see
[Following a run](runs.md#following-a-run)). Raise the cap
that stopped it; no cap can go down. The run adds another pass for the folders still
short, then captions and exports again. Only the new images are captioned, and what the
run has already used still counts against the caps.

### Giving up on a folder

If a folder gains no images that count in two passes in a row, the run gives up on it and
fills the others. This catches a folder whose prompts never make a picture that passes.
A folder that simply got no rows in a pass, because the image cap was shared out, is not
given up on. The finished run lists each folder still short, and why.
