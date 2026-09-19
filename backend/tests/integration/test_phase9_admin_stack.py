from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.admin.configuration import create_draft, publish_draft, update_draft, version_payload
from app.admin.evaluation import _create_attribution, evaluate_agent_version
from app.admin.jobs import create_job, recover_jobs
from app.admin.analytics import reconcile_usage_aggregates
from app.analysis.configuration import seed_analysis_configuration
from app.config import settings
from app.db.models import (
    ActiveConfiguration, AdminJob, AdminUser, AgentVersion, AuditEvent, BudgetPolicyVersion,
    EvaluationBudgetState, ModelPolicyVersion, OpenRouterCatalogRefresh, TaskRun, UsageAggregate,
    UsageEvent, WorkflowVersion,
)
from app.db.session import session_scope
from app.knowledge.service import create_workspace
from app.llmops.accounting import BudgetRejected, finalize_failed_request, reserve_request
from app.llmops.catalog import refresh_catalogs
from app.llmops.contracts import OpenRouterErrorCategory
from app.main import app
from app.runtime.service import create_run
from app.errors import V2Error

pytestmark = pytest.mark.skipif(
    os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1"
    or settings.app_env != "test"
    or settings.openrouter_base_url != "http://openrouter-mock:8089/api/v1",
    reason="requires isolated PostgreSQL, Redis, and local OpenRouter mock",
)


def _admin() -> tuple[TestClient, str]:
    client = TestClient(app)
    login = client.post("/api/v2/admin/session", json={
        "email": settings.admin_email, "password": os.environ["PHASE1_TEST_ADMIN_PASSWORD"],
    })
    assert login.status_code == 200, login.text
    csrf = client.get("/api/v2/admin/csrf")
    assert csrf.status_code == 200, csrf.text
    return client, csrf.json()["csrf_token"]


def _seed_and_activate(db) -> dict:
    """Select the checked-in fixture explicitly; production seeding preserves admin activation."""
    seeded = seed_analysis_configuration(db)
    active = db.get(ActiveConfiguration, 1)
    active.workflow_version_id = uuid.UUID(seeded["workflow_version_id"])
    active.budget_policy_version_id = uuid.UUID(seeded["budget_policy_version_id"])
    active.kill_switch = False
    db.flush()
    return seeded


def _wait_for_job(job_id: uuid.UUID, expected: str, timeout: float = 25) -> AdminJob:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_scope() as db:
            job = db.get(AdminJob, job_id)
            if job and job.status == expected:
                db.expunge(job)
                return job
            if job and job.status in {"succeeded", "failed"} and job.status != expected:
                raise AssertionError(f"job reached {job.status}, expected {expected}: {job.error_code}")
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not reach {expected}")


def test_phase9_auth_csrf_and_audit_redaction() -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v2/admin/runs").status_code == 401
        assert anonymous.get("/api/v2/admin/settings").status_code == 401
    client, csrf = _admin()
    with client:
        settings_response = client.get("/api/v2/admin/settings")
        assert settings_response.status_code == 200
        original = bool(settings_response.json()["kill_switch"])
        payload = {"enabled": not original,
                   "confirmation": "disable kill switch" if original else "enable kill switch"}
        denied = client.put("/api/v2/admin/settings/kill-switch", json=payload)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "csrf_validation_failed"
        changed = client.put("/api/v2/admin/settings/kill-switch", json=payload,
                             headers={"X-CSRF-Token": csrf})
        assert changed.status_code == 200, changed.text
        restore = {"enabled": original,
                   "confirmation": "enable kill switch" if original else "disable kill switch"}
        assert client.put("/api/v2/admin/settings/kill-switch", json=restore,
                          headers={"X-CSRF-Token": csrf}).status_code == 200
        audit = client.get("/api/v2/admin/audit-events?action=kill_switch.changed")
        assert audit.status_code == 200
        assert audit.json()["items"]
        assert "password" not in audit.text.casefold()
        assert "csrf" not in audit.text.casefold()
        overview = client.get("/api/v2/admin/overview")
        assert overview.status_code == 200, overview.text
        assert isinstance(overview.json()["operations"]["alerts"], list)
        health = client.get("/api/v2/admin/system/health")
        assert health.status_code == 200, health.text
        assert health.json()["operations"]["worker_available"] is True


def test_phase9_drafts_published_immutability_and_activation_snapshot() -> None:
    import asyncio

    asyncio.run(refresh_catalogs())
    with session_scope() as db:
        _seed_and_activate(db)
        active = db.get(ActiveConfiguration, 1)
        original_id = active.workflow_version_id
        original = db.get(WorkflowVersion, original_id)
        prior_run = create_run(db, product_name="Phase 9 prior snapshot",
                               initiator_type="system_fixture",
                               requested_options={"source_count": 3, "analyze_comments": False})
        prior_snapshot_id = prior_run.configuration_snapshot_id
        draft = create_draft(db, "workflows", original.definition_id, original.id, "Phase 9 activation fixture")
        definition_id, draft_id = original.definition_id, draft.id
        payload = version_payload("workflows", draft, db)
        stale_version = draft.version
        update_draft(db, "workflows", definition_id, draft_id, payload, stale_version, "Edited once")
        with pytest.raises(Exception) as conflict:
            update_draft(db, "workflows", definition_id, draft_id, payload, stale_version, "Stale edit")
        assert getattr(conflict.value, "code", None) == "edit_conflict"
        publish_draft(db, "workflows", definition_id, draft_id)
    with pytest.raises(DBAPIError), session_scope() as db:
        db.get(WorkflowVersion, draft_id).dag = {"tampered": True}

    client, csrf = _admin()
    with client:
        changed = client.post(
            f"/api/v2/admin/configuration/workflows/{definition_id}/versions/{draft_id}/activate",
            json={"confirmation": "analysis_v2", "expected_active_version_id": str(original_id)},
            headers={"X-CSRF-Token": csrf},
        )
        if changed.status_code == 422:
            with session_scope() as db:
                from app.db.models import WorkflowDefinition
                key = db.get(WorkflowDefinition, definition_id).key
            changed = client.post(
                f"/api/v2/admin/configuration/workflows/{definition_id}/versions/{draft_id}/activate",
                json={"confirmation": key, "expected_active_version_id": str(original_id)},
                headers={"X-CSRF-Token": csrf},
            )
        assert changed.status_code == 200, changed.text
    with session_scope() as db:
        from app.db.models import ConfigurationSnapshot
        old_snapshot = db.get(ConfigurationSnapshot, prior_snapshot_id)
        new_run = create_run(db, product_name="Phase 9 new snapshot",
                             initiator_type="system_fixture",
                             requested_options={"source_count": 3, "analyze_comments": False})
        new_snapshot = db.get(ConfigurationSnapshot, new_run.configuration_snapshot_id)
        seed_analysis_configuration(db)
        active = db.get(ActiveConfiguration, 1)
        assert old_snapshot.workflow_version_id == original_id
        assert new_snapshot.workflow_version_id == draft_id
        assert active.workflow_version_id == draft_id
        active.workflow_version_id = original_id


def test_phase9_evaluation_cap_serializes_competing_reservations() -> None:
    import asyncio

    asyncio.run(refresh_catalogs())
    with session_scope() as db:
        seeded = _seed_and_activate(db)
        active = db.get(ActiveConfiguration, 1)
        workflow = db.get(WorkflowVersion, active.workflow_version_id)
        budget = db.get(BudgetPolicyVersion, active.budget_policy_version_id)
        agent = db.get(AgentVersion, uuid.UUID(seeded["agent_versions"]["review_analyst"]))
        policy = db.get(ModelPolicyVersion, agent.model_policy_version_id)
        state = db.get(EvaluationBudgetState, 1)
        original = (state.token_limit, state.cost_limit_microusd)
        state.token_limit = state.consumed_tokens + 800
        state.cost_limit_microusd = state.consumed_cost_microusd + 800
        contexts = [_create_attribution(db, agent, policy, workflow, budget,
                                        uuid.uuid4(), {"fixture": index}) for index in range(2)]

    def reserve(context):
        try:
            return reserve_request(context, operation="chat", retry_number=1,
                                   estimated_tokens=600, estimated_cost_microusd=600,
                                   requested_models=["deepseek/deepseek-v4-flash"])[0]
        except BudgetRejected as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, contexts))
        successes = [item for item in results if isinstance(item, uuid.UUID)]
        assert len(successes) == 1, results
        assert len([item for item in results if item in {
            "evaluation_token_cap_exhausted", "evaluation_cost_cap_exhausted"}]) == 1
        for reservation_id in successes:
            finalize_failed_request(reservation_id, category=OpenRouterErrorCategory.TIMEOUT,
                                    error_code="phase9_test_release")
    finally:
        with session_scope() as db:
            state = db.get(EvaluationBudgetState, 1)
            state.token_limit, state.cost_limit_microusd = original


def test_phase9_mocked_evaluation_gates_publication_and_attributes_usage() -> None:
    import asyncio

    asyncio.run(refresh_catalogs())
    with session_scope() as db:
        seeded = _seed_and_activate(db)
        source = db.get(AgentVersion, uuid.UUID(seeded["agent_versions"]["review_analyst"]))
        draft = create_draft(db, "agents", source.definition_id, source.id, "Phase 9 mocked evaluation")
        definition_id, draft_id = source.definition_id, draft.id
        with pytest.raises(V2Error) as blocked:
            publish_draft(db, "agents", definition_id, draft_id)
        assert blocked.value.code == "evaluation_required", blocked.value.details

    result = evaluate_agent_version(draft_id, uuid.uuid4())
    assert result["status"] == "passed", result
    assert result["metrics"]["case_count"] == 5
    assert result["metrics"]["cases_executed"] == 5
    assert result["metrics"]["schema_valid_rate"] == 1.0
    assert result["metrics"]["central_claim_evidence_linkage"] == 1.0
    assert len(result["run_ids"]) == 5
    with session_scope() as db:
        draft = db.get(AgentVersion, draft_id)
        assert draft.evaluation_metadata["status"] == "passed"
        published = publish_draft(db, "agents", definition_id, draft_id)
        attributed = list(db.scalars(select(UsageEvent).where(UsageEvent.agent_version_id == draft_id)))
        assert published.lifecycle == "published"
        assert len(attributed) >= 5 and all(row.usage_status != "pending" for row in attributed)


def test_phase9_recovery_actions_celery_jobs_and_refresh_failure() -> None:
    import asyncio

    asyncio.run(refresh_catalogs())
    with session_scope() as db:
        _seed_and_activate(db)
        cancel_run = create_run(db, product_name="Phase 9 cancellation recovery",
                                initiator_type="system_fixture",
                                requested_options={"source_count": 3, "analyze_comments": False})
        retry_run = create_run(db, product_name="Phase 9 retry recovery",
                               initiator_type="system_fixture",
                               requested_options={"source_count": 3, "analyze_comments": False})
        retry_task = db.scalar(select(TaskRun).where(TaskRun.run_id == retry_run.id).order_by(TaskRun.created_at))
        retry_task.status = "failed"
        retry_task.current_attempt = 1
        retry_task.completed_at = datetime.now(timezone.utc)
        retry_run.status = "failed"
        retry_run.completed_at = datetime.now(timezone.utc)
        workspace_run = create_run(db, product_name="Phase 9 recovered export",
                                   initiator_type="system_fixture",
                                   requested_options={"source_count": 3, "analyze_comments": False})
        workspace = create_workspace(db, workspace_run.id)
        actor = db.scalar(select(AdminUser).where(AdminUser.identifier == settings.admin_email))
        recovered = create_job(db, actor_id=actor.id, kind="workspace_export",
                               target_id=str(workspace.id), idempotency_key="phase9-recovered-export-job")
        recovered.status = "running"
        recovered.started_at = datetime.now(timezone.utc) - timedelta(minutes=16)
        cancel_id, retry_task_id, recovered_id = cancel_run.id, retry_task.id, recovered.id

    client, csrf = _admin()
    with client:
        provider = client.get("/api/v2/admin/providers/fixture")
        assert provider.status_code == 200, provider.text
        assert provider.json()["slug"] == "fixture"
        endpoints = client.get("/api/v2/admin/models/deepseek/deepseek-v4-flash/endpoints")
        assert endpoints.status_code == 200, endpoints.text
        assert endpoints.json()["endpoints"][0]["eligibility_reasons"] == []
        cancelled = client.post(f"/api/v2/admin/runs/{cancel_id}/cancel",
                                headers={"X-CSRF-Token": csrf})
        assert cancelled.status_code == 200, cancelled.text
        retried = client.post(f"/api/v2/admin/tasks/{retry_task_id}/retry",
                              headers={"X-CSRF-Token": csrf})
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "queued"

        from app.cache import get_redis
        get_redis().delete("reviewlens:admin:catalog-refresh")
        failed_refresh = client.post("/api/v2/admin/models/refresh",
                                     json={"model_slug": "fixture/refresh-failure"},
                                     headers={"X-CSRF-Token": csrf,
                                              "Idempotency-Key": "phase9-failed-refresh-job"})
        assert failed_refresh.status_code == 202, failed_refresh.text
        failed_job_id = uuid.UUID(failed_refresh.json()["job_id"])

    assert recover_jobs() >= 1
    recovered_job = _wait_for_job(recovered_id, "succeeded")
    assert recovered_job.safe_result["download_url"].endswith(f"/{recovered_id}/download")
    failed_job = _wait_for_job(failed_job_id, "failed")
    assert failed_job.error_code
    with session_scope() as db:
        refresh = db.scalar(select(OpenRouterCatalogRefresh).where(
            OpenRouterCatalogRefresh.catalog_kind == "model_endpoints",
            OpenRouterCatalogRefresh.target_slug == "fixture/refresh-failure",
        ).order_by(OpenRouterCatalogRefresh.started_at.desc()))
        actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.target_id.in_((
            str(cancel_id), str(retry_task_id),
        )))))
        assert refresh is not None and refresh.status == "failed"
        assert {"run.cancel_requested", "task.retry_requested"} <= actions


def test_phase9_analytics_reconcile_and_graph_isolation() -> None:
    with session_scope() as db:
        _seed_and_activate(db)
        run_a = create_run(db, product_name="Phase 9 graph A", initiator_type="system_fixture",
                           requested_options={"source_count": 3, "analyze_comments": False})
        run_b = create_run(db, product_name="Phase 9 graph B", initiator_type="system_fixture",
                           requested_options={"source_count": 3, "analyze_comments": False})
        workspace_a = create_workspace(db, run_a.id)
        workspace_b = create_workspace(db, run_b.id)
        a_id, b_id = workspace_a.id, workspace_b.id
        reconcile_usage_aggregates(db)
        ledger = db.scalar(select(func.coalesce(func.sum(UsageEvent.total_cost_microusd), 0)))
        total = db.scalar(select(func.coalesce(func.sum(UsageAggregate.total_cost_microusd), 0)).where(
            UsageAggregate.granularity == "day", UsageAggregate.dimension == "all"))
        assert int(total) == int(ledger)
    client, csrf = _admin()
    with client:
        node = client.post(f"/api/v2/admin/workspaces/{b_id}/nodes", json={
            "node_type": "agent_memory", "title": "Phase 9 isolated note", "body": "Workspace B only",
            "trust_level": "operational",
        }, headers={"X-CSRF-Token": csrf})
        assert node.status_code == 201, node.text
        node_id = node.json()["node_id"]
        assert client.get(f"/api/v2/admin/workspaces/{a_id}/nodes/{node_id}").status_code == 404
        assert client.get(f"/api/v2/admin/workspaces/{b_id}/nodes/{node_id}").status_code == 200
