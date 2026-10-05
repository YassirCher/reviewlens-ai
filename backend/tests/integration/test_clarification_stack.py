"""Database, owner, dispatch and recovery acceptance; no provider inference."""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import settings
from app.db.models import ActiveConfiguration, AnalysisRun, ProductClarification, RunBudgetState, TaskAttempt, TaskRun, WorkflowDefinition, WorkflowVersion
from app.db.session import session_scope
from app.main import app
from app.public.admission import resolve_session
from app.runtime.clarification import pause_for_product
from app.runtime.contracts import WorkflowDag, WorkflowTaskSpec, canonical_json_hash
from app.runtime.fixtures import install_fixture_configuration
from app.runtime.service import create_run, execute_task_run, repair_unfinished_runs, utc_now
from app.runtime.outbox import read_progress, relay_runtime_outbox

pytestmark = pytest.mark.skipif(os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1" or settings.app_env != "test",
                              reason="requires disposable Compose test project")
ORIGIN = {"Origin": "http://localhost:3000"}


def _waiting_run():
    with session_scope() as db:
        active = db.get(ActiveConfiguration, 1)
        prior = (active.workflow_version_id, active.budget_policy_version_id, dict(active.feature_flags))
        install_fixture_configuration(db, "success")
        dag = WorkflowDag(run_timeout_seconds=900, tasks=(
            WorkflowTaskSpec(task_key="resolve_discovered_product", handler="analysis.resolve_discovered_product"),
            WorkflowTaskSpec(task_key="after_answer", handler="fixture.echo", dependencies=("resolve_discovered_product",), input={"value": "resumed"}),
        ))
        definition = WorkflowDefinition(id=uuid.uuid4(), key="clarification-test-" + uuid.uuid4().hex,
            name="Clarification recovery fixture", description="No research or paid calls")
        db.add(definition)
        db.flush()
        payload = dag.model_dump(mode="json")
        version = WorkflowVersion(id=uuid.uuid4(), definition_id=definition.id, version_number=1,
            lifecycle="published", content_hash=canonical_json_hash(payload), change_note="Recovery acceptance",
            published_at=utc_now(), dag=payload)
        db.add(version)
        db.flush()
        active.workflow_version_id = version.id
        session, cookie = resolve_session(db, None, create=True)
        run = create_run(db, product_name="Sony WH-1000XM5", initiator_type="public", initiator_id=session.id,
                         requested_options={"source_count": 3, "analyze_comments": False})
        task = db.scalar(select(TaskRun).where(TaskRun.run_id == run.id, TaskRun.workflow_task_key == "resolve_discovered_product"))
        run.status, task.status = "running", "running"
        pause_for_product(db, run, task, [
            {"id": "wh1000xm4", "product_name": "Sony WH-1000XM4", "sources": [{"title": "WH-1000XM4 review", "url": "https://www.youtube.com/watch?v=abcdefghijk"}]},
            {"id": "wf1000xm5", "product_name": "Sony WF-1000XM5", "sources": [{"title": "WF-1000XM5 review", "url": "https://www.youtube.com/watch?v=lmnopqrstuv"}]},
        ])
        db.flush()
        question = db.scalar(select(ProductClarification).where(ProductClarification.run_id == run.id))
        result = run.id, task.id, question.id, cookie
        active.workflow_version_id, active.budget_policy_version_id, active.feature_flags = prior
        return result


def _owner(cookie):
    client = TestClient(app)
    client.cookies.set(settings.anonymous_session_cookie, cookie)
    return client


def test_waiting_is_durable_owned_and_cannot_dispatch(monkeypatch):
    run_id, task_id, question_id, cookie = _waiting_run()
    sent = []
    from app.worker import celery_app
    monkeypatch.setattr(celery_app, "send_task", lambda *args, **kwargs: sent.append((args, kwargs)))
    relay_runtime_outbox(run_id=run_id)
    assert sent == []
    assert execute_task_run(task_id, expected_attempt_number=1)["status"] == "waiting_for_input"
    repair_unfinished_runs()
    with session_scope() as db:
        assert db.scalar(select(func.count(TaskAttempt.id)).join(TaskRun).where(TaskRun.run_id == run_id)) == 0
    with _owner(cookie) as owner, TestClient(app) as outsider:
        path = f"/api/v2/analyses/{run_id}"
        first = owner.get(path).json()
        assert first["status"] == "waiting_for_input" and first["clarification"]["id"] == str(question_id)
        assert owner.get(path).json()["clarification"] == first["clarification"]
        answer = path + f"/clarifications/{question_id}/answer"
        assert outsider.get(path).status_code == 404
        assert outsider.post(answer, json={"choice_id": "wh1000xm4"}, headers=ORIGIN).status_code == 404
        assert owner.post(answer, json={"choice_id": "wh1000xm4"}, headers={"Origin": "https://unrelated.example"}).status_code == 403
        assert owner.post(answer, json={"choice_id": "invented"}, headers=ORIGIN).status_code == 422
        assert owner.post(answer, json={"choice_id": "wh1000xm4"}, headers=ORIGIN).status_code == 200
        assert owner.post(answer, json={"choice_id": "wh1000xm4"}, headers=ORIGIN).status_code == 200
        assert owner.post(answer, json={"choice_id": "wf1000xm5"}, headers=ORIGIN).status_code == 409
    with session_scope() as db:
        run = db.get(AnalysisRun, run_id)
        assert run.product_input == "Sony WH-1000XM5" and run.resolved_product_name == "Sony WH-1000XM4"
        events = read_progress(run_id)["events"]
        assert {item["event_type"] for item in events} >= {"run.waiting_for_input", "run.input_received"}
    with _owner(cookie) as owner:
        assert owner.post(f"/api/v2/analyses/{run_id}/cancel", headers=ORIGIN).status_code == 202


def test_concurrent_answers_have_one_winner():
    run_id, _, question_id, cookie = _waiting_run()
    def answer(choice):
        with _owner(cookie) as owner:
            return owner.post(f"/api/v2/analyses/{run_id}/clarifications/{question_id}/answer", json={"choice_id": choice}, headers=ORIGIN).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(answer, ["wh1000xm4", "wf1000xm5"]))
    assert sorted(results) == [200, 409]
    with _owner(cookie) as owner:
        owner.post(f"/api/v2/analyses/{run_id}/cancel", headers=ORIGIN)


@pytest.mark.parametrize("action", ["cancel", "expire"])
def test_cancel_and_expiry_preserve_history_and_close_reservations(action):
    run_id, _, question_id, cookie = _waiting_run()
    if action == "expire":
        with session_scope() as db:
            row = db.get(ProductClarification, question_id)
            row.expires_at = utc_now() - timedelta(seconds=1)
        repair_unfinished_runs()
    with _owner(cookie) as owner:
        if action == "cancel":
            response = owner.post(f"/api/v2/analyses/{run_id}/cancel", headers=ORIGIN)
            assert response.status_code == 202
        state = owner.get(f"/api/v2/analyses/{run_id}").json()
        assert state["status"] == ("cancelled" if action == "cancel" else "failed")
        assert state["clarification"] is None
        response = owner.post(f"/api/v2/analyses/{run_id}/clarifications/{question_id}/answer", json={"choice_id": "wh1000xm4"}, headers=ORIGIN)
        assert response.status_code == 409
    with session_scope() as db:
        assert db.get(ProductClarification, question_id).status == ("cancelled" if action == "cancel" else "expired")
        assert db.get(RunBudgetState, run_id).status == "closed"
        assert db.get(AnalysisRun, run_id).product_input == "Sony WH-1000XM5"
