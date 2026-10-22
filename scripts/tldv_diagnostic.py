"""
Development Diagnostic Script: tl;dv API Connectivity & Meeting Discovery.

Usage:
    python scripts/tldv_diagnostic.py
    python scripts/tldv_diagnostic.py --dlif-only

Security notice:
    This script is for local development and operational verification only.
    It NEVER prints API keys, authorization headers, cookies, passwords,
    or signed recording URLs.
    It does NOT download video files.
"""

import argparse
import sys
from datetime import datetime
import httpx

from app.core.config import settings


def run_diagnostic(dlif_only: bool = False) -> None:
    print("=" * 70)
    print(" tl;dv API Connectivity & Meeting Discovery Diagnostic")
    print("=" * 70)

    # 1. Configuration check (No secret exposure)
    if not settings.tldv_api_key or not settings.tldv_api_key.strip():
        print("[FAIL] TLDV_API_KEY is not configured in environment or .env.")
        sys.exit(1)

    print(f"[OK] TLDV_API_KEY: Configured ({len(settings.tldv_api_key.strip())} chars)")
    print(f"[OK] TLDV_MOCK_MODE: {settings.tldv_mock_mode} (Mock disabled)")
    print(f"[OK] Base URL: {settings.tldv_api_base_url}")
    print()

    headers = {
        "x-api-key": settings.tldv_api_key.strip(),
        "Accept": "application/json",
        "User-Agent": "DLIF-Platform-Backend-Diagnostic/1.0",
    }

    base_url = settings.tldv_api_base_url.rstrip("/")
    meetings_endpoint = f"{base_url}/v1alpha1/meetings?pageSize=50"

    print(f"Connecting to {meetings_endpoint}...")

    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.get(meetings_endpoint, headers=headers)

            if resp.status_code == 401:
                print("[ERROR] 401 Unauthorized: The tl;dv API key was rejected.")
                sys.exit(1)
            elif resp.status_code == 403:
                print("[ERROR] 403 Forbidden: API key lacks required permissions/plan.")
                sys.exit(1)
            elif resp.status_code == 429:
                print("[ERROR] 429 Too Many Requests: Rate limit reached.")
                sys.exit(1)
            elif resp.status_code != 200:
                print(f"[ERROR] HTTP {resp.status_code} received from tl;dv API.")
                sys.exit(1)

            print("[SUCCESS] Authentication succeeded! (HTTP 200 OK)")

            payload = resp.json()
            raw_meetings = payload.get("results") or payload.get("meetings") or []
            total_meetings = payload.get("total", len(raw_meetings))
            print(f"Total meetings available in account: {total_meetings}")
            print(f"Meetings retrieved in current page: {len(raw_meetings)}")
            print("-" * 70)

            results = []
            for m in raw_meetings:
                m_id = m.get("id", "")
                title = (m.get("name") or "Untitled Meeting").strip()
                date_str = m.get("happenedAt", "")
                duration_sec = m.get("duration", 0)

                if dlif_only and "dlif" not in title.lower():
                    continue

                # Check recording availability without downloading files
                rec_available = False
                try:
                    rec_resp = client.get(
                        f"{base_url}/v1alpha1/meetings/{m_id}/download",
                        headers=headers,
                        follow_redirects=False,
                    )
                    if rec_resp.status_code in (200, 301, 302, 303, 307, 308):
                        rec_available = True
                except Exception:
                    rec_available = False

                # Check transcript availability
                tra_available = False
                try:
                    tra_resp = client.get(
                        f"{base_url}/v1alpha1/meetings/{m_id}/transcript",
                        headers=headers,
                    )
                    if tra_resp.status_code == 200:
                        tra_json = tra_resp.json()
                        segs = (
                            tra_json.get("data")
                            or tra_json.get("segments")
                            or tra_json.get("transcript")
                            or []
                        )
                        tra_available = len(segs) > 0
                except Exception:
                    tra_available = False

                results.append({
                    "id": m_id,
                    "title": title,
                    "date": date_str,
                    "duration": f"{int(duration_sec)}s" if duration_sec else "N/A",
                    "recording": "Ready" if rec_available else "Not Available",
                    "transcript": "Ready" if tra_available else "Not Available",
                })

            # Print formatted sanitized table
            print(
                f"{'Meeting ID':<26} | {'Date':<16} | {'Rec':<5} | {'Tra':<5} | {'Title'}"
            )
            print("-" * 100)
            for item in results:
                # Format short date
                short_date = item["date"]
                try:
                    # E.g. "Thu Oct 08 2026 13:15:00 GMT+0000..."
                    parts = short_date.split()
                    if len(parts) >= 4:
                        short_date = f"{parts[1]} {parts[2]}, {parts[3]}"
                except Exception:
                    pass

                print(
                    f"{item['id']:<26} | {short_date:<16} | {item['recording']:<5} | {item['transcript']:<5} | {item['title']}"
                )

            print("-" * 100)
            print(f"Displayed {len(results)} meeting(s).")
            print("Note: No video payloads were downloaded. No secrets were logged.")

    except httpx.RequestError as exc:
        print(f"[ERROR] Network connection error: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tl;dv API Diagnostic")
    parser.add_argument(
        "--dlif-only",
        action="store_true",
        help="Only display meetings with 'DLIF' in title",
    )
    args = parser.parse_args()
    run_diagnostic(dlif_only=args.dlif_only)
