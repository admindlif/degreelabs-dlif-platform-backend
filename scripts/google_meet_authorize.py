from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from app.core.config import settings


SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/calendar.events",
]


def main():
    if not settings.google_oauth_client_file:
        raise RuntimeError(
            "GOOGLE_OAUTH_CLIENT_FILE is not configured."
        )

    if not settings.google_oauth_token_file:
        raise RuntimeError(
            "GOOGLE_OAUTH_TOKEN_FILE is not configured."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        settings.google_oauth_client_file,
        scopes=SCOPES,
    )

    credentials = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    token_path = Path(
        settings.google_oauth_token_file
    )

    token_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    token_path.write_text(
        credentials.to_json(),
        encoding="utf-8",
    )

    print("Google Meet authorization successful.")
    print(f"Token saved to: {token_path}")


if __name__ == "__main__":
    main()