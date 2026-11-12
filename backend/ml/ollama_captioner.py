import asyncio
import base64
import logging

import httpx
from PIL import Image

from backend.config import settings
from backend.ml.image_utils import preprocess_for_caption

logger = logging.getLogger(__name__)

# The transport every Ollama client here is built on. None is httpx's real one;
# tests set a `httpx.MockTransport` (the autouse `_no_real_llm_server` fixture
# refuses every request), so no test reaches a developer's own Ollama.
_HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None

# One request's ceiling. A job's requests share one client (M10 T2), and a
# request the server queues behind the ones in flight spends its wait here too.
CAPTION_TIMEOUT_S = 300

STYLE_PROMPTS = {
    "detailed": "Describe this image in rich detail, covering subjects, setting, lighting, style, and mood.",
    "short": "Briefly describe this image in one or two sentences.",
    "tags": "List the key elements in this image as comma-separated tags.",
    "booru": "Describe this image using booru-style tags (e.g. 1girl, solo, long_hair). Output only comma-separated tags.",
}


def _resize_for_ollama(
    path: str,
    max_px: int = 1024,
    target_w: int | None = None,
    target_h: int | None = None,
) -> str:
    img = preprocess_for_caption(path, target_w, target_h)
    if max(img.width, img.height) > max_px:
        ratio = max_px / max(img.width, img.height)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)
    buf = __import__("io").BytesIO()
    img.save(buf, format="JPEG", quality=90)
    img.close()
    return base64.b64encode(buf.getvalue()).decode()


async def list_vision_models() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=5, transport=_HTTP_TRANSPORT) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                # Return models that are likely vision-capable
                return [
                    {"id": f"ollama:{m['name']}", "name": m["name"], "size_mb": m.get("size", 0) // 1_048_576}
                    for m in models
                ]
    except Exception:
        pass
    return []


def make_client() -> httpx.AsyncClient:
    """One client for a whole caption job (M10 T2); the caller closes it."""
    return httpx.AsyncClient(timeout=CAPTION_TIMEOUT_S, transport=_HTTP_TRANSPORT)


async def caption_image(
    image_path: str,
    model_name: str,
    style: str = "detailed",
    custom_prompt: str = "",
    target_w: int | None = None,
    target_h: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Caption one image. `client` is the job's shared client from `make_client`;
    without one, a client is built for this call and closed after it.

    Every failure is logged and returned as "", which the router reports as an
    empty caption. A cancel is not an `Exception`, so it still propagates.
    """
    prompt = custom_prompt or STYLE_PROMPTS.get(style, STYLE_PROMPTS["detailed"])
    b64 = await asyncio.get_running_loop().run_in_executor(
        None, _resize_for_ollama, image_path, settings.ollama_image_max_px, target_w, target_h
    )

    own_client = client is None
    if own_client:
        client = make_client()
    try:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/generate",
            json={
                "model": model_name,
                "prompt": prompt,
                "images": [b64],
                "stream": False,
            },
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        logger.error("Ollama error on %s: %r", image_path, e, exc_info=True)
        return ""
    finally:
        if own_client:
            await client.aclose()


async def caption_batch(
    image_paths: list[str],
    model_name: str,
    style: str = "detailed",
    custom_prompt: str = "",
    job_id: str | None = None,
    target_w: int | None = None,
    target_h: int | None = None,
) -> list[str]:
    import time
    from backend.workers.progress import broadcaster

    results = []
    total = len(image_paths)
    start_time = time.monotonic()

    for i, path in enumerate(image_paths):
        caption = await caption_image(path, model_name, style, custom_prompt, target_w, target_h)
        results.append(caption)

        if job_id:
            elapsed = time.monotonic() - start_time
            throughput = round((i + 1) / elapsed, 2) if elapsed > 0 else 0
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "caption",
                "status": "running", "done": i + 1, "total": total,
                "percent": round((i + 1) / total * 100, 1),
                "current_item": path.split("/")[-1],
                "message": f"Ollama ({model_name}): {i + 1}/{total}",
                "throughput_ips": throughput,
                "vram_used_mb": 0,
            })

    return results
