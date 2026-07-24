import asyncio
import logging

from backend.ml.image_utils import preprocess_for_caption
from backend.ml import device as _device

logger = logging.getLogger(__name__)

STYLE_PROMPTS = {
    "descriptive": "Write a detailed description for this image.",
    "casual": "Write a descriptive caption for this image in a casual tone.",
    "straightforward": "Write a straightforward caption that accurately represents the main subject, medium, style, and key visual elements of the image without speculation.",
    "sd_prompt": "Output a stable diffusion prompt that is indistinguishable from a real stable diffusion prompt.",
    "midjourney": "Write a MidJourney prompt for this image.",
    "danbooru": "Write a list of Danbooru tags for this image.",
    "e621": "Write a list of e621 tags for this image.",
    "rule34": "Write a list of Rule34 tags for this image.",
    "booru_like": "Write a list of Booru-like tags for this image.",
    "art_critic": "Analyze this image like an art critic would with information about its composition, style, symbolism, the use of color and light, any artistic movement it might belong to, etc.",
    "product": "Write a caption for this image as though it were a product listing.",
    "social_media": "Write a caption for this image as if it were being used for a social media post.",
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

    convo = [
        {"role": "system", "content": "You are a helpful image captioner."},
        {"role": "user", "content": prompt},
    ]
    convo_str = processor.apply_chat_template(convo, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[convo_str], images=[img], return_tensors="pt").to(_device.get_device())
    img.close()

    # LlavaForConditionalGeneration requires pixel_values in bfloat16 explicitly.
    _jc_dtype = _device.safe_dtype_for_device(torch.bfloat16)
    inputs["pixel_values"] = inputs["pixel_values"].to(_jc_dtype)

    try:
        with torch.no_grad():
            generated_ids = model.generate(
                **inputs,
                max_new_tokens=512,
                do_sample=True,
                temperature=0.6,
                top_p=0.9,
                pad_token_id=processor.tokenizer.eos_token_id,
            )
        input_len = inputs["input_ids"].shape[-1]
        result = processor.batch_decode(
            generated_ids[:, input_len:], skip_special_tokens=True
        )[0]
        return result.strip()
    except (torch.cuda.OutOfMemoryError, RuntimeError) as _e:
        if not _device.is_oom_error(_e):
            raise
        _device.empty_cache()
        raise RuntimeError("GPU out of memory during JoyCaption inference")


async def caption_image(
    image_path: str,
    model_entry,
    style: str = "descriptive",
    custom_prompt: str = "",
    target_w: int | None = None,
    target_h: int | None = None,
) -> str:
    prompt = custom_prompt.strip() if custom_prompt.strip() else STYLE_PROMPTS.get(style, STYLE_PROMPTS["descriptive"])
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, infer_sync, image_path, model_entry, prompt, target_w, target_h)
