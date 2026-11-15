# The grid and the contact sheet

The grid half of the Compare page (#130): which mode a set opens in, the grid, and
`POST /images/contact-sheet` with `backend/services/contact_sheet.py`. The user-facing page
is `docs/compare.md`; the rulings are decision 20 (`docs/dev/decisions/20-compare-view.md`).
Routing, the batch read, the actions and what was left unbuilt are in
`docs/dev/gallery/compare.md`; the linked viewport, the pane and A/B are in
`docs/dev/gallery/compare-viewer.md`.

## Grid and the contact sheet

**Which mode a set opens in** is worked out during render, React's pattern for state
that follows a prop (`seenKey` in `ComparePage.tsx`): a set that arrives from
*outside* the page (a Compare button, a URL) opens as Grid when it holds more than
`COMPARE_SIDE_MAX`, and otherwise in the last mode chosen. A list the page wrote
itself through `setIds` (a pane removed or moved, the ticked few, Back to all) is
recognised by `ownKey` and keeps its mode. It used to be recognised by being a subset
of the last list, which treated three images picked in the gallery out of a grid of
ten as a pane removal and left them in Grid; in split view the page is not remounted
between presses, so that case is real. Grid is never stored in `COMPARE_PREFS_KEY`,
since it follows from the set's size, not from a habit.

`components/compare/CompareGrid.tsx` shows thumbnails, not the files: a grid of a
hundred large PNGs would decode hundreds of megabytes for cells a few hundred pixels
wide. Up to four cells tick; **Compare ticked** remembers the full list in `backTo`
(the first one only, so ticking again from a smaller grid cannot lose it) and replaces
the ids with the ticked ones, and **Back to all N** puts the full list back. The grid's
"auto" columns are `autoGridColumns` in `utils/compare.ts`, the same formula as the
server's `auto_columns`, and a chosen count is capped at the number of images as the
server caps it, so the sheet follows the grid on screen.

**`POST /images/contact-sheet`** (`backend/routers/images.py`, drawing in
`backend/services/contact_sheet.py`) returns the sheet as a download and writes nothing
to disk (decision 20). It is the first route that returns *generated* image bytes, and
the first to set `Content-Disposition`; the filename is the dataset name (or `compare`)
reduced to `[a-z0-9-]`, never interpolated raw. In order:

1. Ids are deduped; rows with none drop out; none at all is a 404.
2. **Every `file_path` is gated with `safe_dataset_path` before anything opens it**, and
   one refused row fails the request with 403. This route returns pixels it read, so an
   ungated path would be an arbitrary-file *read* (CLAUDE.md § Path traversal guard).
   A gated path with no file, or one that will not decode, draws a "missing" cell.
3. The output size is computed by `sheet_size` from the first row's **stored**
   width and height, and refused with 422 past 16,000 px on the long side **or** past
   `MAX_SHEET_PIXELS` (64M px) in area, before any image is opened. The side cap alone
   let 100 images at 1024 px cells through, about 12,700 × 15,100 px: ~576 MB of RGB on
   a pool thread that no cancel can stop.
4. `render_contact_sheet` runs on the default pool (`run_in_executor`), never the GPU
   thread. `_open_upright` opens each image (`draft` for JPEG speed), applies the EXIF
   transpose and converts to RGB or RGBA, **closing each step's image as soon as the
   next exists**, so a large file is decoded in memory once, not two or three times.
   It does not use `image_service._safe_from` for that reason: `_safe_from` leaves the
   caller's original open beside the transposed copy and leaks the image it converts
   from. The region crop follows the transpose. The cell is then **filled**, enlarging
   too, so a small zoomed region is drawn at cell size; enlarging uses nearest-neighbour
   when the request's `pixelated` is set (the page's toggle), and shrinking always
   filters. An RGBA image is pasted through its own alpha, so no mask or converted copy
   is made. Text is `ImageFont.load_default(size=…)`, so no font file ships.

**Timing** (dev PC, 2026-11-15): 100 images at 2048×1536, half PNG and half JPEG, every
text line: 1.9 s (JPEG out) to 2.3 s (PNG out) at 256 px cells, 2.4 to 3.1 s at 512 px.
So it stays a plain request, not a background job, by the plan's own test; a sheet
that grows past a few seconds is the trigger to revisit.

**Export current view** (Side by side only) measures pane 1 at click time and sends its
visible rectangle as `region`, `[x0, y0, x1, y1]` in fractions of each image, with the
shown panes in one row and the page's Pixelated setting. One region serves every pane,
which is exact when the images share a shape and Match pixels is off (then every pane
shows the same fraction); otherwise the other panes' crops differ a little from the
screen. The size line under each cell is the whole image's, not the crop's.

`ContactSheetDialog` saves the blob through an `<a download>`; with
`responseType: "blob"` an error body is a Blob too, so `imagesApi.contactSheet` parses
its JSON `detail` back into the error for `apiErrorDetail`. It cannot be closed while a
sheet is drawing: the request cannot be called back, and a closed dialog still
downloaded and toasted "Saved".

## Tests

`frontend/e2e/compare.spec.ts` covers the grid in two journeys (the rest are in `docs/dev/gallery/compare.md` § Tests):

- **Grid**: five selected open as a grid, Export sheet downloads a PNG, two ticked open
  side by side, Export current view downloads a JPEG, and Back to all returns to the
  five.
- **A subset is a new set**: three picked from a grid of five in split view open side by
  side, not as a removal left in Grid.

`backend/tests/test_contact_sheet_http.py` covers the route:

- the size helper against the real output, JPEG, the region, a small region enlarged to
  fill its cell, and Pixelated's hard edges;
- the out-of-tree 403 with the outside file untouched, a missing file's placeholder,
  and both caps refused before any `Image.open`;
- an EXIF-rotated JPEG drawn upright, and nothing written under the datasets folder;
- every image the service makes closed, over a rotated JPEG, a palette PNG and an RGBA
  PNG, tracking the image objects themselves and only calls the service makes (Pillow's
  own temporaries inside `resize` are not ours).
