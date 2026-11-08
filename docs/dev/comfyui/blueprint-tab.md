# The Blueprint tab (BlueprintPanel)

The frontend of structured generation: `BlueprintPanel` and its editors, how the blueprint
saves, the outline draft and its review, `BlueprintPreview`, Apply and Top up, the rewrite
button and `RewriteModal`, their e2e, and the tab's two-column layout. What the server does
with a blueprint is `docs/dev/comfyui/structured.md`; the outline call and the rewrite pass
are `docs/dev/comfyui/structured-llm.md`. The rest of the page — the scroller, the sticky
header, the plan rail and the rows queue — is `docs/dev/comfyui/page.md`.

## The panel

`BlueprintPanel` (`frontend/src/components/comfy/BlueprintPanel.tsx`, keyed by `plan.id`)
edits the plan's blueprint.

- **Saving.** It is server-stored form state on the M5 pattern: `ServerStateGate` on
  `["comfy","blueprint",planId]`, autosaved through `useDebouncedPersist` +
  `writeToServer(blueprintPath, "PUT")` (`docs/dev/app-stored-data.md`).
- **The seed.** A plan with no blueprint gets a random seed, saved only with the first
  edit. *New sampling seed* rerolls it.
  - That changes which combinations a `sample:K` scenario picks, and so which folders
    exist (the preview shows the orphans).
  - It also changes every leaf's template walk, so rows added later may repeat prompts
    that existing rows already used.
- **The editors.** `BlueprintAxesEditor`, `BlueprintVariationsEditor` and
  `BlueprintScenariosEditor` hold the three parts. `BlueprintNameField` renames an axis or
  variation on blur, through `blueprintKeys.renameRefs`. That also rewrites every
  scenario's `combine`, path and template references (`{name}`, `{name.label}`); renaming
  only the key would turn each reference into an error. Entity ids are generated
  client-side and never shown. The numeric
  fields clamp to the schema's bounds (take ≤ 4, count ≤ 10,000), because past them both
  the save and the preview 422. The text inputs carry a `maxLength` for the same reason,
  from `BLUEPRINT_LIMITS` in `blueprintKeys.ts`, which mirrors `schemas/comfy_blueprint.py`
  (name 40, scenario name 100, path 400, template 4000, label 200, descriptor 2000, piece
  300). The same const holds the list sizes (8 axes, 200 entities an axis, 12 variations,
  100 pieces a variation, 50 scenarios, 6 combined axes): each *+ Add* button disables at
  its cap. The pieces box cannot be stopped by `maxLength`, so it saves the first 100
  lines, each cut to 300 characters, and shows a warning for what it left out.
- **Read-only while the plan runs.** A disabled `<fieldset>`, since Apply would 409 anyway.
- **The outline draft.** *✨ Draft from a description…* opens `OutlineModal`, which queues
  the `comfy_outline` job (`docs/dev/comfyui/structured-llm.md` § The outline call).
  - Like `GeneratePromptsModal`, it derives its job from `jobStore` and adds no
    `useJobSSE`. Its provider and model default to the plan's saved "Generate prompts"
    settings, read only.
  - **On start it seeds `jobStore` with the job's `plan_id`.** The queue's pending event
    carries none, so a job queued behind a long run was invisible to the plan scan
    until `_run` began. A reopened modal then offered *Draft* again, only to get a 409.
  - TopBar's `comfy_outline` branch toasts the outcome once per job (`outlineTerminalRef`)
    and invalidates the draft.
  - **The draft has its own query, `["comfy","blueprint-draft",planId]`.** The gate
    latches its first value and never refetches into the form, so it cannot show a draft
    that lands while the tab is open.
  - **While a draft exists, the modal is its review** (`DraftReview`), not the brief
    form, so a draft that lands with the modal open is shown there. The banner's
    *Review…* opens the same view, and *Draft again…* goes back to the brief, where
    *Back to the draft* returns to the review. The review's *Decide later* just closes
    the modal and keeps the draft. The review's
    expansion is the ordinary preview endpoint with the draft in the body, on its own key
    (`["comfy","blueprint-draft-preview",…]`); that endpoint writes nothing, so the saved
    spec stays as it was until *Use it*. A failure of the job this modal started shows
    inline as well as in the toast.
  - **A new *Draft* blanks the cached draft first**, with `setQueryData`. The old one
    would otherwise show in the review between the job's end and the refetch TopBar
    triggers. A failed job leaves the old draft saved, and that refetch brings it back.
- **The draft's *Use it* (banner or review) clears the draft only after the spec holding
  it is saved.** In order:
  1. Await `serverWritesSettled`, so no older autosave can land on top.
  2. Save the draft with an awaited `comfyApi.putBlueprint`.
  3. Set the form to the draft.
  4. `DELETE …/draft`.
  - Not `writeToServer`: it drops a 4xx write, and `serverWritesSettled` then resolves,
    which would clear a draft whose save never landed.
  - A non-empty spec asks before it is replaced: the banner with a `ConfirmDialog`, the
    review with its button relabelled *Replace my blueprint* and a warning line, since a
    dialog over the modal would stack two. *Discard* just deletes the draft.
  - `applyDraft` resolves true on success, and the review closes on it.
- **`BlueprintPreview`** is a `useQuery` on
  `["comfy","blueprint-preview",planId,debouncedJSON,allowOverCap]` (350 ms via
  `hooks/useDebouncedValue.ts`, `keepPreviousData`).
  - **Apply sends the debounced spec the shown preview came from**, never the live form,
    with `confirm_total` = that preview's `rows_to_add`.
  - It is disabled while the form lags the preview, while fetching or on placeholder data,
    on errors, over the cap without the override, and with no prompt pin.
  - A 409 toasts the server's reason and refetches. Success invalidates rows, plans,
    prompts and the preview.
  - **It also refetches on window focus and every 20 s.** Kept and short change outside
    the tab: an image deleted in the gallery makes its folder short. Without a refetch the
    panel sat on "0 short" with Top up disabled. A finished run in `activeRuns`
    invalidates it too.
  - **A background refetch is not "stale".** The buttons wait only for a spec still
    debouncing or placeholder data for a new key, never for `isFetching`. Counting it
    disabled both buttons every 20 s and dropped clicks; `confirm_total` already 409s if
    the rows moved meanwhile.
- **Top up** sits beside Apply, under the same rules. It is also disabled when nothing
  is short, to add, pending or failed ("Every folder is full").
  - It sends Apply's body plus the run bar's **Base folder** and **Prompt as caption**,
    which live in `ComfyPage` state and are shown read-only under the buttons
    (`topup-settings`).
  - **The returned job joins `activeRuns`** through `onRunStarted(jobId, planId)`, the
    same as `runMutation`. Tracking, invalidation, the completion toast and the
    `isRunning` gate then apply unchanged.
  - `job_id: null` toasts "Every folder is full".
  - **`awaiting_rewrite > 0` is not a run.** The reply's rows were applied but nothing ran,
    so it skips `onRunStarted` and shows the `blueprint-awaiting-rewrite` notice, which the
    next Apply or Top up clears.
- **The rewrite** (`docs/dev/comfyui/structured-llm.md` § The rewrite pass).
  - Each scenario has a *Rewrite into prose (LLM)* checkbox (`BlueprintScenario.rewrite`).
    The TS field is what keeps the autosave from dropping it.
  - The totals line adds "N to rewrite · ≤ B LLM calls" from the preview's `rewrite_*`
    figures.
  - **The *Rewrite N prompts* button counts rows that exist now**, from
    `GET …/blueprint/rewrite` at the saved batch size, under
    `["comfy","blueprint-rewrite",planId,batch]`. It does not use the preview's count,
    which includes rows Apply would add. The button appears beside Apply and Top up only
    while the preview says some rows need rewriting (`rewrite_rows > 0`). It is
    **disabled, with an "Apply first" tooltip,** while no such row exists yet.
  - `RewriteModal` follows `OutlineModal`: defaults from the saved settings, read only;
    its job derived from `jobStore`, seeded with `plan_id` on start. It sends the shown
    estimate's `budget` as `confirm_budget`, and a 409 refetches the estimate.
  - **TopBar's `comfy_rewrite` branch** refreshes the rows as each batch lands
    (`rewriteDoneRef`), as the `comfy_prompts` branch does, and the estimate and the
    preview with them. Both count the rows still waiting, so refreshed only at the end
    they sat on the starting count for the whole run (2026-11-04).
    - It toasts once per job (`rewriteTerminalRef`), reading `result_data` through
      `jobsApi.get`, since neither the terminal event nor a reconciled one carries it. It
      then invalidates rows, prompts, the preview and the estimate.
    - **A completed run that stopped early, or left prompts behind, gets a warning
      toast**, not a green one. It names the reason and how many prompts still wait,
      since Top up will refuse while they do.
    - The job is in neither image job-type set: it changes rows, never images.
- **The e2e.** `comfy-run.spec.ts` authors a blueprint by hand, applies it, checks the rows'
  folders and `leaf_id`s, and runs them. Another ticks the rewrite, checks the
  totals line and Top up's notice, and opens `RewriteModal`, all without an LLM. Two more
  cover the draft: the Blueprint tab opens the draft-from-a-description dialog, and a
  waiting draft can be reviewed, with its expansion, before *Use it*.

## The layout

M6 §8a (`docs/dev/roadmap-archive/m6-comfy-automation.md`). The CSS is the `.bp-*` rules in
`frontend/src/index.css`. The page scroller and the sticky header it sits under, and the
`--comfy-head-h`/`--comfy-view-h` offsets these rules read, are `docs/dev/comfyui/page.md`
§ The layout.

- **Two columns** (`.bp-wrap` / `.bp-grid`): the editors on the left, the
  preview's `<aside class="bp-side">` on the right, sticky, capped at the visible height, with
  `.bp-body` scrolling inside it.
  - **A container query, not a media query**, at 880px: a split pane is narrow in a wide
    window. The e2e viewport gives about 980px, so e2e runs the wide layout only.
  - **Narrow, the grid becomes a flex column and `.bp-side` becomes `display: contents`**,
    so `.bp-actions` is a flex item of the whole tab and `position: sticky; bottom: 0` pins
    it while the editors scroll. Both halves are needed: a sticky grid item is held inside
    its own grid area, and a sticky child of the aside inside the aside, which is off
    screen while you edit.
  - `align-items: start` keeps the wide aside unstretched, or sticky could not move it.
  - **`.bp-body` scrolls on its own down to 120px, then the whole aside scrolls.** In a
    wide but short pane (a horizontal split) the header can take most of the height, and a
    clipped aside hid Apply and Top up out of reach.
  - **`RewriteModal` renders outside the aside.** A sticky element is its own stacking
    context, so a dialog inside it sat under the sticky header whatever its z-index, with
    the tabs and Run pending clickable above the backdrop. Keep any new dialog out of it.
  - BlueprintPanel measures the jump bar into `--bp-jump-h`, and the `.bp-wrap` rules turn
    it into `scroll-margin-top`, so a jump or a field taking focus just below the header
    is not hidden under the jump bar.
- **The preview has its own `running` guard.** It used to sit inside the editors' disabled
  `<fieldset>`; outside it, it disables Rewrite and the over-cap and orphan checkboxes
  itself, and Apply's and Top up's reason reads "A run is in progress on this plan". It
  gets the real `hasPromptPin`, no longer `hasPromptPin && !running`, whose tooltip blamed a
  missing prompt pin.
- **The leaves table** has no Sample prompts column (the user's call, 2026-11-05: it made
  every row tall in the side column). A folder with samples gets a chevron button
  (`aria-expanded`) that opens them in a row under it (`blueprint-leaf-samples`). The server
  sends samples for the first 20 folders only, so the rest get no button.
- **`BlueprintSection`** is each editor's collapsible `.panel`: an `<h3><button
  aria-expanded>` toggle with a count summary, and the editor's *+ Add …* button beside it,
  which opens the section when pressed.
  - **A collapsed body is hidden, never unmounted.** `PiecesField` in
    `BlueprintVariationsEditor.tsx` keeps typed text that can differ from what is saved (an
    over-limit line and its warning), and an unmount would lose it.
  - **The read-only `<fieldset>` moved inside each section**, around the Add button and the
    body only, so a section still folds while a run is live. *New sampling seed* takes
    `disabled={running}` directly.
  - The section element reaches the jump bar as a React 19 `ref` prop through each editor.
    A ref inside the `SectionControl` object fails the compiler's `react-hooks/refs` lint.
  - The open state is `COMFY_BLUEPRINT_SECTIONS_KEY` + `-${planId}`
    (`docs/dev/persistence.md`), all open by default, which the e2e relies on.
- **The jump bar** (`.bp-jump`, `blueprint-jump`) is sticky under the header in the left
  column: one button per section (it opens a folded one, then scrolls to it), *Collapse
  all*, *Expand all* and *✨ Draft from a description…* (`outline-open`). They are buttons,
  not `#hash` links: no router hash, and no duplicate ids when two panes show the page.
