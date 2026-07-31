"""The consolidated media allowlist in backend/media_types.py.

Before consolidation three separate frozensets decided what was importable and
they had already drifted: only the file browser's carried `.avif`, so a folder
the browser listed happily imported as empty. These tests pin the properties
that made the drift possible, so a future edit that reintroduces a local
extension set has to break one of them first.
"""

from PIL import features

from backend.media_types import (
    IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    VIDEO_EXTENSIONS,
    codec_label,
    fourcc_to_code,
    media_kind_for,
)


def test_image_and_video_sets_are_disjoint():
    """A suffix must resolve to exactly one kind — media_kind_for checks images
    first, so an overlap would silently make a video import as an image."""
    assert IMAGE_EXTENSIONS & VIDEO_EXTENSIONS == frozenset()
    assert MEDIA_EXTENSIONS == IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def test_every_extension_is_lowercase_and_dotted():
    """media_kind_for lowercases its argument and compares against these sets, so
    an entry stored without a leading dot or with a capital can never match."""
    for ext in MEDIA_EXTENSIONS:
        assert ext.startswith("."), ext
        assert ext == ext.lower(), ext


def test_the_formerly_divergent_image_types_are_all_present():
    """The union of what the three old allowlists accepted. `.avif` was in the
    file browser's list only; it resolves upward, which is what makes a folder
    of AVIFs importable rather than merely browsable."""
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"):
        assert ext in IMAGE_EXTENSIONS


def test_avif_tracks_the_pillow_build_not_the_version_pin():
    """AVIF is a build-time Pillow feature: a source build can lack it at 11.3+.
    Listing it unconditionally would offer an import that then fails to decode."""
    assert (".avif" in IMAGE_EXTENSIONS) is features.check("avif")


def test_media_kind_for_classifies_and_is_case_insensitive():
    assert media_kind_for(".png") == "image"
    assert media_kind_for(".MP4") == "video"
    assert media_kind_for(".JPEG") == "image"
    assert media_kind_for(".mkv") == "video"


def test_media_kind_for_rejects_everything_else():
    """None is the ingest rejection. `.txt` matters specifically: caption
    sidecars sit beside images in every import folder."""
    for ext in (".txt", ".json", ".psd", ".mp3", "", ".exe"):
        assert media_kind_for(ext) is None


def test_codec_label_falls_back_to_the_raw_fourcc():
    """Video rows store the raw CAP_PROP_FOURCC code. An unknown codec must
    still render as something — its own code — never as an empty cell."""
    assert codec_label("avc1") == "H.264/AVC"
    assert codec_label("FMP4") == "MPEG-4"
    assert codec_label("zzzz") == "zzzz"
    assert codec_label(None) == ""
    assert codec_label("") == ""


def test_fourcc_to_code_decodes_real_codes():
    """Little-endian, four bytes, as CAP_PROP_FOURCC packs them."""
    def pack(code: str) -> int:
        return sum(ord(c) << (8 * i) for i, c in enumerate(code))

    assert fourcc_to_code(pack("avc1")) == "avc1"
    assert fourcc_to_code(pack("FMP4")) == "FMP4"
    assert fourcc_to_code(pack("apch")) == "apch"


def test_fourcc_to_code_rejects_the_no_codec_sentinels():
    """The two values a container reports when it has no usable code. Neither is
    caught by stripping whitespace: 0 decodes to four NULs (ascii, not
    printable) and -1 to four 0xFFs (printable, not ascii), and both are truthy
    strings that would otherwise be stored on the row and echoed by codec_label
    into the API response."""
    assert fourcc_to_code(0) is None
    assert fourcc_to_code(-1) is None


def test_fourcc_to_code_rejects_partially_binary_codes():
    """A trailing NUL byte is enough — a 3-char code padded with a NUL is not a
    registered FOURCC, and half-printable is still unrenderable."""
    assert fourcc_to_code(sum(ord(c) << (8 * i) for i, c in enumerate("H26")) ) is None
