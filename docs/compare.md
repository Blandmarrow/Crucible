# Compare

Look at two to four images side by side, or two laid over each other, zoomed in on the
same spot in each. Use it to
pick the best of several near-duplicates, to judge whether an upscale or colour grade
improved an image, or to choose between a few ComfyUI takes on one prompt.

Available from: the **Compare** button in the gallery's selection toolbar, and on each
duplicate group of the **Score images** page. There is no sidebar item, because a
comparison always starts from images you have chosen.

## Opening a comparison

1. In the gallery, select two or more images (up to 100).
2. Press **Compare** in the selection toolbar.

The images appear in the order you selected them, and a selection that spans datasets
compares across them. Two to four open side by side; more open as a [grid](#grid).

**In split view**, Compare opens in the *other* pane, so the gallery you selected from
stays where it is. Change the selection and press Compare again to swap in a new set.
Without split view, Compare takes the gallery's place, and your browser's Back button
returns to the gallery as you left it.

The page's address holds the images it shows, so a reload or a bookmark brings back the
same comparison.

**Back**, at the left of the toolbar, returns to where you opened the comparison: the
gallery, or the Score images page for a duplicate group. Removing or reordering images
doesn't add steps, so one press is enough. In split view, or after opening a bookmark,
it takes you to the first image's gallery (or Score images page) instead.

**From a duplicate group:** on the Score images page, each group of near-duplicates has a
**Compare** button. It opens the group side by side, best first when every image was
scored by the same model, with **Keep this one** under each image (see
[Acting on images](#acting-on-images)).

## Zooming and panning

Zoom and pan are **linked**: every pane follows the one you are working in.

- **Scroll** over any image to zoom around the pointer.
- **Drag** to pan.
- **Double-click** to switch between the whole image and 100% (one screen pixel per image
  pixel) at the point you clicked.
- With a pane focused (click it, or Tab to it): **+** and **−** zoom, the **arrow keys**
  pan, and **0** goes back to the whole image.
- The toolbar has the same controls, with a readout of the current zoom. **100%** shows
  the first image at its real pixels.

By default each pane shows **the same part of the picture**, whatever the image sizes.
A 1024-pixel original and its 2048-pixel upscale line up exactly, which is what you want
when judging an edit. Two toggles change how panes are drawn:

- **Match pixels** draws every image at the same scale as the first one instead, so a
  larger image shows larger and you can see the real difference in resolution.
- **Pixelated** shows true pixels once you zoom past 100%, rather than the browser's
  smoothing. Use it to judge an upscaler's detail.

Both toggles are remembered in this browser.

## A/B: two images in one frame

Press **A/B** in the toolbar to lay one image over another instead of beside it. Pick
the two with the **A** and **B** lists; choosing the image already on the other side
swaps them. The same zoom, pan and keys work here, and **Pixelated** applies too.

Three ways to see what changed, chosen beside the lists:

- **Swipe**: A on the left of a divider, B on the right. Drag the round handle, or Tab to
  it and use the left and right arrow keys.
- **Flip**: A fills the frame until you hold **Space** (or the **Hold to show B**
  button), when B replaces it. A change is often easier to catch in a flip than side by
  side.
- **Difference**: B is blended over A so that anything the same goes black, and only
  what changed shows. Images of different sizes are scaled to one frame first, so fine
  noise shows up as change too.

If A and B have different shapes, B is stretched to A's frame and a note says the
overlay is approximate. A crop is not lined up automatically. The mode and the way B is
shown are remembered in this browser.

## Grid

A comparison of more than four images opens as a **Grid**: every image in an equal
cell, with its file name and aesthetic score. Press **Grid** in the toolbar to see any
comparison this way, and choose **Columns** (Auto, or a number).

Click images to tick up to four, then press **Compare ticked** to open them side by
side. **Back to all** under the panes returns to the full set.

## Exporting a contact sheet

**Export sheet** saves every image in the comparison as one picture, laid out like the
grid, with the text you choose under each image: a title line, the file name, the size
and format, the aesthetic score, and one line of caption. Pick the cell width, a dark
or light background, and PNG (exact) or JPEG (smaller), then press **Download**.

In Side by side, **Export current view** saves the panes on screen in one row, each
cropped to the part you are zoomed into and enlarged to fill its cell. With
**Pixelated** on, the enlargement keeps hard pixel edges, as on screen. Use it to show
an upscaler's detail, or a before and after, in one image. The crop is exact for images
of one shape; with Match pixels on, or images of different shapes, the other panes'
crops can differ a little from what they show.

The sheet is drawn by Crucible from the full-size files and downloads to your computer.
It is never added to a dataset, so it can't end up in a training set by accident. A
sheet is limited to 100 images, 16,000 pixels on its longest side and 64 million pixels
in all (100 images at 512-pixel cells fit); past that, the dialog says so. A sheet of
100 large images takes a few seconds.

## What each pane shows

Under each image: its file name, size in pixels, file size, format, and aesthetic score
with the model that produced it. A score marked **stale** was measured before the image
was last edited in place (see [Stale scores](reading-scores.md#stale-scores)). When the
images come from more than one dataset, each pane names its dataset. Click the caption
line to read the whole caption.

Where the panes differ, the best value is labelled: **most px** beside the largest image,
and **best** beside the highest aesthetic score. Scores are only labelled when every
image was scored by the same model, since the two aesthetic models' scales are not
comparable ([Choosing the aesthetic model](reading-scores.md#choosing-the-aesthetic-model)).

## Changing the comparison

Each pane has buttons in its top-right corner:

- **Open** the image in the detail view. In split view it opens in the other pane, so
  the comparison stays on screen.
- **Move left** swaps the pane with the one before it.
- **Labels** and **Delete**: see below.
- **Remove** takes the image out of the comparison. Its file is not touched. A
  comparison needs at least two images.

If some of the images no longer exist (deleted since, or a link from another install), a
note under the panes says how many were left out.

## Acting on images

- **Labels** opens a list of your [labels](labels.md); tick or untick them for that
  image. Its labels show under the pane.
- **Delete** removes the image and its file from its dataset, with its caption, after
  you confirm. The comparison carries on with the rest.
- **Keep this one** appears under each image of a duplicate group opened from the Score
  images page. It keeps that image and deletes the rest of the group, after you confirm,
  as **Keep best** does on that page but with your choice. It shows only while the
  comparison is still the whole group: once you remove an image from it, keeping one
  would leave the removed ones still marked as duplicates, so the button goes away.
  Open the group again from the Score images page to get it back.
