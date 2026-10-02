"""
Tests for Phase B portal auth isolation.

Verifies that:
- Each portal runtime only accepts tokens with the allowed role(s).
- Cross-portal token access is strictly rejected with HTTP 403.
"""

import sys
from pathlib import Path
from uuid import uuid4

# Ensure repo root is on sys.path so apps.* is importable
repo_root = Path(__file__).resolve().parent.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.models.user import User, UserRole, AccountStatus
from apps.student_api.main import app as student_app
from apps.mentor_api.main import app as mentor_app
from apps.admin_api.main import app as admin_app


@pytest.fixture
def student_client():
    return TestClient(student_app)


@pytest.fixture
def mentor_client():
    return TestClient(mentor_app)


@pytest.fixture
def admin_client():
    return TestClient(admin_app)


@pytest.fixture
def student_user(db: Session) -> User:
    email = f"student_{uuid4().hex[:8]}@degreelabs.com"
    user = User(
        first_name="Alice",
        last_name="Student",
        email=email,
        password_hash=hash_password("Pass12345!"),
        role=UserRole.STUDENT,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    yield user
    db.delete(user)
    db.commit()


@pytest.fixture
def mentor_user(db: Session) -> User:
    email = f"mentor_{uuid4().hex[:8]}@degreelabs.com"
    user = User(
        first_name="Bob",
        last_name="Mentor",
        email=email,
        password_hash=hash_password("Pass12345!"),
        role=UserRole.MENTOR,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    yield user
    db.delete(user)
    db.commit()


def test_student_portal_allows_student_and_rejects_others(
    student_client: TestClient,
    student_user: User,
    mentor_user: User,
    admin_user: User,
):
    student_token = create_access_token(str(student_user.id), role=student_user.role)
    mentor_token = create_access_token(str(mentor_user.id), role=mentor_user.role)
    admin_token = create_access_token(str(admin_user.id), role=admin_user.role)

    # 1. Student accesses student portal /me -> 200
    res = student_client.get(
        "/api/v1/student-portal/me",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == student_user.email
    assert data["role"].lower() in {"fellow", "student"}

    # 2. Mentor tries student portal /me -> 403
    res_mentor = student_client.get(
        "/api/v1/student-portal/me",
        headers={"Authorization": f"Bearer {mentor_token}"},
    )
    assert res_mentor.status_code == 403
    assert "restricted to the" in res_mentor.json()["detail"]

    # 3. Admin tries student portal /me -> 403
    res_admin = student_client.get(
        "/api/v1/student-portal/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_admin.status_code == 403
    assert "restricted to the" in res_admin.json()["detail"]


def test_mentor_portal_allows_mentor_and_rejects_others(
    mentor_client: TestClient,
    student_user: User,
    mentor_user: User,
    admin_user: User,
):
    student_token = create_access_token(str(student_user.id), role=student_user.role)
    mentor_token = create_access_token(str(mentor_user.id), role=mentor_user.role)
    admin_token = create_access_token(str(admin_user.id), role=admin_user.role)

    # 1. Mentor accesses mentor portal /me -> 200
    res = mentor_client.get(
        "/api/v1/mentor-portal/me",
        headers={"Authorization": f"Bearer {mentor_token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == mentor_user.email
    assert data["role"].lower() == "mentor"

    # 2. Student tries mentor portal /me -> 403
    res_student = mentor_client.get(
        "/api/v1/mentor-portal/me",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert res_student.status_code == 403
    assert "restricted to the Mentor Portal" in res_student.json()["detail"]

    # 3. Admin tries mentor portal /me -> 403
    res_admin = mentor_client.get(
        "/api/v1/mentor-portal/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_admin.status_code == 403
    assert "restricted to the Mentor Portal" in res_admin.json()["detail"]


def test_admin_portal_allows_admin_and_rejects_others(
    admin_client: TestClient,
    student_user: User,
    mentor_user: User,
    admin_user: User,
):
    student_token = create_access_token(str(student_user.id), role=student_user.role)
    mentor_token = create_access_token(str(mentor_user.id), role=mentor_user.role)
    admin_token = create_access_token(str(admin_user.id), role=admin_user.role)

    # 1. Admin accesses admin portal /me -> 200
    res = admin_client.get(
        "/api/v1/admin-portal/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == admin_user.email
    assert data["role"].lower() == "admin"

    # 2. Student tries admin portal /me -> 403
    res_student = admin_client.get(
        "/api/v1/admin-portal/me",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert res_student.status_code == 403
    assert "restricted to the Admin Portal" in res_student.json()["detail"]

    # 3. Mentor tries admin portal /me -> 403
    res_mentor = admin_client.get(
        "/api/v1/admin-portal/me",
        headers={"Authorization": f"Bearer {mentor_token}"},
    )
    assert res_mentor.status_code == 403
    assert "restricted to the Admin Portal" in res_mentor.json()["detail"]
