import io

from PIL import Image, ImageOps


def open_rgb_bytes(image_bytes: bytes) -> Image.Image:
    """`open_rgb` for an in-memory image, for callers that never touch disk.

    Exists so the bytes and path forms of the *same* embedding cannot drift: a
    reference image uploaded as bytes is compared against embeddings extracted
    from stored paths, so if only one of the two EXIF-transposes, style
    similarity silently compares two different orientations of one picture.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return ImageOps.exif_transpose(img)


def open_rgb(image_path: str) -> Image.Image:
    """The single sanctioned open for ML inference paths.

    Opens the image, converts to RGB, and applies ``ImageOps.exif_transpose`` so
    every predictor sees pixels in the same EXIF-transposed frame. Normalized
    point/box coordinates therefore denormalize consistently. Callers own closing
    the returned image (call ``img.close()`` right after handing pixels to the
    model's preprocessor, per the "Close PIL Images after preprocessing" invariant).
    """
    img = Image.open(image_path).convert("RGB")
    return ImageOps.exif_transpose(img)


def preprocess_for_caption(
    image_path: str,
    target_w: int | None,
    target_h: int | None,
) -> Image.Image:
    """Open image, correct EXIF orientation, and optionally center-crop + resize to target resolution."""
    img = open_rgb(image_path)

    if target_w and target_h:
        target_ar = target_w / target_h
        img_ar = img.width / img.height

        if img_ar > target_ar:
            # Image is wider than target — crop left and right
            new_w = int(img.height * target_ar)
            left = (img.width - new_w) // 2
            img = img.crop((left, 0, left + new_w, img.height))
        elif img_ar < target_ar:
            # Image is taller than target — crop top and bottom
            new_h = int(img.width / target_ar)
            top = (img.height - new_h) // 2
            img = img.crop((0, top, img.width, top + new_h))

        img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    return img
