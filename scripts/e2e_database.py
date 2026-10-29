"""Strictly local database bootstrap shared by the Playwright E2E tools."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


BACKEND_ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def configure_e2e_environment() -> str:
    configured = os.environ.get("DLIF_E2E_DATABASE_URL")
    if not configured:
        values = dotenv_values(BACKEND_ROOT / ".env")
        configured = str(values.get("DATABASE_URL") or "")
    if not configured:
        raise RuntimeError("A local DATABASE_URL is required for E2E setup.")

    base_url = make_url(configured)
    if base_url.get_backend_name() != "postgresql":
        raise RuntimeError("E2E tests require isolated PostgreSQL.")
    if (base_url.host or "").lower() not in LOCAL_HOSTS:
        raise RuntimeError("REFUSING E2E DATABASE: PostgreSQL host is not loopback.")
    base_name = base_url.database or ""
    database_name = base_name if base_name.endswith("_e2e_test") else f"{base_name}_e2e_test"
    if not database_name.endswith("_e2e_test"):
        raise RuntimeError("REFUSING E2E DATABASE: name must end with '_e2e_test'.")

    e2e_url: URL = base_url.set(database=database_name)
    admin_url = base_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": database_name},
        ).scalar()
        if not exists:
            quoted = connection.dialect.identifier_preparer.quote(database_name)
            connection.exec_driver_sql(f"CREATE DATABASE {quoted}")
    admin_engine.dispose()

    rendered = e2e_url.render_as_string(hide_password=False)
    os.environ.update(
        {
            "DATABASE_URL": rendered,
            "ENVIRONMENT": "test",
            "SECRET_KEY": "dlif-e2e-only-secret-key-32-characters-minimum",
            "FRONTEND_BASE_URL": "http://127.0.0.1:3000",
            "MENTOR_FRONTEND_BASE_URL": "http://127.0.0.1:3003",
            "ADMIN_FRONTEND_BASE_URL": "http://127.0.0.1:3002",
            "CORS_ALLOWED_ORIGINS": (
                "http://127.0.0.1:3000,http://127.0.0.1:3002,"
                "http://127.0.0.1:3003"
            ),
            "EMAIL_BACKEND": "logging",
            "AUTH_COOKIE_SECURE": "false",
            "AUTH_COOKIE_DOMAIN": "",
            "PRIVILEGED_MFA_ENFORCEMENT_ENABLED": "true",
            "ALLOW_LEGACY_SESSIONLESS_ACCESS_TOKENS": "false",
            "TRUSTED_PROXY_CIDRS": "",
        }
    )
    return rendered