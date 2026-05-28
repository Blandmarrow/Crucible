import asyncio
import logging
from PIL import Image

from backend.ml.image_utils import preprocess_for_caption

logger = logging.getLogger(__name__)

STYLE_PROMPTS = {
    "detailed": "<MORE_DETAILED_CAPTION>",
    "short": "<CAPTION>",
    "tags": "<GENERATE_TAGS>",
    "dense": "<DENSE_REGION_CAPTION>",
    "promptgen": "<GENERATE_PROMPT>",  # PromptGen variant
}


def _move_inputs_to_cuda(model, inputs: dict) -> dict:
    model_dtype = next(model.parameters()).dtype
    return {
        k: (v.to("cuda", dtype=model_dtype) if v.is_floating_point() else v.to("cuda"))
        if hasattr(v, "to") else v
        for k, v in inputs.items()
    }


def infer_sync(
    image_path: str,
    model_entry,
    prompt: str,
    target_w: int | None = None,
    target_h: int | None = None,
) -> str:
    import torch
    model = model_entry.model
    processor = model_entry.processor

    img = preprocess_for_caption(image_path, target_w, target_h)
    img_w, img_h = img.width, img.height
    inputs = processor(text=prompt, images=img, return_tensors="pt")
    img.close()
    inputs = _move_inputs_to_cuda(model, inputs)

    try:
        with torch.no_grad():
            generated_ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs.get("pixel_values"),
                max_new_tokens=1024,
                num_beams=3,
            )
        generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            generated_text,
            task=prompt,
            image_size=(img_w, img_h),
        )
        result = parsed.get(prompt, "")
        if isinstance(result, dict):
            result = str(result)
        return str(result).strip()
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        raise RuntimeError("GPU out of memory during Florence-2 inference")


async def caption_image(
    image_path: str,
    model_entry,
    style: str = "detailed",
    target_w: int | None = None,
    target_h: int | None = None,
) -> str:
    prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["detailed"])
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, infer_sync, image_path, model_entry, prompt, target_w, target_h)


def infer_sync_detection(
    image_path: str,
    model_entry,
    task: str,
    text_input: str = "",
) -> list[dict]:
    """Run a Florence-2 detection task and return normalized bounding boxes.

    task: "<OD>" for object detection, "<CAPTION_TO_PHRASE_GROUNDING>" for grounded caption.
    text_input: caption string for grounding; unused for OD.
    Returns list of {"label": str, "bbox": [x1, y1, x2, y2]} with coords in 0-1 range.
    """
    import torch
    model = model_entry.model
    processor = model_entry.processor

    img = preprocess_for_caption(image_path, None, None)
    img_w, img_h = img.width, img.height
    task_text = task + text_input
    inputs = processor(text=task_text, images=img, return_tensors="pt")
    img.close()
    inputs = _move_inputs_to_cuda(model, inputs)

    try:
        with torch.no_grad():
            generated_ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs.get("pixel_values"),
                max_new_tokens=1024,
                num_beams=3,
            )
        generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            generated_text,
            task=task,
            image_size=(img_w, img_h),
        )
        raw = parsed.get(task, {})
        bboxes = raw.get("bboxes", [])
        # <OD> uses "labels"; <CAPTION_TO_PHRASE_GROUNDING> uses "bboxes_labels"
        labels = raw.get("labels") or raw.get("bboxes_labels", [])
        return [
            {"label": lbl, "bbox": [b[0] / img_w, b[1] / img_h, b[2] / img_w, b[3] / img_h]}
            for lbl, b in zip(labels, bboxes)
            if lbl
        ]
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        raise RuntimeError("GPU out of memory during Florence-2 detection")


async def detect_image(
    image_path: str,
    model_entry,
    task: str,
    text_input: str = "",
) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, infer_sync_detection, image_path, model_entry, task, text_input)


async def caption_batch(
    image_paths: list[str],
    model_entry,
    style: str = "detailed",
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
        try:
            caption = await caption_image(path, model_entry, style, target_w, target_h)
        except Exception as e:
            logger.error("Florence-2 failed on %s: %s", path, e)
            caption = ""
        results.append(caption)

        if job_id:
            elapsed = time.monotonic() - start_time
            throughput = round((i + 1) / elapsed, 2) if elapsed > 0 else 0
            try:
                import torch
                vram_mb = int(torch.cuda.memory_reserved() / 1024 / 1024) if torch.cuda.is_available() else 0
            except Exception:
                vram_mb = 0
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "caption",
                "status": "running", "done": i + 1, "total": total,
                "percent": round((i + 1) / total * 100, 1),
                "current_item": path.split("/")[-1],
                "message": f"Florence-2: {i + 1}/{total}",
                "throughput_ips": throughput,
                "vram_used_mb": vram_mb,
            })

    return results
