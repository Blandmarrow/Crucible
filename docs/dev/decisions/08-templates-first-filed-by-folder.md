# Decision 8: Structured generation builds prompts from templates and files by folder

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-03.
- **Status:** **Decided** (2026-11-03, by the user).
- **Decision:** in M6's structured generation, the one outline call returns the axes, each
  entity's descriptor, and sets of variation pieces (camera angle, pose, time of day). Code
  then builds every leaf's prompts from a template, with no LLM call per leaf. A per-leaf LLM
  rewrite into prose is an optional pass, not the default. Each leaf is filed by **folder**,
  and the leaf records which entities it combines; there are no labels per axis value. A
  **Top up** action, which fills only the leaves that are short, comes before any unattended
  loop. Record: [roadmap-archive/m6-comfy-automation.md](../roadmap-archive/m6-comfy-automation.md).
- **Rationale:** templates keep each character the same in every leaf, can be repeated, and
  cost nothing per leaf. Tag-based image models, which Crucible's users often train, suit
  them best. The August plan's per-leaf calls are what made its retry and budget work (§3)
  necessary. Folders set the export layout, and the leaf record keeps the entity data, so a
  filter by entity can be added later without touching labels. Fixed per-leaf targets already
  make a re-run fill only what is missing, so Top up gives most of the loop's value.
- **Rejected alternatives:**
  - **An LLM call per leaf by default** (the August plan). Better prose for natural-language
    models, but hundreds of calls and the reliability work they need. Kept as the optional pass.
  - **A label per axis value**, so any axis could be filtered. Rejected by the user: labels
    are one global, flat vocabulary with hotkeys, and a few blueprints would bury the labels
    people sort by hand.
  - **Building the goal loop before Top up.** It needs caps, budgets and a stop rule before
    anyone has run a big job unattended.
