from fastapi import APIRouter

from .bots import router as bots_router
from .conversations import router as conversations_router
from .knowledge_services import router as knowledge_services_router
from .routes import router as legacy_router


router = APIRouter()
router.include_router(legacy_router)
router.include_router(bots_router)
router.include_router(conversations_router)
router.include_router(knowledge_services_router)

__all__ = ["router"]
