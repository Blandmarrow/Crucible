# Subfolders

Filing a dataset's images into subfolders from the gallery's left sidebar: dragging images into them, the folder tree it remembers, and renaming or re-nesting a subfolder. Subfolders are logical groupings — images stay flat on disk. Browsing and filtering the grid itself is covered in [Gallery](gallery.md).

Available from: the subfolder sidebar on the left of any dataset's **Gallery**.

## Moving images into a subfolder

**Drag an image card onto a subfolder row** in the left sidebar to move it there — this works in any sort mode. Drop onto **(root)** to move an image back out of its subfolder; that row is always available, even when it's empty. Missing the rows — dropping on **All** or on empty sidebar space — does nothing.

As you drag over a row it fills with green and the picture you're dragging fades, so you can see the row underneath it; a green tag on the card names the folder you're about to drop into. If you're dragging a selection, the number of images comes along with it.

If the card you drag is part of the current selection, the **whole selection moves**; dragging an unselected card moves just that one and leaves your selection intact. Dropping images onto the subfolder they're already in makes no changes — you'll just get an "Already in …" notice.

Whether moved files are also renamed to match the target subfolder follows the same **auto-rename** preference as the selection toolbar's "Move to subfolder" action. The toolbar route remains available for moving to a subfolder that doesn't exist yet.

## Expanding and collapsing the tree

**The sidebar remembers which folders you left open.** Click the arrow at the left of a row to expand or collapse it; the tree comes back the way you left it when you return from an image, switch pages, or reopen Crucible entirely — per dataset, so a deep working folder stays put. **Reset filters** clears your filters and selected folder but leaves the tree open where it was. And whenever you pick a folder — by clicking it, or by following a link to it from a video's extraction history — it opens to show the folders inside it, along with every branch above it, so you can see both where you are and what is filed under it.

**Collapsing the folder you are standing in sticks**, including across leaving the page and coming back: the tree returns the way you left it, not the way selecting the folder first drew it. The branches *above* the folder you are in do reopen, because otherwise the folder you are standing in would be off screen. Following a link to a folder always opens it — that is an arrival, not a return.

## Renaming and re-nesting a subfolder

**Right-click a subfolder row** for its full menu: create a subfolder inside it, **Rename…**, **Move to…**, move or copy it to another dataset, or delete it. The hover buttons on the row still do the same things they always did — the menu adds to them rather than replacing them.

- **Rename** turns the row into a text box. Type the new name and press Enter; Escape or clicking away cancels. It's a *name*, not a path — slashes are ignored as you type, so renaming can never accidentally move the folder somewhere else.
- **Move to…** opens a list of every folder it can go under, plus **(root) — top level**, and shows you the resulting path before you commit. A folder cannot be moved into itself or into one of its own subfolders, so those rows aren't offered.
- **Drag a subfolder row onto another** to re-nest it — the same gesture as dragging images, and dropping on **(root)** moves it back out to the top level. Everything inside it, subfolders included, comes along.

**Renaming a folder does not rename the files inside it.** A folder is a label, so images keep the filenames they already had — including images auto-named after the old folder (`characters_001.png` stays `characters_001.png` after `characters` becomes `people`). If you want the names to match, use **Renumber Files** (in [Custom order](gallery.md#manual-image-ordering)) or move the images with the auto-rename preference on.

The **All** and **(root)** rows have no menu: *All* isn't a folder, it's the no-filter view, and *(root)* has no name to change — its move and copy buttons are already on the row.
