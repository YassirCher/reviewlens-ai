from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.admin.agent_models import get_agent_models_state, save_agent_models_assignment
from app.admin.common import require_admin_mutation
from app.api.v2.dependencies import get_v2_db, require_admin
from app.services.admin_auth import AuthenticatedAdmin
from app.services.audit_service import add_audit_event

router = APIRouter(prefix="/admin", tags=["admin-agent-models"])


class UpdateAgentModelsRequest(BaseModel):
    models: dict[str, str] = Field(
        description="Dictionary mapping agent key to model slug. Any missing agent will keep or revert to default."
    )


@router.get("/agent-models")
def get_agent_models(
    db: Session = Depends(get_v2_db),
    _: AuthenticatedAdmin = Depends(require_admin),
) -> dict[str, Any]:
    return get_agent_models_state(db)


@router.put("/agent-models")
def set_agent_models(
    payload: UpdateAgentModelsRequest,
    request: Request,
    db: Session = Depends(get_v2_db),
    admin: AuthenticatedAdmin = Depends(require_admin_mutation),
) -> dict[str, Any]:
    state = save_agent_models_assignment(db, payload.models)
    add_audit_event(
        db,
        action="admin.agent_models_updated",
        actor_type="admin",
        actor_id=admin.admin.id,
        target_type="agent_models",
        target_id=state.get("active_workflow_version_id"),
        request_id=getattr(request.state, "request_id", ""),
        safe_metadata={
            "agent_models": {a["key"]: a["current_model"] for a in state.get("agents", [])}
        },
    )
    db.commit()
    return state
