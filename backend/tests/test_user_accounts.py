from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.db.models import AnalysisRun, AnonymousSession, ReportPublication, User, UserSession
from app.errors import V2Error
from app.public.admission import _signed_identifier
from app.security import keyed_hash, verify_password
from app.services.user_auth import UserAuthService
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateColumn


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(CreateColumn, "sqlite")
def compile_create_column(element, compiler, **kw):
    text = compiler.visit_create_column(element, **kw)
    return text.replace("::jsonb", "")


def _in_memory_db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (User, UserSession, AnonymousSession, AnalysisRun, ReportPublication):
        model.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    return session_factory()


def _test_config() -> Settings:
    return Settings(
        session_secret="s" * 32,
        rate_limit_hash_secret="r" * 32,
        public_token_hash_secret="p" * 32,
        user_session_idle_days=30,
        user_session_absolute_days=90,
    )


def test_user_registration_and_validation() -> None:
    db = _in_memory_db()
    config = _test_config()
    service = UserAuthService(db, config=config)

    # 1. Successful registration
    tokens = service.register(
        email="User.Test@Example.com",
        password="securePassword123!",
        name="Test User",
        client_ip="127.0.0.1",
        user_agent="pytest-client",
    )
    assert tokens.user.email == "user.test@example.com"
    assert tokens.user.name == "Test User"
    assert verify_password("securePassword123!", tokens.user.password_hash)
    assert tokens.session_token is not None
    assert tokens.session.user_id == tokens.user.id

    # 2. Duplicate registration fails
    try:
        service.register(
            email="user.test@example.com",
            password="anotherPassword123!",
        )
        assert False, "Expected email_already_registered"
    except V2Error as exc:
        assert exc.code == "email_already_registered"

    # 3. Short password fails
    try:
        service.register(
            email="newuser@example.com",
            password="123",
        )
        assert False, "Expected weak_password"
    except V2Error as exc:
        assert exc.code == "weak_password"

    # 4. Invalid email format fails
    try:
        service.register(
            email="notanemail",
            password="validPassword123!",
        )
        assert False, "Expected invalid_email"
    except V2Error as exc:
        assert exc.code == "invalid_email"


def test_user_login_and_logout() -> None:
    db = _in_memory_db()
    config = _test_config()
    service = UserAuthService(db, config=config)

    # Register
    service.register(
        email="auth.test@example.com",
        password="MySecretPassword123",
        name="Auth Test",
    )

    # Login successfully
    login_tokens = service.login(
        email="auth.test@example.com",
        password="MySecretPassword123",
        client_ip="192.168.1.1",
        user_agent="test-agent",
    )
    assert login_tokens.user.email == "auth.test@example.com"

    # Authenticate with valid token
    auth = service.authenticate(login_tokens.session_token)
    assert auth.user.id == login_tokens.user.id

    # Login with wrong password
    try:
        service.login(
            email="auth.test@example.com",
            password="WrongPassword123",
        )
        assert False, "Expected invalid_credentials"
    except V2Error as exc:
        assert exc.code == "invalid_credentials"

    # Logout
    service.logout(auth)

    # Authenticate revoked session
    try:
        service.authenticate(login_tokens.session_token)
        assert False, "Expected user_session_expired"
    except V2Error as exc:
        assert exc.code == "user_session_expired"


def test_user_session_expiry() -> None:
    db = _in_memory_db()
    config = _test_config()
    service = UserAuthService(db, config=config)

    tokens = service.register(
        email="expiry@example.com",
        password="password12345",
    )

    # Manually expire the session
    tokens.session.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    try:
        service.authenticate(tokens.session_token)
        assert False, "Expected expired session"
    except V2Error as exc:
        assert exc.code == "user_session_expired"


def test_anonymous_run_adoption() -> None:
    db = _in_memory_db()
    config = _test_config()
    service = UserAuthService(db, config=config)

    import secrets

    now = datetime.now(timezone.utc)
    raw_anon_id = secrets.token_urlsafe(32)
    digest = keyed_hash("anon:" + raw_anon_id, config.session_secret)
    anon_session = AnonymousSession(
        id=uuid.uuid4(),
        identifier_hash=digest,
        quota_counters={},
        last_seen_at=now,
        expires_at=now + timedelta(days=7),
        absolute_expires_at=now + timedelta(days=7),
    )
    db.add(anon_session)
    db.flush()

    # Create an analysis run by this anonymous session
    run = AnalysisRun(
        id=uuid.uuid4(),
        product_input="Sony WH-1000XM5",
        canonical_product="Sony WH-1000XM5",
        initiator_type="public",
        initiator_id=anon_session.id,
        configuration_snapshot_id=uuid.uuid4(),
        deadline_at=now + timedelta(hours=1),
        status="complete",
        requested_options={"source_count": 5},
    )
    db.add(run)
    db.commit()

    # User registers with signed cookie of the anonymous session
    signed_cookie = _signed_identifier(raw_anon_id, config)
    tokens = service.register(
        email="adopter@example.com",
        password="adopterPassword123",
        prior_anon_cookie=signed_cookie,
    )

    # Run should now be owned by the user
    db.refresh(run)
    assert run.user_id == tokens.user.id


def test_user_researches_response() -> None:
    from app.api.v2.user_researches import list_user_researches
    from app.services.user_auth import AuthenticatedUser

    db = _in_memory_db()
    config = _test_config()
    service = UserAuthService(db, config=config)

    tokens = service.register(
        email="researcher@example.com",
        password="researcherPass123",
        name="Researcher",
    )
    auth = AuthenticatedUser(session=tokens.session, user=tokens.user)

    now = datetime.now(timezone.utc)

    # Add a completed report publication
    report_id = uuid.uuid4()
    run_id = uuid.uuid4()
    pub = ReportPublication(
        id=uuid.uuid4(),
        report_id=report_id,
        run_id=run_id,
        token_hash="a" * 64,
        content_hash="b" * 64,
        published_at=now,
        graph_payload={},
        payload={
            "product_name": "MacBook Pro M3",
            "score": 85,
            "verdict": "Strong Buy",
            "summary": "Outstanding battery life and processing power.",
            "source_count_analyzed": 5,
        },
    )
    db.add(pub)

    run = AnalysisRun(
        id=run_id,
        product_input="MacBook Pro M3",
        canonical_product="MacBook Pro M3",
        initiator_type="public",
        initiator_id=uuid.uuid4(),
        user_id=tokens.user.id,
        configuration_snapshot_id=uuid.uuid4(),
        deadline_at=now + timedelta(hours=1),
        status="complete",
        started_at=now - timedelta(minutes=5),
        completed_at=now,
        requested_options={"source_count": 5},
        report_id=report_id,
    )
    db.add(run)
    db.commit()

    response = list_user_researches(db=db, authenticated=auth)
    assert response.total == 1
    item = response.researches[0]
    assert item.product_name == "MacBook Pro M3"
    assert item.status == "complete"
    assert item.overall_score == 85
    assert item.verdict == "Strong Buy"
    assert item.report_url is not None
    assert item.pdf_url is not None
    assert item.duration_seconds is not None
    assert item.duration_seconds > 0


def test_get_optional_user_with_cookie_in_request() -> None:
    from unittest.mock import MagicMock
    from app.api.v2.dependencies import get_optional_user

    db = _in_memory_db()
    config = _test_config()
    service = UserAuthService(db, config=config)

    tokens = service.register(email="optuser@example.com", password="password123")

    # Mock request with cookie matching settings.user_session_cookie
    request = MagicMock()
    request.cookies = {config.user_session_cookie: tokens.session_token}

    # When called directly with get_optional_user(request, db), session_token defaults to Cookie(...)
    opt_user = get_optional_user(request, db, config=config)
    assert opt_user is not None
    assert opt_user.user.id == tokens.user.id


def test_unpublished_research_list_counts_only_completed_review_outputs() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.api.v2.user_researches import list_user_researches

    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=uuid.uuid4(), product_input="Black Shark T11", status="failed",
        created_at=now, started_at=now, completed_at=now,
        requested_options={"source_count": 5},
    )
    db = MagicMock()
    db.scalars.side_effect = [[run], []]
    db.execute.return_value = [
        (run.id, uuid.uuid4(), {"skipped": True}),
        (run.id, uuid.uuid4(), {"analysis": {"source_id": "verified"}}),
    ]
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))
    result = list_user_researches(db=db, authenticated=auth)
    assert result.researches[0].source_count_analyzed == 1


def test_standalone_adopt_anonymous_runs_and_token() -> None:
    from app.api.v2.user_researches import adopt_researches
    from app.services.user_auth import AuthenticatedUser
    from unittest.mock import MagicMock
    import secrets

    db = _in_memory_db()
    config = _test_config()
    service = UserAuthService(db, config=config)

    tokens = service.register(email="adopt_tester@example.com", password="password123")
    auth = AuthenticatedUser(session=tokens.session, user=tokens.user)

    now = datetime.now(timezone.utc)
    raw_anon_id = secrets.token_urlsafe(32)
    digest = keyed_hash("anon:" + raw_anon_id, config.session_secret)
    anon_session = AnonymousSession(
        id=uuid.uuid4(),
        identifier_hash=digest,
        quota_counters={},
        last_seen_at=now,
        expires_at=now + timedelta(days=7),
        absolute_expires_at=now + timedelta(days=7),
    )
    db.add(anon_session)
    db.flush()

    run = AnalysisRun(
        id=uuid.uuid4(),
        product_input="Gadget X",
        canonical_product="Gadget X",
        initiator_type="public",
        initiator_id=anon_session.id,
        configuration_snapshot_id=uuid.uuid4(),
        deadline_at=now + timedelta(hours=1),
        status="complete",
        requested_options={"source_count": 5},
    )
    db.add(run)
    db.commit()

    # Adopt via cookie
    signed_cookie = _signed_identifier(raw_anon_id, config)
    request = MagicMock()
    request.cookies = {config.anonymous_session_cookie: signed_cookie}

    result = adopt_researches(request=request, payload=None, db=db, authenticated=auth, config=config)
    assert result["adopted_count"] == 1

    db.refresh(run)
    assert run.user_id == tokens.user.id


def test_user_session_concurrency_and_touch_threshold() -> None:
    db = _in_memory_db()
    config = _test_config()
    service = UserAuthService(db, config=config)

    tokens = service.register(email="concurrency_test@example.com", password="password123")
    initial_last_seen = tokens.session.last_seen_at

    # Rapid second authentication within 30 seconds should not update last_seen_at
    auth1 = service.authenticate(tokens.session_token)
    assert auth1.user.email == "concurrency_test@example.com"
    assert auth1.session.last_seen_at == initial_last_seen

    # Authentication with invalid token fails cleanly without corrupting the session
    try:
        service.authenticate("invalid_token")
        assert False, "Expected user_session_expired"
    except V2Error as exc:
        assert exc.code == "user_session_expired"

    # Subsequent valid authentication still succeeds cleanly
    auth2 = service.authenticate(tokens.session_token)
    assert auth2.user.email == "concurrency_test@example.com"

