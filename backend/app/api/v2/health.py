from __future__ import annotations

from fastapi import APIRouter, Depends

from sqlalchemy.orm import Session

from app.api.v2.dependencies import get_v2_db, require_admin
from app.platform.alerts import collect_operational_alerts
from app.platform.health import collect_health
from app.services.admin_auth import AuthenticatedAdmin

router = APIRouter(prefix="/admin/system", tags=["system"])


@router.get("/health")
def system_health(
    _: AuthenticatedAdmin = Depends(require_admin),
    db: Session = Depends(get_v2_db),
) -> dict:
    payload = collect_health().to_dict(detailed=True)
    payload["operations"] = collect_operational_alerts(db).model_dump(mode="json")
    return payload
