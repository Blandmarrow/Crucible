from fastapi import APIRouter

router = APIRouter(prefix="/models", tags=["models"])


@router.post("/unload-all")
async def unload_all_models():
    """Evict all ML models from VRAM. Call after quality scoring to free memory."""
    from backend.ml.model_manager import model_manager
    evicted = await model_manager.evict_all()
    return {"status": "ok", "unloaded": evicted}
