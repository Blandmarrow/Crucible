import type { PercentCrop } from "react-image-crop";
import type { CropArea } from "./detectionCrop";

/**
 * Rect maths for the detail page's crop tool. The tool holds its box as a
 * percent crop, so the box keeps its place when the pane is resized; these turn
 * it into the source-pixel rect `POST /images/{id}/crop` takes, and back.
 *
 * `w`/`h` are the image's natural size as the browser shows it: EXIF
 * orientation already applied, the same frame the backend crops in after
 * `exif_transpose`.
 */

/** The largest rect of `aspect` (width / height) that fits a `w`×`h` image,
 *  centred, as a percent crop. */
export function fitAspect(w: number, h: number, aspect: number): PercentCrop {
  let pw = 100;
  let ph = ((w / aspect) / h) * 100;
  if (ph > 100) {
    ph = 100;
    pw = ((h * aspect) / w) * 100;
  }
  return { unit: "%", x: (100 - pw) / 2, y: (100 - ph) / 2, width: pw, height: ph };
}

/** The whole image as a percent crop. */
export function fullCrop(): PercentCrop {
  return { unit: "%", x: 0, y: 0, width: 100, height: 100 };
}

/** Clamp a pixel rect to a `w`×`h` image: whole pixels, at least 1×1, and never
 *  past an edge (the size shrinks before the position moves out of the image). */
export function clampArea(area: CropArea, w: number, h: number): CropArea {
  const x = Math.min(Math.max(Math.round(area.x), 0), w - 1);
  const y = Math.min(Math.max(Math.round(area.y), 0), h - 1);
  const width = Math.min(Math.max(Math.round(area.width), 1), w - x);
  const height = Math.min(Math.max(Math.round(area.height), 1), h - y);
  return { x, y, width, height };
}

/**
 * Reshape a pixel rect to `aspect` by growing its short side around its centre,
 * then moving it back inside the image. Grow-only, so a box drawn around a
 * subject keeps the subject; if the grown rect cannot fit at all it becomes the
 * largest centred rect of that aspect instead.
 */
export function growToAspect(area: CropArea, aspect: number, w: number, h: number): CropArea {
  let width = area.width;
  let height = area.height;
  if (width / height < aspect) width = height * aspect;
  else height = width / aspect;
  if (width > w || height > h) {
    const fit = fitAspect(w, h, aspect);
    return percentToPixels(fit, w, h);
  }
  const cx = area.x + area.width / 2;
  const cy = area.y + area.height / 2;
  const x = Math.min(Math.max(cx - width / 2, 0), w - width);
  const y = Math.min(Math.max(cy - height / 2, 0), h - height);
  return clampArea({ x, y, width, height }, w, h);
}

/**
 * Reshape a pixel rect to `aspect`, keeping its area and centre. Used when the
 * aspect changes under a box the user placed: typing an output W×H changes the
 * aspect once per keystroke, and an area-keeping reshape lands back near the
 * same box, where a grow-only one would ratchet up to the whole image. Shrinks
 * to the largest centred fit if the result would not fit the image.
 */
export function reshapeToAspect(area: CropArea, aspect: number, w: number, h: number): CropArea {
  const a = area.width * area.height;
  let width = Math.sqrt(a * aspect);
  let height = Math.sqrt(a / aspect);
  if (width > w || height > h) {
    const scale = Math.min(w / width, h / height);
    width *= scale;
    height *= scale;
  }
  const cx = area.x + area.width / 2;
  const cy = area.y + area.height / 2;
  const x = Math.min(Math.max(cx - width / 2, 0), w - width);
  const y = Math.min(Math.max(cy - height / 2, 0), h - height);
  return clampArea({ x, y, width, height }, w, h);
}

/**
 * The box the tool opens with on a `w`×`h` image: a pending prefill (Crop from
 * Detections) grown to the aspect if one is locked, else the largest centred
 * rect of the aspect, else the whole image.
 */
export function seedCrop(
  w: number,
  h: number,
  pending: PercentCrop | null,
  aspect: number | undefined,
): PercentCrop {
  if (pending) {
    if (!aspect) return pending;
    return pixelsToPercent(growToAspect(percentToPixels(pending, w, h), aspect, w, h), w, h);
  }
  return aspect ? fitAspect(w, h, aspect) : fullCrop();
}

export function percentToPixels(crop: PercentCrop, w: number, h: number): CropArea {
  return clampArea(
    {
      x: (crop.x / 100) * w,
      y: (crop.y / 100) * h,
      width: (crop.width / 100) * w,
      height: (crop.height / 100) * h,
    },
    w,
    h,
  );
}

export function pixelsToPercent(area: CropArea, w: number, h: number): PercentCrop {
  const a = clampArea(area, w, h);
  return {
    unit: "%",
    x: (a.x / w) * 100,
    y: (a.y / h) * 100,
    width: (a.width / w) * 100,
    height: (a.height / h) * 100,
  };
}
