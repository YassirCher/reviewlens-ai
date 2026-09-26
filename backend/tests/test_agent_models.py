from datetime import datetime, timezone
import uuid
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CheckConstraint, CreateColumn

from unittest.mock import patch

from app.admin.agent_models import (
    RECOMMENDED_MODELS,
    get_agent_models_state,
    save_agent_models_assignment,
)
from app.analysis.configuration import seed_analysis_configuration
from app.config import Settings
from app.db.base import Base
from app.db.models import ActiveConfiguration, AgentVersion, ModelPolicyVersion, WorkflowVersion
from app.runtime.contracts import WorkflowDag


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(CreateColumn, "sqlite")
def compile_create_column(element, compiler, **kw):
    text = compiler.visit_create_column(element, **kw)
    return text.replace("::jsonb", "")


@compiles(CheckConstraint, "sqlite")
def compile_check_constraint(element, compiler, **kw):
    if "~" in str(element.sqltext):
        return "CHECK (1 = 1)"
    return compiler.visit_check_constraint(element, **kw)


def _mock_validation(db, policy, **kw):
    return {"status": "valid", "eligible_routes": {slug: ["mock_provider"] for slug in policy.models}}


def _setup_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return session_factory()


def _test_config():
    return Settings(
        app_env="test",
        v2_agent_chat_models="deepseek/deepseek-v4-flash",
        session_secret="s" * 32,
        rate_limit_hash_secret="r" * 32,
        public_token_hash_secret="p" * 32,
    )


@patch("app.llmops.policies.validate_model_policy", lambda *a, **k: {"status": "valid"})
@patch("app.analysis.configuration.validate_model_policy", lambda *a, **k: {"status": "valid"})
def test_agent_models_initial_state():
    db = _setup_db()
    config = _test_config()
    seed_analysis_configuration(db, config=config)

    state = get_agent_models_state(db, config=config)
    assert state["default_model"] == "deepseek/deepseek-v4-flash"
    assert len(state["agents"]) == 8

    for agent in state["agents"]:
        assert agent["current_model"] == "deepseek/deepseek-v4-flash"
        assert agent["is_default"] is True

    # Available models include recommended models
    slugs = [m["slug"] for m in state["available_models"]]
    assert "deepseek/deepseek-v4-flash" in slugs
    assert "deepseek/deepseek-v4-flash-0731" in slugs
    assert "anthropic/claude-3.5-sonnet" in slugs


@patch("app.llmops.policies.validate_model_policy", lambda *a, **k: {"status": "valid"})
@patch("app.analysis.configuration.validate_model_policy", lambda *a, **k: {"status": "valid"})
def test_switch_agent_to_custom_model_and_revert():
    db = _setup_db()
    config = _test_config()
    seed_analysis_configuration(db, config=config)

    # Switch consensus_analyst to deepseek/deepseek-v4-flash-0731
    updated_state = save_agent_models_assignment(
        db,
        {"consensus_analyst": "deepseek/deepseek-v4-flash-0731"},
        config=config,
    )

    # Check updated state
    agents_map = {a["key"]: a for a in updated_state["agents"]}
    assert agents_map["consensus_analyst"]["current_model"] == "deepseek/deepseek-v4-flash-0731"
    assert agents_map["consensus_analyst"]["is_default"] is False

    # Check others remain on default
    assert agents_map["review_analyst"]["current_model"] == "deepseek/deepseek-v4-flash"
    assert agents_map["review_analyst"]["is_default"] is True

    # Verify the active workflow DAG points to the custom agent version for consensus_analyst
    active = db.get(ActiveConfiguration, 1)
    workflow = db.get(WorkflowVersion, active.workflow_version_id)
    dag = WorkflowDag.model_validate(workflow.dag)

    consensus_template = next(t for t in dag.templates if t.template_key == "build_consensus")
    agent_ver = db.get(AgentVersion, consensus_template.agent_version_id)
    policy_ver = db.get(ModelPolicyVersion, agent_ver.model_policy_version_id)
    assert policy_ver.policy["models"] == ["deepseek/deepseek-v4-flash-0731"]

    # Now revert consensus_analyst back to default
    reverted_state = save_agent_models_assignment(
        db,
        {"consensus_analyst": "deepseek/deepseek-v4-flash"},
        config=config,
    )
    agents_map_rev = {a["key"]: a for a in reverted_state["agents"]}
    assert agents_map_rev["consensus_analyst"]["current_model"] == "deepseek/deepseek-v4-flash"
    assert agents_map_rev["consensus_analyst"]["is_default"] is True


@patch("app.llmops.policies.validate_model_policy", lambda *a, **k: {"status": "valid"})
@patch("app.analysis.configuration.validate_model_policy", lambda *a, **k: {"status": "valid"})
def test_switch_agent_to_deepseek_v4_1_flash():
    """Explicitly verify switching from deepseek/deepseek-v4-flash to deepseek/deepseek-v4.1-flash."""
    db = _setup_db()
    config = _test_config()
    seed_analysis_configuration(db, config=config)

    # Switch review_analyst to deepseek/deepseek-v4.1-flash
    updated_state = save_agent_models_assignment(
        db,
        {"review_analyst": "deepseek/deepseek-v4.1-flash"},
        config=config,
    )

    agents_map = {a["key"]: a for a in updated_state["agents"]}
    assert agents_map["review_analyst"]["current_model"] == "deepseek/deepseek-v4.1-flash"
    assert agents_map["review_analyst"]["is_default"] is False

    # Check that the DAG template for analyze_reviews uses the new model policy
    active = db.get(ActiveConfiguration, 1)
    workflow = db.get(WorkflowVersion, active.workflow_version_id)
    dag = WorkflowDag.model_validate(workflow.dag)
    review_template = next(t for t in dag.templates if t.template_key == "analyze_review")
    agent_ver = db.get(AgentVersion, review_template.agent_version_id)
    policy_ver = db.get(ModelPolicyVersion, agent_ver.model_policy_version_id)
    assert policy_ver.policy["models"] == ["deepseek/deepseek-v4.1-flash"]
    assert "review_analyst" in active.feature_flags.get("agent_models", {})
    assert active.feature_flags["agent_models"]["review_analyst"] == "deepseek/deepseek-v4.1-flash"


@patch("app.llmops.policies.validate_model_policy", lambda *a, **k: {"status": "valid"})
@patch("app.analysis.configuration.validate_model_policy", lambda *a, **k: {"status": "valid"})
def test_agent_models_api_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.v2.dependencies import get_v2_db, require_admin
    from app.admin.common import require_admin_mutation
    from app.services.admin_auth import AuthenticatedAdmin
    from app.db.models import AdminSession, AdminUser

    db = _setup_db()
    config = _test_config()
    seed_analysis_configuration(db, config=config)

    admin_user = AdminUser(id=uuid.uuid4(), identifier="admin@test.com", password_hash="hash")
    admin_session = AdminSession(
        id=uuid.uuid4(),
        token_hash="th",
        csrf_secret_hash="ch",
        admin_id=admin_user.id,
        expires_at=datetime.now(timezone.utc),
        absolute_expires_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        ip_hash="ip",
        user_agent_hash="ua",
    )
    fake_admin = AuthenticatedAdmin(
        admin=admin_user,
        session=admin_session,
    )

    app.dependency_overrides[get_v2_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: fake_admin
    app.dependency_overrides[require_admin_mutation] = lambda: fake_admin

    try:
        client = TestClient(app)
        get_res = client.get("/api/v2/admin/agent-models")
        assert get_res.status_code == 200, get_res.text
        data = get_res.json()
        assert data["default_model"] == "deepseek/deepseek-v4-flash"
        assert len(data["agents"]) == 8

        # Update review_analyst
        put_res = client.put(
            "/api/v2/admin/agent-models",
            json={"models": {"review_analyst": "deepseek/deepseek-v4-flash-0731"}},
        )
        assert put_res.status_code == 200, put_res.text
        updated = put_res.json()
        agents_dict = {a["key"]: a for a in updated["agents"]}
        assert agents_dict["review_analyst"]["current_model"] == "deepseek/deepseek-v4-flash-0731"
        assert agents_dict["review_analyst"]["is_default"] is False
    finally:
        app.dependency_overrides.clear()
