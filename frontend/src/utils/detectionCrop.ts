import type { Detection } from "../types";

export interface CropArea { x: number; y: number; width: number; height: number; }

/**
 * Compute a pixel-space crop rect from the union of visible detection bboxes,
 * padded by `paddingPct` percent of the union's own extent per side and clamped
 * to the image. Returns null when no visible detection has a usable bbox. No
 * aspect-ratio snapping — react-easy-crop's aspect prop handles that
 * interactively once seeded.
 */
export function detectionCropPrefill(
  dets: Detection[],
  hiddenLabels: Set<string>,
  imgW: number,
  imgH: number,
  paddingPct = 5,
): CropArea | null {
  const visible = dets.filter((d) => !hiddenLabels.has(d.label));
  const boxes: [number, number, number, number][] = [];
  for (const d of visible) {
    const b = d.bbox;
    if (!b || b.length !== 4) continue;
    let [x1, y1, x2, y2] = b;
    if (x1 > x2) [x1, x2] = [x2, x1];
    if (y1 > y2) [y1, y2] = [y2, y1];
    boxes.push([
      Math.min(Math.max(x1, 0), 1), Math.min(Math.max(y1, 0), 1),
      Math.min(Math.max(x2, 0), 1), Math.min(Math.max(y2, 0), 1),
    ]);
  }
  if (boxes.length === 0) return null;

  let ux1 = Math.min(...boxes.map((b) => b[0]));
  let uy1 = Math.min(...boxes.map((b) => b[1]));
  let ux2 = Math.max(...boxes.map((b) => b[2]));
  let uy2 = Math.max(...boxes.map((b) => b[3]));

  const padX = (ux2 - ux1) * paddingPct / 100;
  const padY = (uy2 - uy1) * paddingPct / 100;
  ux1 = Math.max(ux1 - padX, 0);
  uy1 = Math.max(uy1 - padY, 0);
  ux2 = Math.min(ux2 + padX, 1);
  uy2 = Math.min(uy2 + padY, 1);

  const x = Math.round(ux1 * imgW);
  const y = Math.round(uy1 * imgH);
  const width = Math.round((ux2 - ux1) * imgW);
  const height = Math.round((uy2 - uy1) * imgH);
  if (width < 1 || height < 1) return null;
  return { x, y, width, height };
}
