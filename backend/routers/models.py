from fastapi import APIRouter

router = APIRouter(prefix="/models", tags=["models"])


@router.post("/unload-all")
async def unload_all_models():
    """Evict every idle ML model from VRAM.

    A model a running job holds a lease on stays resident and is listed under
    `in_use`; unloading it would fail every image the job has left (GPT-032).
    """
    from backend.ml.model_manager import model_manager
    evicted = await model_manager.evict_all()
    return {"status": "ok", "unloaded": evicted, "in_use": model_manager.leased_ids()}
