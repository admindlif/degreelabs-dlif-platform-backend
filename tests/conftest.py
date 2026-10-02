"""
Shared test configuration and fixtures for backend test suite.
"""

from typing import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sqlalchemy import create_engine, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import create_access_token, hash_password

from app.main import app
from app.models.user import AccountStatus, User, UserRole
from app.models.user_invitation import UserInvitationToken
from app.models.user_recovery_code import UserRecoveryCode

from app.core.config import settings
import app.db.session as db_session_module
from app.db.session import get_db


# ---------------------------------------------------------------------------
# Dedicated pytest database
# ---------------------------------------------------------------------------

_dev_url = make_url(settings.database_url)

if not _dev_url.database:
    raise RuntimeError(
        "DATABASE_URL does not contain a database name."
    )

_test_database_name = (
    f"{_dev_url.database}_test"
)

_test_url = _dev_url.set(
    database=_test_database_name
)

TEST_DATABASE_URL = (
    _test_url.render_as_string(
        hide_password=False
    )
)

test_engine = create_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    expire_on_commit=False,
)

# IMPORTANT:
# Any API runtime using app.db.session.get_db
# will now obtain sessions from the test DB.
db_session_module.engine = test_engine
db_session_module.SessionLocal = (
    TestingSessionLocal
)


@pytest.fixture(scope="session", autouse=True)
def ensure_test_database():
    database_name = make_url(
        TEST_DATABASE_URL
    ).database or ""

    if not database_name.endswith("_test"):
        raise RuntimeError(
            "REFUSING TO RUN TESTS: "
            "pytest must use a database ending "
            "with '_test'. "
            f"Current database: {database_name}"
        )


@pytest.fixture(scope="session")
def db_session() -> Generator[Session, None, None]:
    """Provide a database session for the test session."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def db() -> Generator[Session, None, None]:
    """Provide a fresh test database session per test."""
    session = TestingSessionLocal()

    try:
        yield session
    finally:
        session.rollback()
        session.close()

@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """FastAPI TestClient with database dependency override."""
    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def admin_user(db: Session) -> Generator[User, None, None]:
    """Create a persistent active admin user for test cases."""
    email = f"admin_{uuid4().hex[:8]}@example.com"
    user = User(
        first_name="Admin",
        last_name="User",
        email=email,
        password_hash=hash_password("AdminSecret123!"),
        role=UserRole.ADMIN,
        account_status=AccountStatus.ACTIVE,
        two_factor_enabled=True,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    yield user

    # Teardown
    db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
    db.execute(delete(UserInvitationToken).where(UserInvitationToken.user_id == user.id))
    db.execute(delete(User).where(User.id == user.id))
    db.commit()


@pytest.fixture
def admin_token(admin_user: User) -> str:
    """Return a valid JWT access token for the test admin user."""
    return create_access_token(str(admin_user.id))


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    """Authorization header with Bearer token for the test admin."""
    return {"Authorization": f"Bearer {admin_token}"}
