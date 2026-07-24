import json
import re
from pathlib import Path

from PIL import Image, ImageOps

from backend.utils import image_save_kwargs


def _save_kwargs(path: str) -> dict:
    """PIL save() kwargs for an in-place save, keyed off the file extension.

    JPEG destinations get quality=95 + no chroma subsampling (via utils.image_save_kwargs)
    so re-encoding on resize/crop doesn't silently drop to Pillow's default quality 75.
    Other formats keep their defaults. Deliberately keyed on the existing suffix — these
    are in-place saves whose filename is recorded in the DB, so the extension must not change.
    """
    return image_save_kwargs("JPEG") if Path(path).suffix.lower() in (".jpg", ".jpeg") else {}


RESAMPLE_MAP = {
    "LANCZOS": Image.Resampling.LANCZOS,
    "BICUBIC": Image.Resampling.BICUBIC,
    "NEAREST": Image.Resampling.NEAREST,
    "BILINEAR": Image.Resampling.BILINEAR,
}


def _open_safe(path: str) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # respect EXIF rotation
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    return img


def _parse_a1111_params(text: str) -> dict:
    """Parse Automatic1111 / AUTOMATIC1111 generation parameters string."""
    result: dict = {"source": "a1111", "raw": text}

    # Split on "Negative prompt:" — case-insensitive, handles \r\n, matches at
    # start-of-string or after a newline so the separator itself is consumed.
    neg_split = re.split(
        r"(?:^|\r?\n)Negative prompt:\s*",
        text,
        maxsplit=1,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    prompt = neg_split[0].strip()
    if prompt:
        result["prompt"] = prompt

    if len(neg_split) > 1:
        remainder = neg_split[1]
        # The negative prompt ends at the param line that starts with "Steps:"
        param_line_match = re.search(r"\r?\nSteps:", remainder, re.IGNORECASE)
        if param_line_match:
            result["negative_prompt"] = remainder[: param_line_match.start()].strip()
            param_text = remainder[param_line_match.start():]
        else:
            result["negative_prompt"] = remainder.strip()
            param_text = ""
    else:
        # No negative prompt — the prompt part may contain a trailing param line
        param_line_match = re.search(r"\r?\nSteps:", neg_split[0], re.IGNORECASE)
        if param_line_match:
            result["prompt"] = neg_split[0][: param_line_match.start()].strip()
            param_text = neg_split[0][param_line_match.start():]
        else:
            param_text = ""

    for match in re.finditer(r"([\w][\w\s]+?):\s*([^,\n]+)", param_text):
        key = match.group(1).strip().lower().replace(" ", "_")
        val = match.group(2).strip()
        if key == "steps":
            try:
                result["steps"] = int(val)
            except ValueError:
                pass
        elif key == "cfg_scale":
            try:
                result["cfg_scale"] = float(val)
            except ValueError:
                pass
        elif key == "seed":
            try:
                result["seed"] = int(val)
            except ValueError:
                pass
        elif key in ("sampler", "sampler_name"):
            result["sampler"] = val
        elif key == "model":
            result["model"] = val
        elif key == "model_hash":
            result["model_hash"] = val
        elif key == "size":
            result["size"] = val
        elif key == "vae":
            result["vae"] = val

    return result


def _extract_comfyui_prompt(prompt_data: dict) -> str | None:
    """Try to extract a human-readable prompt from ComfyUI prompt JSON."""
    texts = []
    for node in prompt_data.values():
        cls = node.get("class_type", "")
        inputs = node.get("inputs", {})
        if cls in ("CLIPTextEncode", "CLIPTextEncodeSDXL") and "text" in inputs:
            t = inputs["text"]
            if isinstance(t, str) and t.strip():
                texts.append(t.strip())
    return "\n".join(texts) if texts else None


def extract_generation_metadata(path: str) -> dict | None:
    """Extract AI generation parameters from PNG text chunks or EXIF."""
    try:
        img = Image.open(path)
    except Exception:
        return None

    info = getattr(img, "info", {}) or {}

    # A1111 / sd-webui style
    if "parameters" in info:
        raw = info["parameters"]
        if isinstance(raw, str) and raw.strip():
            return _parse_a1111_params(raw)

    # ComfyUI stores workflow JSON + prompt JSON
    if "workflow" in info or "prompt" in info:
        result: dict = {"source": "comfyui"}
        if "workflow" in info:
            try:
                result["comfyui_workflow"] = json.loads(info["workflow"])
            except Exception:
                result["raw"] = info["workflow"]
        if "prompt" in info:
            try:
                prompt_data = json.loads(info["prompt"])
                extracted = _extract_comfyui_prompt(prompt_data)
                if extracted:
                    result["prompt"] = extracted
            except Exception:
                pass
        return result if len(result) > 1 else None

    # Generic "Comment" text chunk (used by some tools)
    if "Comment" in info:
        comment = info["Comment"]
        if isinstance(comment, str) and comment.strip():
            try:
                parsed = json.loads(comment)
                if isinstance(parsed, dict):
                    return {"source": "unknown", "raw": comment, **parsed}
            except Exception:
                pass
            return {"source": "unknown", "raw": comment}

    # EXIF UserComment (tag 37510) — some tools write here
    try:
        exif = img._getexif() or {}
        user_comment = exif.get(37510)
        if user_comment:
            if isinstance(user_comment, bytes):
                # Strip EXIF ASCII/Unicode header prefix if present
                user_comment = user_comment.decode("utf-8", errors="replace").lstrip("\x00")
            if user_comment.strip():
                if "Steps:" in user_comment:
                    return _parse_a1111_params(user_comment)
                return {"source": "unknown", "raw": user_comment.strip()}
    except Exception:
        pass

    return None


# --- Source & license provenance capture -----------------------------------
#
# Both helpers return a dict of Image provenance columns (or None), and are
# called from the ingest executor hop — never on the event loop. Values are
# only ever used to fill fields the caller has not already set: request-supplied
# provenance wins, then the sidecar, then EXIF (see _ingest_file_sync).

_EXIF_ARTIST = 315
_EXIF_COPYRIGHT = 33432
# PNG has no EXIF Artist/Copyright; the equivalents are tEXt chunks, read from
# `img.info` exactly as extract_generation_metadata does a few functions above.
_PNG_ATTRIBUTION_KEYS = ("Author", "Artist", "Copyright", "Attribution")


def extract_embedded_provenance(path: str) -> dict | None:
    """Read embedded attribution into an `attribution` string.

    EXIF Artist (315) / Copyright (33432) for JPEG/TIFF, and the `Author` /
    `Copyright` PNG tEXt chunks for PNG — a PNG carries no EXIF Artist tag at all,
    so reading only EXIF silently captured nothing for the most common format here.

    Deliberately never sets `license`: a copyright notice is a rights *assertion*,
    not a license id, and guessing one would put unverified images into the
    commercial-use bucket. Returns None when nothing is present.
    """
    def _clean(v) -> str:
        if isinstance(v, bytes):
            v = v.decode("utf-8", errors="replace")
        return str(v).replace("\x00", "").strip() if v else ""

    parts: list[str] = []
    try:
        with Image.open(path) as img:
            exif = img.getexif() or {}
            parts = [_clean(exif.get(_EXIF_ARTIST)), _clean(exif.get(_EXIF_COPYRIGHT))]
            info = img.info or {}
            parts += [_clean(info.get(k)) for k in _PNG_ATTRIBUTION_KEYS]
    except Exception:
        return None

    parts = [p for p in parts if p]
    if not parts:
        return None
    return {"attribution": " — ".join(dict.fromkeys(parts))}


# gallery-dl / Grabber sidecar keys, in precedence order per target field.
# source_url prefers the citable *page* over the direct file: a `file_url` is
# routinely a signed, rotating CDN blob that is dead by the time anyone reads
# CREDITS.md, while post_url/page_url identify the post permanently.
_SIDECAR_KEYS: dict[str, tuple[str, ...]] = {
    "source_url": ("post_url", "page_url", "source", "url", "file_url"),
    "attribution": ("author", "uploader", "artist", "creator", "owner", "user"),
    "license": ("license", "license_name", "rights"),
}
# Keys that identify the site rather than the post.
_SOURCE_NAME_KEYS = ("category", "site", "service", "subcategory")

# Every key that makes a JSON file recognisable as a provenance sidecar.
_RECOGNISED_SIDECAR_KEYS = frozenset(
    k for keys in _SIDECAR_KEYS.values() for k in keys
) | frozenset(_SOURCE_NAME_KEYS)

# A provenance sidecar is a few KB of scraper metadata. Anything larger is some
# other JSON file that happens to share the stem, and read_text()-ing it would
# pull an arbitrary amount of the user's disk into an executor hop and then into
# a DB column.
_SIDECAR_MAX_BYTES = 256 * 1024


def _sidecar_str(payload: dict, keys: tuple[str, ...]) -> str:
    """First non-empty value among `keys`; dicts are probed for a name-ish field."""
    for k in keys:
        v = payload.get(k)
        if isinstance(v, dict):  # gallery-dl nests {"user": {"name": ...}}
            v = v.get("name") or v.get("username") or v.get("title") or ""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def read_provenance_sidecar(image_path: Path | str) -> dict | None:
    """Read a scraper sidecar (gallery-dl style) into provenance fields.

    Both layouts are accepted, in this order: `pic.png.json` (filename + `.json`,
    what gallery-dl's metadata postprocessor writes by default) and `pic.json`
    (extension replaced). Checking only one silently skips half the real-world
    scrape folders.

    Known keys map onto source_name/source_url/attribution/license; the whole raw
    payload is kept under `source_meta` so nothing in the long tail (post id,
    scrape date, tags) is lost. Unparseable, non-object, oversized or
    unrecognisable JSON returns None rather than raising — a bad sidecar must
    never fail an import, and an unrelated one must never be adopted as
    provenance.

    The `pic.json` layout is only accepted when the payload carries at least one
    recognised provenance key, because that name is ambiguous: a ComfyUI
    `workflow.json` sits next to `workflow.png`, a project `metadata.json` next to
    `metadata.jpg`, and one `12345.json` is claimed by both `12345.png` and
    `12345.jpg`. The `pic.png.json` layout keeps the filename's extension, so it
    is unambiguous and is accepted as-is.
    """
    from backend.licenses import normalize_license

    src = Path(image_path)
    exact = src.with_name(src.name + ".json")
    ambiguous = src.with_suffix(".json")
    side = next((p for p in (exact, ambiguous) if p.exists()), None)
    if side is None:
        return None
    try:
        if side.stat().st_size > _SIDECAR_MAX_BYTES:
            return None
        payload = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if side == ambiguous and not (_RECOGNISED_SIDECAR_KEYS & payload.keys()):
        return None

    out: dict = {}
    source_name = _sidecar_str(payload, _SOURCE_NAME_KEYS)
    if source_name:
        out["source_name"] = source_name
    for field, keys in _SIDECAR_KEYS.items():
        value = _sidecar_str(payload, keys)
        if value:
            out[field] = value
    if "license" in out:
        # Unrecognised strings survive as other:<raw> instead of being dropped.
        out["license"] = normalize_license(out["license"])
        if not out["license"]:
            del out["license"]
    out["source_meta"] = payload
    return out


def get_image_info(path: str) -> dict:
    import imagehash  # lazy: pulls scipy/PyWavelets; keep out of the module import graph

    try:
        img = _open_safe(path)
        return {
            "width": img.width,
            "height": img.height,
            "format": img.format or Path(path).suffix.lstrip(".").upper(),
            "file_size_bytes": Path(path).stat().st_size,
            "phash": str(imagehash.phash(img)),
        }
    except Exception:
        return {}


def generate_thumbnail(src_path: str, dest_path: str, size: int = 256) -> None:
    img = _open_safe(src_path)
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(dest_path, "WEBP", quality=85)


def resize_image(
    path: str,
    width: int | None = None,
    height: int | None = None,
    scale: float | None = None,
    maintain_ar: bool = True,
    resample: str = "LANCZOS",
) -> tuple[int, int]:
    img = _open_safe(path)
    orig_w, orig_h = img.width, img.height
    resampler = RESAMPLE_MAP.get(resample, Image.Resampling.LANCZOS)

    if scale is not None:
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)
    elif width and height:
        if maintain_ar:
            ratio = min(width / orig_w, height / orig_h)
            new_w = int(orig_w * ratio)
            new_h = int(orig_h * ratio)
        else:
            new_w, new_h = width, height
    elif width:
        new_w = width
        new_h = int(orig_h * (width / orig_w)) if maintain_ar else orig_h
    elif height:
        new_h = height
        new_w = int(orig_w * (height / orig_h)) if maintain_ar else orig_w
    else:
        raise ValueError("Provide width, height, or scale")

    resized = img.resize((new_w, new_h), resampler)
    resized.save(path, **_save_kwargs(path))
    return new_w, new_h


def crop_image_to_dest(
    src_path: str,
    dest_path: str,
    x: int,
    y: int,
    width: int,
    height: int,
    output_width: int | None = None,
    output_height: int | None = None,
) -> dict:
    import imagehash  # lazy: pulls scipy/PyWavelets; keep out of the module import graph

    img = _open_safe(src_path)
    cropped = img.crop((x, y, x + width, y + height))
    img.close()
    if output_width is not None and output_height is not None:
        cropped = cropped.resize((output_width, output_height), Image.Resampling.LANCZOS)
    elif output_width is not None:
        oh = round(cropped.height * (output_width / cropped.width))
        cropped = cropped.resize((output_width, oh), Image.Resampling.LANCZOS)
    elif output_height is not None:
        ow = round(cropped.width * (output_height / cropped.height))
        cropped = cropped.resize((ow, output_height), Image.Resampling.LANCZOS)
    w, h = cropped.width, cropped.height
    phash_str = str(imagehash.phash(cropped))
    cropped.save(dest_path, **_save_kwargs(dest_path))
    return {
        "width": w,
        "height": h,
        "format": Path(dest_path).suffix.lstrip(".").upper(),
        "file_size_bytes": Path(dest_path).stat().st_size,
        "phash": phash_str,
    }


def crop_to_aspect(
    path: str, target_ar: float, strategy: str = "center"
) -> tuple[int, int, tuple[int, int, int, int], tuple[int, int]]:
    """Center/top crop an image to ``target_ar`` in place.

    Returns ``(new_w, new_h, rect, orig_size)`` where ``rect`` is the crop
    rectangle ``(x, y, w, h)`` and ``orig_size`` the pre-crop ``(w, h)``, both in
    the EXIF-transposed frame (``_open_safe``). The extra return values let the
    single caller (``routers/images.py::batch_crop``) remap detections through the
    crop.
    """
    img = _open_safe(path)
    orig_w, orig_h = img.width, img.height
    current_ar = orig_w / orig_h

    if current_ar > target_ar:
        new_w = int(orig_h * target_ar)
        new_h = orig_h
    else:
        new_w = orig_w
        new_h = int(orig_w / target_ar)

    if strategy == "center":
        x = (orig_w - new_w) // 2
        y = (orig_h - new_h) // 2
    else:
        x, y = 0, 0

    cropped = img.crop((x, y, x + new_w, y + new_h))
    cropped.save(path, **_save_kwargs(path))
    return cropped.width, cropped.height, (x, y, new_w, new_h), (orig_w, orig_h)


def convert_and_save(src_path: str, dest_path: str, fmt: str = "PNG", quality: int = 95) -> None:
    img = _open_safe(src_path)
    if fmt.upper() == "JPEG" and img.mode == "RGBA":
        img = img.convert("RGB")
    img.save(dest_path, fmt.upper(), quality=quality)
