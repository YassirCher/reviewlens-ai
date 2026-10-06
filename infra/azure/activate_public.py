"""Enable the new deployment only after checking its published spend limits."""
import uuid
from sqlalchemy import select
from app.db.models import ActiveConfiguration, BudgetPolicyVersion
from app.db.session import session_scope
from app.services.audit_service import add_audit_event

with session_scope() as db:
    active = db.scalar(select(ActiveConfiguration).where(ActiveConfiguration.id == 1).with_for_update())
    if active is None or active.environment != "production" or active.kill_switch:
        raise SystemExit("Production configuration is missing or the kill switch is active.")
    budget = db.get(BudgetPolicyVersion, active.budget_policy_version_id)
    if budget is None or budget.lifecycle != "published" or budget.public_daily_cost_cap_usd > 1 or budget.public_run_cost_cap_usd > 0.5:
        raise SystemExit("The deployment spend limits have not been verified.")
    if not active.public_analysis_enabled:
        active.public_analysis_enabled = True
        add_audit_event(db, action="deployment.public_analysis_enabled", actor_type="system",
                        target_type="active_configuration", target_id="1", request_id=uuid.uuid4())
print("Public analysis enabled with verified production spend limits.")
