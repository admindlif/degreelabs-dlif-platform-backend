from starlette.requests import Request

from app.core.client_ip import get_client_ip
from app.core.config import settings


def _request(peer: str, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_spoofed_forwarded_for_from_untrusted_peer_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "10.0.0.0/8")
    request = _request("203.0.113.10", "198.51.100.25")

    assert get_client_ip(request) == "203.0.113.10"


def test_trusted_proxy_reports_effective_client(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "10.0.0.0/8")
    request = _request("10.0.0.5", "198.51.100.25")

    assert get_client_ip(request) == "198.51.100.25"


def test_trusted_multi_proxy_chain_skips_known_hops(monkeypatch):
    monkeypatch.setattr(
        settings,
        "trusted_proxy_cidrs",
        "10.0.0.0/8, 192.0.2.0/24",
    )
    request = _request(
        "10.0.0.5",
        "198.51.100.25, 203.0.113.9, 192.0.2.20",
    )

    assert get_client_ip(request) == "203.0.113.9"


def test_malformed_forwarded_chain_falls_back_to_peer(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "10.0.0.0/8")
    request = _request("10.0.0.5", "not-an-ip, 198.51.100.25")

    assert get_client_ip(request) == "10.0.0.5"


def test_forwarded_for_is_ignored_when_no_proxy_ranges_configured(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", None)
    request = _request("10.0.0.5", "198.51.100.25")

    assert get_client_ip(request) == "10.0.0.5"
