"""Shared, strict validation for user-supplied external URLs."""

from collections.abc import Collection
from urllib.parse import urlsplit


def validate_external_url(
    value: object,
    *,
    field_name: str = "URL",
    required: bool = False,
    allowed_hosts: Collection[str] | None = None,
) -> str | None:
    """Return a trimmed HTTP(S) URL or raise a Pydantic-friendly error."""
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required.")
        return None

    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a valid HTTP(S) URL.")

    cleaned = value.strip()
    if not cleaned:
        if required:
            raise ValueError(f"{field_name} is required.")
        return None

    if any(character.isspace() or ord(character) < 32 for character in cleaned):
        raise ValueError(f"{field_name} must be a valid HTTP(S) URL.")

    try:
        parsed = urlsplit(cleaned)
        hostname = parsed.hostname
        # Accessing port also rejects malformed values such as ``:not-a-port``.
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid HTTP(S) URL.") from exc

    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{field_name} must be a valid HTTP(S) URL.")

    if allowed_hosts:
        normalized_host = hostname.rstrip(".").lower()
        normalized_allowed = {host.rstrip(".").lower() for host in allowed_hosts}
        if normalized_host not in normalized_allowed:
            raise ValueError(f"{field_name} must be a Google Drive URL.")

    return cleaned
