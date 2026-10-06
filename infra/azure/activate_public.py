"""Enable the new deployment only after checking its published spend limits."""
import uuid
import argparse
from decimal import Decimal
from sqlalchemy import select
from app.admin.configuration import create_draft, update_draft, publish_draft, activate_version, version_payload
from app.config import settings
from app.db.models import ActiveConfiguration, BudgetPolicyVersion
from app.db.session import session_scope
from app.public.admission import _estimate
from app.services.audit_service import add_audit_event

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--pause", action="store_true", help="Pause new public runs without stopping existing reports")
args = parser.parse_args()

with session_scope() as db:
    active = db.scalar(select(ActiveConfiguration).where(ActiveConfiguration.id == 1).with_for_update())
    if active is None or active.environment != "production" or active.kill_switch:
        raise SystemExit("Production configuration is missing or the kill switch is active.")
    if args.pause:
        active.public_analysis_enabled = False
        add_audit_event(db, action="deployment.public_analysis_paused", actor_type="system",
                        target_type="active_configuration", target_id="1", request_id=uuid.uuid4())
        db.commit()
        print("New public analysis paused; existing reports remain readable.")
        raise SystemExit(0)
    budget = db.get(BudgetPolicyVersion, active.budget_policy_version_id)
    if budget is None or budget.lifecycle != "published" or budget.public_daily_cost_cap_usd > 1 or budget.public_run_cost_cap_usd > Decimal("0.80"):
        raise SystemExit("The deployment spend limits have not been verified.")
    if budget.public_run_cost_cap_usd < Decimal("0.80"):
        draft = create_draft(db, "budget-policies", budget.definition_id, budget.id,
                             "Azure demo: support three to five reviews within the daily $1 cap")
        payload = version_payload("budget-policies", draft, db)
        payload["public_run_cost_cap_usd"] = "0.800000"
        update_draft(db, "budget-policies", draft.definition_id, draft.id, payload, draft.version, draft.change_note)
        publish_draft(db, "budget-policies", draft.definition_id, draft.id)
        before, after = activate_version(db, "budget-policies", draft.id)
        add_audit_event(db, action="deployment.budget_configured", actor_type="system",
                        target_type="budget_policy_version", target_id=after, request_id=uuid.uuid4())
        budget = draft
        db.refresh(budget)
    for count in (3, 5):
        _, estimate, _ = _estimate(db, active, budget, {"source_count": count, "analyze_comments": True}, settings)
        if estimate > int(budget.public_run_cost_cap_usd * 1_000_000):
            raise SystemExit("Current model prices exceed the demo admission cap.")
    if not active.public_analysis_enabled:
        active.public_analysis_enabled = True
        add_audit_event(db, action="deployment.public_analysis_enabled", actor_type="system",
                        target_type="active_configuration", target_id="1", request_id=uuid.uuid4())
print("Public analysis enabled with verified production spend limits.")
