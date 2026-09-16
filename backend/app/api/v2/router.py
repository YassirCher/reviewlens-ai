from fastapi import APIRouter

from app.api.v2.auth import router as auth_router
from app.api.v2.health import router as health_router
from app.api.v2.analyses import router as analyses_router, admin_router as analysis_admin_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(health_router)
router.include_router(analyses_router)
router.include_router(analysis_admin_router)
