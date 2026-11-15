# Compare

Look at two to four images side by side, zoomed in on the same spot in each. Use it to
pick the best of several near-duplicates, to judge whether an upscale or colour grade
improved an image, or to choose between a few ComfyUI takes on one prompt.

Available from: the **Compare** button in the gallery's selection toolbar. There is no
sidebar item, because a comparison always starts from images you have chosen.

## Opening a comparison

1. In the gallery, select two or more images (up to 100).
2. Press **Compare** in the selection toolbar.

The images appear in the order you selected them, and a selection that spans datasets
compares across them. Side by side shows the first four; remove one to bring in the next.

**In split view**, Compare opens in the *other* pane, so the gallery you selected from
stays where it is. Change the selection and press Compare again to swap in a new set.
Without split view, Compare takes the gallery's place, and your browser's Back button
returns to the gallery as you left it.

The page's address holds the images it shows, so a reload or a bookmark brings back the
same comparison.

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

Each pane has three buttons in its top-right corner:

- **Open** the image in the detail view. In split view it opens in the other pane, so
  the comparison stays on screen.
- **Move left** swaps the pane with the one before it.
- **Remove** takes the image out of the comparison. Its file is not touched. A
  comparison needs at least two images.

If some of the images no longer exist (deleted since, or a link from another install), a
note under the panes says how many were left out.
