from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import Response

from app.api.v2 import analyses as routes
from app.config import Settings
from app.platform import health


def test_public_failure_categories_are_allowlisted() -> None:
    run = SimpleNamespace(id=uuid.uuid4())
    transcript = SimpleNamespace(id=uuid.uuid4(), workflow_task_key="fetch_transcript.source_1")
    db = MagicMock()
    db.scalars.return_value = [
        SimpleNamespace(task_run_id=uuid.uuid4(), status="failed", error_code="youtube_no_candidates", error_category="upstream", output_payload=None)
    ]
    assert routes._public_failure(db, run, [transcript])["code"] == "no_relevant_videos"
    db.scalar.return_value = None
    db.scalars.return_value = [
        SimpleNamespace(task_run_id=transcript.id, status="succeeded", error_code=None, error_category=None, output_payload={"available": False})
    ]
    assert routes._public_failure(db, run, [transcript])["code"] == "no_transcripts"
    db.scalars.return_value = [
        SimpleNamespace(task_run_id=uuid.uuid4(), status="failed", error_code="secret upstream detail", error_category="budget", output_payload=None)
    ]
    failure = routes._public_failure(db, run, [])
    assert failure["code"] == "analysis_capacity_reached"
    assert "secret" not in str(failure)
    db.scalars.return_value = []
    assert routes._public_failure(db, run, [transcript])["code"] == "analysis_failed"


def test_public_failure_recovers_legacy_all_excluded_discovery() -> None:
    run = SimpleNamespace(id=uuid.uuid4())
    discovery = SimpleNamespace(id=uuid.uuid4(), workflow_task_key="discover_candidates")
    db = MagicMock()
    db.scalars.return_value = [SimpleNamespace(
        task_run_id=discovery.id,
        status="succeeded",
        error_code=None,
        error_category=None,
        output_payload={"candidates": [
            {"video_id": "fixture01", "deterministic_exclusion": "irrelevant_product"},
            {"video_id": "fixture02", "deterministic_exclusion": "irrelevant_product"},
        ]},
    )]
    assert routes._public_failure(db, run, [discovery]) == {
        "code": "no_relevant_videos",
        "message": "No relevant review videos were found. Try a more specific product model.",
    }


def test_graph_page_retains_connections_to_nodes_on_other_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "settings", Settings(
        _env_file=None, session_secret="s" * 40,
        public_token_hash_secret="p" * 40, rate_limit_hash_secret="r" * 40,
    ))
    publication = SimpleNamespace(
        report_id=uuid.uuid4(),
        graph_payload={
            "nodes": [
                {"id": "source-public", "type": "source", "label": "Review"},
                {"id": "finding-public", "type": "finding", "label": "A finding"},
            ],
            "edges": [{"source": "source-public", "target": "finding-public", "type": "SUPPORTS"}],
        },
    )
    monkeypatch.setattr(routes, "_publication", lambda *_: publication)
    response = routes.read_report_graph("A" * 43, Response(), type_filter=None, cursor=None, limit=1, db=MagicMock())
    assert [node.id for node in response.nodes] == ["source-public"]
    assert [(edge.source, edge.target) for edge in response.edges] == [("source-public", "finding-public")]
    assert response.next_cursor is not None


def test_phase5_downgrade_rejects_successor_tool_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = importlib.import_module("migrations.versions.20260916_0005_phase5_typed_tools")
    cursor = SimpleNamespace(scalar=lambda: True)
    monkeypatch.setattr(migration.op, "get_bind", lambda: SimpleNamespace(execute=lambda *_: cursor))
    with pytest.raises(RuntimeError, match="successor tool versions"):
        migration.downgrade()


def test_readiness_returns_promptly_when_postgres_is_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable() -> None:
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(health, "_postgres_probe", unavailable)
    monkeypatch.setattr(health, "_redis_probe", lambda: None)
    monkeypatch.setattr(health, "_storage_probe", lambda *_: None)
    monkeypatch.setattr(health, "_neo4j_probe", lambda *_: None)
    monkeypatch.setattr(health, "_qdrant_probe", lambda *_: None)
    result = health.collect_health(Settings(
        _env_file=None, session_secret="s" * 40,
        public_token_hash_secret="p" * 40, rate_limit_hash_secret="r" * 40,
    ))
    assert result.status == "not_ready"
    assert result.dependencies["postgres"].status == "unavailable"
    assert result.llmops == result.knowledge == result.research == {}
