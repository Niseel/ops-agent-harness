from fastapi import APIRouter

from app.config import settings

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "llm_default": settings.llm_default}
