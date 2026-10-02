"""
Model for hashed TOTP recovery codes.

Recovery codes are generated in plain text once, shown to the user,
and immediately hashed with SHA-256 before storage.

The plain-text values are never persisted.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User

class UserRecoveryCode(Base):
    __tablename__ = "user_recovery_codes"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # SHA-256 hex-digest of the plain-text recovery code.
    code_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    # True once the student has consumed this code to regain access.
    used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(  # noqa: F821
        "User",
        back_populates="recovery_codes",
    )
