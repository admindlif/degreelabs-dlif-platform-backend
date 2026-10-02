from pathlib import Path

import requests

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.core.config import settings


SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/calendar.events",
]


def _get_credentials() -> Credentials:
    if not settings.google_oauth_token_file:
        raise RuntimeError(
            "GOOGLE_OAUTH_TOKEN_FILE is not configured."
        )

    token_path = Path(
        settings.google_oauth_token_file
    )

    if not token_path.exists():
        raise RuntimeError(
            "Google Meet authorization token not found. "
            "Run google_meet_authorize.py first."
        )

    credentials = Credentials.from_authorized_user_file(
        str(token_path),
        SCOPES,
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

        token_path.write_text(
            credentials.to_json(),
            encoding="utf-8",
        )

    if not credentials.valid:
        raise RuntimeError(
            "Google Meet OAuth credentials are invalid."
        )

    return credentials


def create_meeting_space() -> dict:
    credentials = _get_credentials()

    response = requests.post(
        f"{settings.google_meet_base_url}/spaces",
        headers={
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        },
        json={},
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"Google Meet API error "
            f"{response.status_code}: {response.text}"
        )

    data = response.json()

    meeting_url = data.get("meetingUri")

    if not meeting_url:
        raise RuntimeError(
            "Google Meet API did not return a meeting URL."
        )

    return {
        "space_name": data.get("name"),
        "meeting_url": meeting_url,
        "meeting_code": data.get("meetingCode"),
    }