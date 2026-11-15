import { useState } from "react";
import toast from "react-hot-toast";
import ModalShell from "../common/ModalShell";
import { imagesApi } from "../../api/images";
import type { ContactSheetRequest, SheetField } from "../../api/images";
import { apiErrorDetail } from "../../utils/apiError";

/** The options the dialog remembers between exports (part of the Compare prefs). */
export interface SheetPrefs {
  cellPx: number;
  fields: SheetField[];
  background: "dark" | "light";
  format: "png" | "jpeg";
}

const FIELD_LABELS: { value: SheetField; label: string }[] = [
  { value: "header", label: "Title line" },
  { value: "filename", label: "File name" },
  { value: "size", label: "Size and format" },
  { value: "score", label: "Aesthetic score" },
  { value: "caption", label: "Caption (one line)" },
];
const CELL_SIZES = [128, 192, 256, 384, 512, 768, 1024];

interface Props {
  ids: string[];
  /** The grid's columns, or null for the server's auto layout (which matches the grid's). */
  columns: number | null;
  /** Export current view: the region of every image to keep, x0, y0, x1, y1. */
  region: [number, number, number, number] | null;
  prefs: SheetPrefs;
  onPrefs: (p: SheetPrefs) => void;
  onClose: () => void;
}

/**
 * Export sheet / Export current view. The server draws the sheet from the full
 * files and sends it back; this saves it through the browser's download, and
 * nothing is written into any dataset (decision 20). There is no preview canvas:
 * the grid behind the dialog is the layout the sheet follows.
 */
export default function ContactSheetDialog({ ids, columns, region, prefs, onPrefs, onClose }: Props) {
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<SheetPrefs>) => onPrefs({ ...prefs, ...patch });
  const toggleField = (f: SheetField) =>
    set({ fields: prefs.fields.includes(f) ? prefs.fields.filter((x) => x !== f) : FIELD_LABELS.map((o) => o.value).filter((v) => v === f || prefs.fields.includes(v)) });

  const download = async () => {
    setBusy(true);
    const body: ContactSheetRequest = {
      image_ids: ids, columns, cell_px: prefs.cellPx, fields: prefs.fields,
      background: prefs.background, format: prefs.format, region,
    };
    try {
      const { blob, filename } = await imagesApi.contactSheet(body);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Revoked a tick later: some browsers start the download asynchronously.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast.success(`Saved ${filename}`);
      onClose();
    } catch (err) {
      toast.error(apiErrorDetail(err, "The sheet could not be made."));
    } finally {
      setBusy(false);
    }
  };

  const title = region ? "Export current view" : "Export sheet";
  return (
    <ModalShell label={title} onClose={onClose} closeOnBackdrop>
      <h3>{title}</h3>
      <p className="cmp-dialog-lede">
        {region
          ? `The ${ids.length} images side by side, each cropped to the part you were zoomed into.`
          : `All ${ids.length} images in one picture, laid out like the grid${columns ? ` (${columns} columns)` : ""}.`}{" "}
        It downloads to your computer; nothing is added to a dataset.
      </p>
      <div className="cmp-dialog-grid">
        <label className="cmp-field">
          Cell width
          <select className="select" value={prefs.cellPx} onChange={(e) => set({ cellPx: Number(e.target.value) })}>
            {CELL_SIZES.map((s) => <option key={s} value={s}>{s} px</option>)}
          </select>
        </label>
        <fieldset className="cmp-fieldset">
          <legend>Text under each image</legend>
          {FIELD_LABELS.map((f) => (
            <label key={f.value} className="cmp-check">
              <input type="checkbox" className="checkbox" checked={prefs.fields.includes(f.value)} onChange={() => toggleField(f.value)} />
              {f.label}
            </label>
          ))}
        </fieldset>
        <fieldset className="cmp-fieldset">
          <legend>Background</legend>
          {(["dark", "light"] as const).map((b) => (
            <label key={b} className="cmp-check">
              <input type="radio" name="sheet-bg" checked={prefs.background === b} onChange={() => set({ background: b })} />
              {b === "dark" ? "Dark" : "Light"}
            </label>
          ))}
        </fieldset>
        <fieldset className="cmp-fieldset">
          <legend>Format</legend>
          {(["png", "jpeg"] as const).map((f) => (
            <label key={f} className="cmp-check">
              <input type="radio" name="sheet-format" checked={prefs.format === f} onChange={() => set({ format: f })} />
              {f === "png" ? "PNG (exact)" : "JPEG (smaller)"}
            </label>
          ))}
        </fieldset>
      </div>
      <div className="cmp-dialog-foot">
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="btn primary" disabled={busy} onClick={download}>
          {busy ? "Drawing…" : "Download"}
        </button>
      </div>
    </ModalShell>
  );
}
