from fastapi import APIRouter

from .analytics import router as analytics_router
from .audit import router as audit_router
from .auth import router as auth_router
from .bots import router as bots_router
from .command_center import router as command_center_router
from .conversations import router as conversations_router
from .incidents import router as incidents_router
from .knowledge_services import router as knowledge_services_router
from .test_center import router as test_center_router
from .users import router as users_router


router = APIRouter()
router.include_router(auth_router)
router.include_router(command_center_router)
router.include_router(bots_router)
router.include_router(test_center_router)
router.include_router(conversations_router)
router.include_router(knowledge_services_router)
router.include_router(analytics_router)
router.include_router(incidents_router)
router.include_router(audit_router)
router.include_router(users_router)

__all__ = ["router"]
