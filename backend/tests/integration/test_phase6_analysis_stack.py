from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.analysis.configuration import seed_analysis_configuration
from app.analysis import configuration as analysis_configuration
from app.analysis.registry import AGENT_SPECS, _retrieval
from app.config import settings
from app.db.models import ActiveConfiguration, AgentEvaluationResult, AgentVersion, ConfigurationSnapshot, TaskRun
from app.db.session import session_scope
from app.llmops.catalog import refresh_catalogs
from app.runtime.service import create_run
from app.knowledge.contracts import NodeType

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
        active = db.get(ActiveConfiguration, 1)
        active.workflow_version_id = uuid.UUID(second["workflow_version_id"])
        active.budget_policy_version_id = uuid.UUID(second["budget_policy_version_id"])
        run = create_run(
            db,
            product_name="Phase 6 integration fixture",
            initiator_type="system_fixture",
            requested_options={"source_count": 5, "analyze_comments": False},
        )
        run_id = run.id
        snapshot_id = run.configuration_snapshot_id
    assert first == second
    assert len(first["agent_versions"]) == 8

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
    assert len(snapshot.snapshot["agents"]) == 7
    assert len(tasks) == 25
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
        assert snapshot is not None and len(snapshot.snapshot["agents"]) == 7
        assert agent is not None and agent.system_prompt == original_prompt


def test_phase8_curator_policy_publishes_compatible_version_without_rewriting_old_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(refresh_catalogs())
    older = tuple(
        replace(spec, retrieval_policy=_retrieval((NodeType.PRODUCT, NodeType.SOURCE), tokens=5000))
        if spec.key == "source_curator" else spec
        for spec in AGENT_SPECS
    )
    monkeypatch.setattr(analysis_configuration, "AGENT_SPECS", older)
    with session_scope() as db:
        prior = seed_analysis_configuration(db)
        active = db.get(ActiveConfiguration, 1)
        active.workflow_version_id = uuid.UUID(prior["workflow_version_id"])
        active.budget_policy_version_id = uuid.UUID(prior["budget_policy_version_id"])
        run = create_run(
            db, product_name="Phase 8 snapshot fixture", initiator_type="system_fixture",
            requested_options={"source_count": 5, "analyze_comments": False},
        )
        snapshot_id = run.configuration_snapshot_id
    monkeypatch.setattr(analysis_configuration, "AGENT_SPECS", AGENT_SPECS)
    with session_scope() as db:
        current = seed_analysis_configuration(db)
        repeat = seed_analysis_configuration(db)
        frozen = db.get(ConfigurationSnapshot, snapshot_id)
        old_agent = db.get(AgentVersion, uuid.UUID(prior["agent_versions"]["source_curator"]))
        new_agent = db.get(AgentVersion, uuid.UUID(current["agent_versions"]["source_curator"]))
    assert current == repeat
    assert old_agent is not None and new_agent is not None
    assert old_agent.id != new_agent.id and old_agent.lifecycle == new_agent.lifecycle == "published"
    assert old_agent.retrieval_policy["input_token_budget"] == 5000
    assert new_agent.retrieval_policy["input_token_budget"] == 400
    assert prior["workflow_version_id"] != current["workflow_version_id"]
    assert frozen is not None
    assert any(item["id"] == str(old_agent.id) and item["content_hash"] == old_agent.content_hash for item in frozen.snapshot["agents"])
    assert all(item["id"] != str(new_agent.id) for item in frozen.snapshot["agents"])
