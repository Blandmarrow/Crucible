from fastapi import HTTPException


def normalize_subfolder(s: str) -> str:
    """Normalize a subfolder path: strip leading/trailing slashes, reject '..' segments."""
    parts = [p for p in s.replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise HTTPException(400, "Subfolder path must not contain '..'")
    return "/".join(parts)
