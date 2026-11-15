# Decision 20: Compare is a page opened from images, with download-only sheets

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-15.
- **Status:** **Decided** (2026-11-15, by the user), while designing issue #130. The
  design is [compare-plan/README.md](../compare-plan/README.md) until it lands.
- **Decision:**
  - **Compare is a routed page, not a modal**, at `/compare?ids=…`, tied to no dataset,
    since a selection can span datasets.
  - **It has no sidebar entry and is not in a pane's page picker.** It opens from a
    Compare button: the gallery's selection toolbar and each duplicate group on the
    Quality page.
  - **In split view it opens in the neighbouring pane**, so the gallery that opened it
    stays put. Without split view it replaces the gallery, and Back returns to it.
  - **Linked panes match the same region of the picture by default**, so a source and its
    upscale line up. Matching pixel size is a toggle.
  - **A contact sheet is a download only.** The server draws it and writes nothing to
    disk.
  - **The view can act on images**: Keep this one for a duplicate group, plus delete and
    labels on any image.
  - **It ships one PR per phase**, four phases in the plan's order.
- **Rationale:**
  - A page keeps its address, so a reload or a bookmark brings the comparison back, and it
    can sit in a pane beside the gallery. That is the fast iteration the issue asks for.
  - A sidebar link would open an empty page and send the user back to the gallery to
    choose images.
  - Anything in a dataset's `images/` folder becomes training data, so a sheet must never
    be written there; a download avoids a second write path to guard.
  - Deleting and labelling from the comparison saves a trip to the detail page when
    culling, which is the common reason to compare.
- **Rejected:**
  - A full-screen modal over the gallery: no routing work, but no address, no reload and
    no pane beside the gallery.
  - A sidebar entry with an empty state.
  - Saving sheets to an `exports/` folder: a new path setting and a write path for little
    gain over a download.
  - A view that only looks, leaving every action to the detail and Quality pages.
- **Revisit when:** users ask for sheets on disk (for a run's record, say), or the Compare
  page needs to be reached without images in hand.
