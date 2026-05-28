import asyncio
import base64
import logging
from pathlib import Path

from PIL import Image, ImageOps

from backend.ml.image_utils import preprocess_for_caption

logger = logging.getLogger(__name__)

STYLE_PROMPTS = {
    "detailed": "Describe this image in rich detail, covering subjects, setting, lighting, style, and mood.",
    "short": "Briefly describe this image in one or two sentences.",
    "tags": "List the key elements in this image as comma-separated tags.",
    "booru": "Describe this image using booru-style tags (e.g. 1girl, solo, long_hair). Output only comma-separated tags.",
}


def _encode_image(image_path: str, max_px: int, target_w: int | None, target_h: int | None) -> str:
    img = preprocess_for_caption(image_path, target_w, target_h)
    try:
        if max(img.width, img.height) > max_px:
            ratio = max_px / max(img.width, img.height)
            resized = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)
            img.close()
            img = resized
        buf = __import__("io").BytesIO()
        img.save(buf, format="JPEG", quality=90)
    finally:
        img.close()
    return base64.b64encode(buf.getvalue()).decode()


def _caption_sync(
    image_path: str,
    base_url: str,
    api_key: str,
    model_name: str,
    prompt: str,
    max_px: int,
    max_tokens: int,
    target_w: int | None,
    target_h: int | None,
) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai package not installed; run: pip install openai>=1.0")
        return ""

    b64 = _encode_image(image_path, max_px, target_w, target_h)
    client = OpenAI(base_url=base_url, api_key=api_key or "no-key", timeout=120.0)
    resp = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


async def caption_image(
    image_path: str,
    base_url: str,
    api_key: str,
    model_name: str,
    style: str = "detailed",
    custom_prompt: str = "",
    max_px: int = 1024,
    max_tokens: int = 2048,
    target_w: int | None = None,
    target_h: int | None = None,
) -> str:
    prompt = custom_prompt or STYLE_PROMPTS.get(style, STYLE_PROMPTS["detailed"])
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _caption_sync, image_path, base_url, api_key, model_name, prompt, max_px, max_tokens, target_w, target_h
    )
