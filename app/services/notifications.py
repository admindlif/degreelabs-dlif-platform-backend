from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.notification import PortalNotification
from app.schemas.mentor import MentorNotificationResponse, MentorNotificationsResponse


def create_portal_notification(
    db: Session,
    *,
    recipient_user_id: UUID,
    notification_type: str,
    title: str,
    message: str,
    target_url: str | None = None,
    metadata: dict | None = None,
) -> PortalNotification:
    notification = PortalNotification(
        recipient_user_id=recipient_user_id,
        notification_type=notification_type,
        title=title,
        message=message,
        target_url=target_url,
        notification_metadata=metadata or {},
    )
    db.add(notification)
    return notification


def _serialize(notification: PortalNotification) -> MentorNotificationResponse:
    return MentorNotificationResponse(
        id=notification.id,
        type=notification.notification_type,
        title=notification.title,
        message=notification.message,
        is_read=notification.read_at is not None,
        created_at=notification.created_at,
        target_url=notification.target_url,
        meta=notification.notification_metadata or {},
    )


def list_portal_notifications(
    db: Session,
    recipient_user_id: UUID,
    *,
    limit: int = 50,
) -> MentorNotificationsResponse:
    notifications = list(
        db.scalars(
            select(PortalNotification)
            .where(PortalNotification.recipient_user_id == recipient_user_id)
            .order_by(PortalNotification.created_at.desc())
            .limit(limit)
        ).all()
    )
    total_count = db.scalar(
        select(func.count(PortalNotification.id)).where(
            PortalNotification.recipient_user_id == recipient_user_id
        )
    ) or 0
    unread_count = db.scalar(
        select(func.count(PortalNotification.id)).where(
            PortalNotification.recipient_user_id == recipient_user_id,
            PortalNotification.read_at.is_(None),
        )
    ) or 0
    return MentorNotificationsResponse(
        total_count=total_count,
        unread_count=unread_count,
        notifications=[_serialize(notification) for notification in notifications],
    )


def get_unread_notification_count(db: Session, recipient_user_id: UUID) -> int:
    return db.scalar(
        select(func.count(PortalNotification.id)).where(
            PortalNotification.recipient_user_id == recipient_user_id,
            PortalNotification.read_at.is_(None),
        )
    ) or 0


def mark_portal_notification_read(
    db: Session,
    recipient_user_id: UUID,
    notification_id: UUID,
) -> None:
    notification = db.scalar(
        select(PortalNotification).where(
            PortalNotification.id == notification_id,
            PortalNotification.recipient_user_id == recipient_user_id,
        )
    )
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found.",
        )
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        db.commit()


def mark_all_portal_notifications_read(
    db: Session,
    recipient_user_id: UUID,
) -> None:
    db.execute(
        update(PortalNotification)
        .where(
            PortalNotification.recipient_user_id == recipient_user_id,
            PortalNotification.read_at.is_(None),
        )
        .values(read_at=datetime.now(timezone.utc))
    )
    db.commit()
