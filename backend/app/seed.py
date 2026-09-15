from __future__ import annotations

import uuid

from argon2 import extract_parameters
from sqlalchemy import select

from app.config import Settings, settings
from app.db.models import ActiveConfiguration, AdminUser, OpenRouterAccountState
from app.db.session import session_scope
from app.security import normalize_admin_identifier
from app.services.audit_service import add_audit_event


def seed_foundation(config: Settings = settings) -> str:
    errors = config.v2_configuration_errors("migrate")
    if errors:
        raise RuntimeError("Invalid V2 configuration: " + "; ".join(errors))
    try:
        extract_parameters(config.admin_password_hash)
    except Exception as exc:
        raise RuntimeError("ADMIN_PASSWORD_HASH must be a valid Argon2id hash") from exc

    identifier = normalize_admin_identifier(config.admin_email)
    with session_scope() as db:
        admins = list(db.scalars(select(AdminUser).order_by(AdminUser.created_at)))
        if not admins:
            admin = AdminUser(identifier=identifier, password_hash=config.admin_password_hash)
            db.add(admin)
            db.flush()
            add_audit_event(
                db,
                action="system.admin_seeded",
                actor_type="system",
                target_type="admin_user",
                target_id=str(admin.id),
                request_id=uuid.uuid4(),
            )
            result = "admin_created"
        elif len(admins) == 1 and admins[0].identifier == identifier and admins[0].password_hash == config.admin_password_hash:
            result = "already_seeded"
        else:
            raise RuntimeError(
                "The database already contains a different admin. Use an explicit credential rotation workflow."
            )

        active = db.get(ActiveConfiguration, 1)
        if active is None:
            db.add(
                ActiveConfiguration(
                    id=1,
                    environment=config.app_env,
                    public_analysis_enabled=config.public_analysis_enabled,
                    feature_flags={},
                )
            )
        elif active.environment != config.app_env:
            raise RuntimeError("Active configuration belongs to a different APP_ENV")

        account_state = db.get(OpenRouterAccountState, 1)
        if account_state is None:
            db.add(
                OpenRouterAccountState(
                    id=1,
                    environment=config.app_env,
                    status="unknown",
                )
            )
        elif account_state.environment != config.app_env:
            raise RuntimeError("OpenRouter account state belongs to a different APP_ENV")
    return result
