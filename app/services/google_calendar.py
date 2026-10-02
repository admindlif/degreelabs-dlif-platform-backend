from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import sleep
from uuid import uuid4

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.config import settings


CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/calendar.events",
]


def _get_calendar_service():
    if not settings.google_oauth_token_file:
        raise RuntimeError(
            "GOOGLE_OAUTH_TOKEN_FILE is not configured."
        )

    token_path = Path(
        settings.google_oauth_token_file
    )

    if not token_path.exists():
        raise RuntimeError(
            "Google OAuth token not found. "
            "Run google_meet_authorize.py first."
        )

    credentials = Credentials.from_authorized_user_file(
        str(token_path),
        CALENDAR_SCOPES,
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

        token_path.write_text(
            credentials.to_json(),
            encoding="utf-8",
        )

    if not credentials.valid:
        raise RuntimeError(
            "Google OAuth credentials are invalid."
        )

    return build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

def _normalize_attendees(
    attendee_emails: list[str],
) -> list[dict[str, str]]:
    unique_emails = {
        email.strip().lower()
        for email in attendee_emails
        if email and email.strip()
    }

    return [
        {"email": email}
        for email in sorted(unique_emails)
    ]

def _extract_meet_details(
    event: dict,
) -> tuple[str | None, str | None]:
    conference_data = (
        event.get("conferenceData")
        or {}
    )

    for entry_point in conference_data.get(
        "entryPoints",
        [],
    ):
        if (
            entry_point.get("entryPointType")
            == "video"
        ):
            return (
                entry_point.get("uri"),
                entry_point.get("meetingCode"),
            )

    return None, None


def _wait_for_meet(
    service,
    event_id: str,
) -> dict:
    """
    Conference creation may briefly remain pending after
    events.insert(), so re-read the event a few times.
    """
    for _ in range(8):
        event = (
            service.events()
            .get(
                calendarId="primary",
                eventId=event_id,
            )
            .execute()
        )

        meeting_url, _ = _extract_meet_details(
            event
        )

        if meeting_url:
            return event

        sleep(0.5)

    raise RuntimeError(
        "Google Calendar event was created, but "
        "Google Meet conference creation did not complete."
    )


def create_calendar_event_with_meet(
    *,
    title: str,
    description: str | None,
    start_at: datetime,
    end_at: datetime,
    attendee_emails: list[str],
) -> dict:
    service = _get_calendar_service()

    event_body = {
        "summary": title,
        "description": description or "",
        "start": {
            "dateTime": start_at.isoformat(),
            "timeZone": (
                settings.google_calendar_timezone
            ),
        },
        "end": {
            "dateTime": end_at.isoformat(),
            "timeZone": (
                settings.google_calendar_timezone
            ),
        },
        "attendees": _normalize_attendees(
            attendee_emails
        ),
        "conferenceData": {
            "createRequest": {
                "requestId": uuid4().hex,
                "conferenceSolutionKey": {
                    "type": "hangoutsMeet",
                },
            }
        },
    }

    event = (
        service.events()
        .insert(
            calendarId="primary",
            body=event_body,
            conferenceDataVersion=1,
            sendUpdates="all",
        )
        .execute()
    )

    event_id = event.get("id")

    if not event_id:
        raise RuntimeError(
            "Google Calendar did not return an event ID."
        )

    meeting_url, meeting_code = (
        _extract_meet_details(event)
    )

    # Google notes conference creation can initially
    # return a pending state.
    if not meeting_url:
        event = _wait_for_meet(
            service,
            event_id,
        )

        meeting_url, meeting_code = (
            _extract_meet_details(event)
        )

    if not meeting_url:
        raise RuntimeError(
            "Google Calendar event created but "
            "Meet link was not returned."
        )

    return {
        "event_id": event_id,
        "calendar_url": event.get("htmlLink"),
        "meeting_url": meeting_url,
        "meeting_code": meeting_code,
    }


def update_calendar_event(
    *,
    event_id: str,
    title: str,
    description: str | None,
    start_at: datetime,
    end_at: datetime,
    attendee_emails: list[str],
) -> dict:
    service = _get_calendar_service()

    event = (
        service.events()
        .get(
            calendarId="primary",
            eventId=event_id,
        )
        .execute()
    )

    event["summary"] = title
    event["description"] = (
        description or ""
    )

    event["start"] = {
        "dateTime": start_at.isoformat(),
        "timeZone": (
            settings.google_calendar_timezone
        ),
    }

    event["end"] = {
        "dateTime": end_at.isoformat(),
        "timeZone": (
            settings.google_calendar_timezone
        ),
    }

    event["attendees"] = (
        _normalize_attendees(
            attendee_emails
        )
    )

    updated_event = (
        service.events()
        .update(
            calendarId="primary",
            eventId=event_id,
            body=event,
            conferenceDataVersion=1,
            sendUpdates="all",
        )
        .execute()
    )

    meeting_url, meeting_code = (
        _extract_meet_details(updated_event)
    )

    return {
        "event_id": updated_event.get("id"),
        "calendar_url": (
            updated_event.get("htmlLink")
        ),
        "meeting_url": meeting_url,
        "meeting_code": meeting_code,
    }


def delete_calendar_event(
    event_id: str,
) -> None:
    service = _get_calendar_service()

    service.events().delete(
        calendarId="primary",
        eventId=event_id,
        sendUpdates="all",
    ).execute()