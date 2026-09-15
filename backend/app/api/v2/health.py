from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v2.dependencies import require_admin
from app.platform.health import collect_health
from app.services.admin_auth import AuthenticatedAdmin

router = APIRouter(prefix="/admin/system", tags=["system"])


@router.get("/health")
def system_health(_: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    return collect_health().to_dict(detailed=True)
