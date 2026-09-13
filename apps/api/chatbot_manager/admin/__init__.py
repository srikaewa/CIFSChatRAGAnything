from fastapi import APIRouter

from .bots import router as bots_router
from .routes import router as legacy_router


router = APIRouter()
router.include_router(legacy_router)
router.include_router(bots_router)

__all__ = ["router"]
