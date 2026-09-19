from fastapi import APIRouter

from app.api.v2.auth import router as auth_router
from app.api.v2.health import router as health_router
from app.api.v2.analyses import router as analyses_router, admin_router as analysis_admin_router
from app.api.v2.admin_catalog import router as admin_catalog_router
from app.api.v2.admin_observe import router as admin_observe_router
from app.api.v2.admin_versions import router as admin_versions_router
from app.api.v2.admin_analytics import router as admin_analytics_router
from app.api.v2.admin_knowledge import router as admin_knowledge_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(health_router)
router.include_router(analyses_router)
router.include_router(analysis_admin_router)
router.include_router(admin_catalog_router)
router.include_router(admin_observe_router)
router.include_router(admin_versions_router)
router.include_router(admin_analytics_router)
router.include_router(admin_knowledge_router)
