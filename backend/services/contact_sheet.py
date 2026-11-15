"""Draw a contact sheet: a grid of images with a few lines of text under each.

Pure: no DB, no HTTP, and it writes nothing to disk. The caller hands in cells
whose paths already passed `utils.safe_dataset_path` (this module trusts them,
which is why the gate lives in the route) and gets the encoded image back as
bytes. A sheet must never land in a dataset (decision 20), so there is no
output path to misuse.

CPU work: the route runs it on the default thread pool, never the GPU thread.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from backend.services.image_service import _safe_from

MAX_SHEET_SIDE = 16_000
MAX_COLUMNS = 12
DEFAULT_ASPECT = 4 / 3
# Cells follow the first image's aspect, but never get absurdly tall or wide.
MIN_ASPECT, MAX_ASPECT = 0.5, 2.0

DEFAULT_FIELDS: tuple[str, ...] = ("header", "filename", "size", "score")

_THEMES = {
    "dark": {"bg": (0x10, 0x13, 0x15), "cell": (0x1a, 0x1e, 0x20), "text": (0xe6, 0xed, 0xec), "dim": (0x9a, 0xa6, 0xa3)},
    "light": {"bg": (0xec, 0xee, 0xed), "cell": (0xdd, 0xe0, 0xdf), "text": (0x14, 0x18, 0x17), "dim": (0x40, 0x47, 0x4a)},
}


@dataclass
class SheetCell:
    """One image. `path` is None when the row has no file; a path that does not
    exist (or will not decode) draws a "missing" placeholder rather than failing."""
    path: Path | None
    filename: str = ""
    width: int | None = None
    height: int | None = None
    file_size_bytes: int | None = None
    format: str | None = None
    aesthetic_score: float | None = None
    aesthetic_model: str | None = None
    caption: str = ""
    dataset_name: str = ""


@dataclass
class SheetOptions:
    columns: int | None = None
    cell_px: int = 256
    fields: tuple[str, ...] = DEFAULT_FIELDS
    background: str = "dark"
    format: str = "png"
    # (x0, y0, x1, y1) as fractions of each image, after the EXIF transpose.
    region: tuple[float, float, float, float] | None = None
    # Cell width / height. None = the 4:3 fallback. The route derives it from the
    # first row's stored size so it can size-check before opening anything.
    aspect: float | None = None


@dataclass(frozen=True)
class _Layout:
    cell_px: int
    font_px: int
    pad: int
    line_h: int
    header_h: int
    area_h: int
    lines: int
    cols: int
    rows: int

    @property
    def cell_h(self) -> int:
        return self.area_h + (self.pad // 2 + self.lines * self.line_h if self.lines else 0)

    @property
    def size(self) -> tuple[int, int]:
        w = self.cols * self.cell_px + (self.cols + 1) * self.pad
        h = self.header_h + self.rows * self.cell_h + (self.rows + 1) * self.pad
        return w, h


def clamp_aspect(aspect: float | None) -> float:
    if not aspect or aspect <= 0 or not math.isfinite(aspect):
        return DEFAULT_ASPECT
    return min(MAX_ASPECT, max(MIN_ASPECT, aspect))


def auto_columns(n: int) -> int:
    return max(1, min(n, math.ceil(math.sqrt(n * 1.4))))


def _text_lines(fields) -> int:
    return sum(1 for f in ("filename", "size", "score", "caption") if f in fields)


def _layout(n: int, columns: int | None, cell_px: int, aspect: float | None, fields) -> _Layout:
    cols = max(1, min(columns or auto_columns(n), MAX_COLUMNS, max(n, 1)))
    font_px = max(10, round(cell_px / 22))
    pad = max(6, cell_px // 32)
    line_h = round(font_px * 1.35)
    header_h = round(font_px * 1.6) + pad if "header" in fields else 0
    return _Layout(
        cell_px=cell_px, font_px=font_px, pad=pad, line_h=line_h, header_h=header_h,
        area_h=max(1, round(cell_px / clamp_aspect(aspect))),
        lines=_text_lines(fields), cols=cols, rows=math.ceil(n / cols) if n else 0,
    )


def sheet_size(
    n: int, columns: int | None, cell_px: int, aspect: float | None, fields
) -> tuple[int, int]:
    """The output size in pixels, from the request alone. Opens nothing, so the
    route can refuse an oversize sheet before any work."""
    return _layout(n, columns, cell_px, aspect, fields).size


def region_aspect(width: int | None, height: int | None, region) -> float | None:
    """Aspect of `region` applied to a width x height image (None if unknown)."""
    if not width or not height:
        return None
    x0, y0, x1, y1 = region or (0.0, 0.0, 1.0, 1.0)
    return (width * (x1 - x0)) / (height * (y1 - y0))


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    """`text` cut with an ellipsis so it is at most `max_w` pixels wide."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid].rstrip() + "…", font=font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def _human_size(n: int | None) -> str:
    if n is None:
        return ""
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{int(v)} B" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return ""


def _lines_for(cell: SheetCell, fields) -> list[str]:
    out: list[str] = []
    if "filename" in fields:
        out.append(cell.filename)
    if "size" in fields:
        dims = f"{cell.width}×{cell.height}" if cell.width and cell.height else ""
        parts = [p for p in (dims, (cell.format or "").upper(), _human_size(cell.file_size_bytes)) if p]
        out.append(" · ".join(parts))
    if "score" in fields:
        if cell.aesthetic_score is None:
            out.append("aesthetic n/a")
        else:
            model = f" ({cell.aesthetic_model})" if cell.aesthetic_model else ""
            out.append(f"aesthetic {cell.aesthetic_score:.2f}{model}")
    if "caption" in fields:
        out.append(" ".join((cell.caption or "").split()))
    return out


def _load_cell_image(cell: SheetCell, lay: _Layout, region) -> Image.Image | None:
    """The cell's pixels, transposed, cropped to `region` and scaled to fit, or
    None when the file is missing or will not decode. Every image opened here is
    closed here, the pre-transpose original included."""
    if cell.path is None:
        return None
    raw = None
    img = None
    try:
        raw = Image.open(cell.path)
        if raw.format == "JPEG":
            # Decode at a reduced scale. Draft keeps both sides at or above the
            # request, and the pre-transpose axes may be swapped, so ask for the
            # larger need on both.
            rw = (region[2] - region[0]) if region else 1.0
            rh = (region[3] - region[1]) if region else 1.0
            need = math.ceil(max(lay.cell_px / rw, lay.area_h / rh))
            raw.draft("RGB", (need, need))
        img = _safe_from(raw)
        if region:
            x0, y0, x1, y1 = region
            w, h = img.size
            box = (
                int(x0 * w), int(y0 * h),
                max(int(x0 * w) + 1, round(x1 * w)), max(int(y0 * h) + 1, round(y1 * h)),
            )
            cropped = img.crop(box)
            img.close()
            img = cropped
        img.thumbnail((lay.cell_px, lay.area_h), Image.Resampling.LANCZOS)
        out, img = img, None
        return out
    except Exception:
        return None
    finally:
        if img is not None:
            img.close()
        if raw is not None:
            raw.close()


def _title(cells: list[SheetCell]) -> str:
    names = {c.dataset_name for c in cells if c.dataset_name}
    n = len(cells)
    who = next(iter(names)) if len(names) == 1 else f"{len(names)} datasets"
    return f"{who} · {n} image{'s' if n != 1 else ''}"


def render_contact_sheet(cells: list[SheetCell], opts: SheetOptions) -> bytes:
    """The sheet as PNG or JPEG bytes. Never raises for an unreadable image."""
    fields = tuple(opts.fields)
    lay = _layout(len(cells), opts.columns, opts.cell_px, opts.aspect, fields)
    theme = _THEMES.get(opts.background, _THEMES["dark"])
    width, height = lay.size
    sheet = Image.new("RGB", (width, height), theme["bg"])
    try:
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default(size=lay.font_px)
        if lay.header_h:
            draw.text((lay.pad, lay.pad), _fit(draw, _title(cells), font, width - 2 * lay.pad),
                      font=font, fill=theme["text"])
        for i, cell in enumerate(cells):
            col, row = i % lay.cols, i // lay.cols
            x = lay.pad + col * (lay.cell_px + lay.pad)
            y = lay.header_h + lay.pad + row * (lay.cell_h + lay.pad)
            draw.rectangle((x, y, x + lay.cell_px - 1, y + lay.area_h - 1), fill=theme["cell"])
            img = _load_cell_image(cell, lay, opts.region)
            if img is None:
                label = _fit(draw, f"missing: {cell.filename}" if cell.filename else "missing",
                             font, lay.cell_px - 2 * lay.pad)
                draw.rectangle((x, y, x + lay.cell_px - 1, y + lay.area_h - 1), outline=theme["dim"])
                tw = draw.textlength(label, font=font)
                draw.text((x + (lay.cell_px - tw) / 2, y + (lay.area_h - lay.font_px) / 2),
                          label, font=font, fill=theme["dim"])
            else:
                try:
                    ox = x + (lay.cell_px - img.width) // 2
                    oy = y + (lay.area_h - img.height) // 2
                    mask = img.getchannel("A") if "A" in img.getbands() else None
                    sheet.paste(img.convert("RGB") if mask is None else img, (ox, oy), mask)
                finally:
                    img.close()
            ty = y + lay.area_h + lay.pad // 2
            for j, line in enumerate(_lines_for(cell, fields)):
                if line:
                    draw.text((x, ty + j * lay.line_h), _fit(draw, line, font, lay.cell_px),
                              font=font, fill=theme["text"] if j == 0 else theme["dim"])
        buf = io.BytesIO()
        if opts.format == "jpeg":
            sheet.save(buf, "JPEG", quality=90)
        else:
            sheet.save(buf, "PNG")
        return buf.getvalue()
    finally:
        sheet.close()
