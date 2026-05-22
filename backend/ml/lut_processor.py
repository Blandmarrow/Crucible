import logging
from pathlib import Path

import numpy as np

from backend.utils import normalize_image_format, image_save_kwargs

logger = logging.getLogger(__name__)

# Module-level LUT cache: lut_path -> np.ndarray (N,N,N,3) float32
_lut_cache: dict[str, np.ndarray] = {}


def scan_lut_models(directory: Path) -> list[dict]:
    """Return [{name, path, format}] for every .cube/.3dl in directory."""
    if not directory.exists():
        return []
    results = []
    for ext in ("*.cube", "*.3dl"):
        for p in sorted(directory.glob(ext)):
            if p.is_file():
                results.append({
                    "name": p.stem,
                    "path": str(p),
                    "format": p.suffix.lstrip(".").upper(),
                })
    return results


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_cube(path: Path) -> np.ndarray:
    """Parse a .cube 3D LUT file. Returns float32 array of shape (N, N, N, 3)."""
    size: int | None = None
    domain_min = np.array([0.0, 0.0, 0.0], dtype=np.float32)
    domain_max = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    data: list[list[float]] = []

    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            upper = line.upper()
            if upper.startswith("LUT_3D_SIZE"):
                size = int(line.split()[-1])
            elif upper.startswith("DOMAIN_MIN"):
                parts = line.split()
                domain_min = np.array([float(parts[1]), float(parts[2]), float(parts[3])], dtype=np.float32)
            elif upper.startswith("DOMAIN_MAX"):
                parts = line.split()
                domain_max = np.array([float(parts[1]), float(parts[2]), float(parts[3])], dtype=np.float32)
            elif upper.startswith("TITLE") or upper.startswith("LUT_1D_SIZE"):
                continue
            else:
                parts = line.split()
                if len(parts) == 3:
                    try:
                        data.append([float(parts[0]), float(parts[1]), float(parts[2])])
                    except ValueError:
                        continue

    if size is None:
        raise ValueError(f"No LUT_3D_SIZE found in {path}")

    expected = size ** 3
    if len(data) < expected:
        raise ValueError(f"Expected {expected} entries in {path}, got {len(data)}")

    arr = np.array(data[:expected], dtype=np.float32)

    # Normalize from [domain_min, domain_max] to [0, 1]
    scale = domain_max - domain_min
    scale[scale == 0] = 1.0
    arr = (arr - domain_min) / scale
    arr = np.clip(arr, 0.0, 1.0)

    # .cube data order: R varies fastest, G middle, B slowest.
    # reshape gives [B, G, R] axis order; transpose to [R, G, B] for natural indexing.
    lut_bgr = arr.reshape(size, size, size, 3)
    return lut_bgr.transpose(2, 1, 0, 3).astype(np.float32)


def _parse_3dl(path: Path) -> np.ndarray:
    """Parse an Autodesk/Lustre .3dl 3D LUT file. Returns float32 array (N, N, N, 3)."""
    lines: list[str] = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append(line)

    if not lines:
        raise ValueError(f"Empty .3dl file: {path}")

    # First line: mesh input breakpoints, e.g. "0 64 128 192 256" or "0 256 512 768 1023"
    mesh_parts = lines[0].split()
    try:
        mesh = [int(x) for x in mesh_parts]
    except ValueError:
        raise ValueError(f"Cannot parse mesh line in {path}: {lines[0]!r}")

    size = len(mesh)
    max_val = float(mesh[-1]) if mesh[-1] != 0 else 4095.0

    data_lines = lines[1:]
    expected = size ** 3
    if len(data_lines) < expected:
        raise ValueError(f"Expected {expected} data lines in {path}, got {len(data_lines)}")

    data: list[list[float]] = []
    for line in data_lines[:expected]:
        parts = line.split()
        if len(parts) >= 3:
            data.append([float(parts[0]) / max_val, float(parts[1]) / max_val, float(parts[2]) / max_val])

    arr = np.clip(np.array(data, dtype=np.float32), 0.0, 1.0)
    # .3dl data order: R varies fastest, G middle, B slowest (same as .cube).
    # Transpose from [B, G, R] to [R, G, B] for natural indexing.
    lut_bgr = arr.reshape(size, size, size, 3)
    return lut_bgr.transpose(2, 1, 0, 3).astype(np.float32)


def _load_lut(lut_path: str) -> np.ndarray:
    """Load and cache a LUT from disk."""
    if lut_path in _lut_cache:
        return _lut_cache[lut_path]

    p = Path(lut_path)
    ext = p.suffix.lower()
    if ext == ".cube":
        lut = _parse_cube(p)
    elif ext == ".3dl":
        lut = _parse_3dl(p)
    else:
        raise ValueError(f"Unsupported LUT format: {ext}")

    _lut_cache[lut_path] = lut
    logger.info("Loaded LUT %s (size=%d)", p.name, lut.shape[0])
    return lut


# ---------------------------------------------------------------------------
# Trilinear interpolation (pure numpy, no scipy dependency)
# ---------------------------------------------------------------------------

def _apply_lut_array(img_arr: np.ndarray, lut: np.ndarray) -> np.ndarray:
    n = lut.shape[0]
    h, w = img_arr.shape[:2]

    # Scale pixel values to LUT coordinate space; lut is [R, G, B] indexed after parse-time transpose
    coords = img_arr.reshape(-1, 3) * (n - 1)  # (H*W, 3)
    coords = np.clip(coords, 0.0, n - 1 - 1e-6)

    floor = coords.astype(np.int32)
    frac = (coords - floor).astype(np.float32)  # (H*W, 3)

    r0, g0, b0 = floor[:, 0], floor[:, 1], floor[:, 2]
    r1 = np.minimum(r0 + 1, n - 1)
    g1 = np.minimum(g0 + 1, n - 1)
    b1 = np.minimum(b0 + 1, n - 1)

    rf = frac[:, 0:1]
    gf = frac[:, 1:2]
    bf = frac[:, 2:3]

    # 8-corner trilinear blend
    result = (
        lut[r0, g0, b0] * (1 - rf) * (1 - gf) * (1 - bf)
        + lut[r0, g0, b1] * (1 - rf) * (1 - gf) * bf
        + lut[r0, g1, b0] * (1 - rf) * gf * (1 - bf)
        + lut[r0, g1, b1] * (1 - rf) * gf * bf
        + lut[r1, g0, b0] * rf * (1 - gf) * (1 - bf)
        + lut[r1, g0, b1] * rf * (1 - gf) * bf
        + lut[r1, g1, b0] * rf * gf * (1 - bf)
        + lut[r1, g1, b1] * rf * gf * bf
    )

    return result.reshape(h, w, 3)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_lut_sync(
    src: str,
    dest: str,
    lut_path: str,
    intensity: float,
    replace: bool,
) -> dict:
    """
    Apply a LUT to an image file.

    replace=True  → overwrites src in place; dest is ignored.
    replace=False → writes to dest.
    intensity     → 0.0 = no change, 1.0 = full LUT.
    Returns {width, height, file_size_bytes, format}.
    """
    from PIL import Image, ImageOps

    lut = _load_lut(lut_path)

    img = Image.open(src)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")

    arr = np.array(img, dtype=np.float32) / 255.0  # HxWx3 [0,1]

    graded = _apply_lut_array(arr, lut)

    if intensity < 1.0:
        graded = arr * (1.0 - intensity) + graded * intensity

    out_arr = (np.clip(graded, 0.0, 1.0) * 255.0).astype(np.uint8)
    result_img = Image.fromarray(out_arr)
    img.close()

    out_path = src if replace else dest
    fmt, out_path = normalize_image_format(Path(src).suffix, out_path)
    save_kwargs = image_save_kwargs(fmt)

    result_img.save(out_path, format=fmt, **save_kwargs)
    stat = Path(out_path).stat()
    w_out, h_out = result_img.size
    result_img.close()

    return {
        "width": w_out,
        "height": h_out,
        "file_size_bytes": stat.st_size,
        "format": fmt,
        "out_path": out_path,
    }
