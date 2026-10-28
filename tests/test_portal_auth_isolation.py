"""Role isolation tests for the current monolithic portal API.

The project previously collected three ``apps.*`` ASGI applications that no
longer exist.  Portal isolation now lives in route dependencies on the shared
FastAPI application, so these tests exercise the real mounted routes.
"""

from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.models.user import AccountStatus, User, UserRole


def _headers(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id), role=user.role.value)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def portal_users(db: Session):
    suffix = uuid4().hex[:8]
    users = {
        "fellow": User(
            first_name="Alice",
            last_name="Fellow",
            email=f"fellow_{suffix}@degreelabs.com",
            password_hash=hash_password("Pass12345!"),
            role=UserRole.FELLOW,
            account_status=AccountStatus.ACTIVE,
            is_active=True,
        ),
        "mentor": User(
            first_name="Bob",
            last_name="Mentor",
            email=f"mentor_{suffix}@degreelabs.com",
            password_hash=hash_password("Pass12345!"),
            role=UserRole.MENTOR,
            account_status=AccountStatus.ACTIVE,
            is_active=True,
        ),
        "admin": User(
            first_name="Carol",
            last_name="Admin",
            email=f"admin_{suffix}@degreelabs.com",
            password_hash=hash_password("Pass12345!"),
            role=UserRole.ADMIN,
            account_status=AccountStatus.ACTIVE,
            is_active=True,
        ),
    }
    db.add_all(users.values())
    db.commit()
    yield users
    db.execute(delete(User).where(User.id.in_([user.id for user in users.values()])))
    db.commit()


def test_fellow_portal_rejects_mentor_and_admin(client, portal_users):
    route = "/api/v1/fellow/context"

    # The Fellow passes role authorization.  With no enrollment fixture, the
    # route correctly reaches business validation and returns 404, not 403.
    assert client.get(route, headers=_headers(portal_users["fellow"])).status_code == 404
    assert client.get(route, headers=_headers(portal_users["mentor"])).status_code == 403
    assert client.get(route, headers=_headers(portal_users["admin"])).status_code == 403


def test_mentor_portal_allows_mentor_and_rejects_other_roles(client, portal_users):
    route = "/api/v1/mentor/me"

    response = client.get(route, headers=_headers(portal_users["mentor"]))
    assert response.status_code == 200
    assert response.json()["email"] == portal_users["mentor"].email
    assert client.get(route, headers=_headers(portal_users["fellow"])).status_code == 403
    assert client.get(route, headers=_headers(portal_users["admin"])).status_code == 403


def test_admin_portal_allows_admin_and_rejects_other_roles(client, portal_users):
    route = "/api/v1/admin/stats"

    assert client.get(route, headers=_headers(portal_users["admin"])).status_code == 200
    assert client.get(route, headers=_headers(portal_users["fellow"])).status_code == 403
    assert client.get(route, headers=_headers(portal_users["mentor"])).status_code == 403
