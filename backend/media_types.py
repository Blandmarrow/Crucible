"""Media file types: the single allowlist of what Crucible will ingest.

Before this module three separate frozensets decided what counted as an image —
one in `routers/filesystem.py`, one in `routers/images.py`, one in
`services/dataset_service.py` — and they had already drifted: only the file
browser's list carried `.avif`, so a folder the browser happily listed imported
as empty. Every extension decision now resolves here.

Flat beside `licenses.py` and `utils.py`: the backend has no `constants/`
package and a couple of frozensets do not justify creating one.

Naming stays `media_kind`-shaped rather than `is_video`-shaped so the eventual
audio arc adds a third value instead of a second boolean. That applies to API
fields and helper signatures only — the *schema* deliberately splits into two
tables (`images`, `videos`), so no `media_kind` column belongs on either.
"""

from PIL import features

# AVIF decode is a *build-time* Pillow feature, not a version guarantee: a
# source build can produce a Pillow 11.3+ with it absent, which would recreate
# the exact drift this module exists to remove, one layer down. The pin in
# requirements.txt states intent; this gate enforces reality.
_AVIF = {".avif"} if features.check("avif") else set()

IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"} | _AVIF
)

# Containers OpenCV can open by header for metadata (see services/video_service.py).
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".avi"})

MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# Browsers pick a decoder from the response Content-Type, and mimetypes.guess_type
# is unreliable for .mkv/.avi across platforms, so map the ones we accept.
_VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
}

# CAP_PROP_FOURCC yields a stable 4-character code. Store it raw on the row and
# map to a display name here, so an unrecognised codec degrades to its own code
# rather than to "unknown".
_CODEC_LABELS = {
    "avc1": "H.264/AVC",
    "h264": "H.264/AVC",
    "hev1": "HEVC",
    "hvc1": "HEVC",
    "hevc": "HEVC",
    "av01": "AV1",
    "vp09": "VP9",
    "VP90": "VP9",
    "VP80": "VP8",
    "mp4v": "MPEG-4",
    "FMP4": "MPEG-4",
    "MJPG": "Motion JPEG",
    "apch": "ProRes 422 HQ",
    "apcn": "ProRes 422",
    "apcs": "ProRes 422 LT",
    "ap4h": "ProRes 4444",
    "theo": "Theora",
    "WMV3": "Windows Media 9",
}


def media_kind_for(suffix: str) -> str | None:
    """`"image"`, `"video"`, or None for anything we do not ingest.

    Takes a suffix with its leading dot (`Path.suffix`) and lowercases it, so no
    caller has to remember to.
    """
    s = suffix.lower()
    if s in IMAGE_EXTENSIONS:
        return "image"
    if s in VIDEO_EXTENSIONS:
        return "video"
    return None


def video_mime(suffix: str) -> str:
    """Content-Type for a video response; octet-stream for anything unmapped."""
    return _VIDEO_MIME.get(suffix.lower(), "application/octet-stream")


def fourcc_to_code(value: int) -> str | None:
    """Decode a raw `CAP_PROP_FOURCC` integer to its 4-character code, or None.

    A registered FOURCC is ASCII by definition, so anything else is a container
    that reported no usable codec rather than a codec we merely do not know. Two
    such values turn up in practice and neither is caught by stripping
    whitespace: `0` decodes to four NUL bytes, and `-1` to four `0xFF` bytes.
    Both are truthy strings, so without this guard they reach the row, the JSON
    response and `codec_label`, which faithfully echoes them back. NULL is the
    honest answer — the same rule the duration guard follows.
    """
    text = "".join(chr((value >> (8 * i)) & 0xFF) for i in range(4)).strip()
    if not text or not text.isascii() or not text.isprintable():
        return None
    return text


def codec_label(fourcc: str | None) -> str:
    """Display name for a stored FOURCC, falling back to the raw code."""
    if not fourcc:
        return ""
    return _CODEC_LABELS.get(fourcc, _CODEC_LABELS.get(fourcc.lower(), fourcc))
