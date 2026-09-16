from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.analysis.configuration import seed_analysis_configuration
from app.config import settings
from app.db.models import AgentEvaluationResult, AgentVersion, ConfigurationSnapshot, TaskRun
from app.db.session import session_scope
from app.llmops.catalog import refresh_catalogs
from app.runtime.service import create_run

pytestmark = pytest.mark.skipif(
    os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1"
    or settings.app_env != "test"
    or settings.openrouter_base_url != "http://openrouter-mock:8089/api/v1",
    reason="requires the isolated Compose stack and its local OpenRouter mock",
)


def test_phase6_seed_snapshot_and_database_immutability() -> None:
    # The isolated Compose profile points at the local OpenRouter mock, never a live key.
    asyncio.run(refresh_catalogs())
    with session_scope() as db:
        first = seed_analysis_configuration(db)
    with session_scope() as db:
        second = seed_analysis_configuration(db)
        run = create_run(
            db,
            product_name="Phase 6 integration fixture",
            initiator_type="system_fixture",
            requested_options={"source_count": 5, "analyze_comments": False},
        )
        run_id = run.id
        snapshot_id = run.configuration_snapshot_id
    assert first == second
    assert len(first["agent_versions"]) == 7

    with session_scope() as db:
        snapshot = db.get(ConfigurationSnapshot, snapshot_id)
        tasks = list(db.scalars(select(TaskRun).where(TaskRun.run_id == run_id)))
        agent_id = uuid.UUID(next(iter(first["agent_versions"].values())))
        agent = db.get(AgentVersion, agent_id)
        evaluation = db.scalar(
            select(AgentEvaluationResult).where(
                AgentEvaluationResult.agent_version_id == agent_id,
                AgentEvaluationResult.status == "passed",
            )
        )
        evaluation_id = evaluation.id if evaluation else None
        original_prompt = agent.system_prompt if agent else None
    assert snapshot is not None
    assert len(snapshot.snapshot["agents"]) == 6
    assert len(tasks) == 20
    assert any(task.workflow_task_key.startswith("analyze_review.source_") for task in tasks)
    assert all(not task.workflow_task_key.startswith("fetch_comments.source_") for task in tasks)
    assert original_prompt and evaluation_id is not None

    with pytest.raises(DBAPIError), session_scope() as db:
        agent = db.get(AgentVersion, agent_id)
        assert agent is not None
        agent.system_prompt = "forbidden mutation"

    with pytest.raises(DBAPIError), session_scope() as db:
        evaluation = db.get(AgentEvaluationResult, evaluation_id)
        assert evaluation is not None
        evaluation.metrics = {"tampered": True}

    with pytest.raises(DBAPIError), session_scope() as db:
        snapshot = db.get(ConfigurationSnapshot, snapshot_id)
        assert snapshot is not None
        snapshot.snapshot = {"tampered": True}

    with session_scope() as db:
        snapshot = db.get(ConfigurationSnapshot, snapshot_id)
        agent = db.get(AgentVersion, agent_id)
        assert snapshot is not None and len(snapshot.snapshot["agents"]) == 6
        assert agent is not None and agent.system_prompt == original_prompt
