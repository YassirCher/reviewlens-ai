from fastapi import APIRouter

from app.api.v2.auth import router as auth_router
from app.api.v2.health import router as health_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(health_router)
