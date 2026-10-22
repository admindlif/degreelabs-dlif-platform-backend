#!/usr/bin/env python3
"""
transfer_session_to_r2.py
--------------------------
Secure, idempotent Session 2 video transfer: tl;dv -> FFmpeg faststart -> Cloudflare R2.

Usage:
    python scripts/transfer_session_to_r2.py \
        --session-id   2b27b883-d215-4530-8df9-340b74024b87 \
        --tldv-meeting-id 6ab67739571e2f00130d01b6 \
        [--min-duration-seconds 5000] \
        [--dry-run]

Outputs ONLY sanitised logs (no signed URLs, no API keys).
No database reads or writes are performed.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Logging — sanitise URLs before they reach any handler
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s\x27\x22]+")


def _sanitize(text: str) -> str:
    """Strip query strings from any URLs embedded in log text."""
    def _strip_qs(m: re.Match) -> str:
        return m.group(0).split("?")[0] + " [REDACTED_QS]"
    return _URL_RE.sub(_strip_qs, str(text))


class SanitizingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        formatted = super().format(record)
        return _sanitize(formatted)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        SanitizingFormatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger = logging.getLogger("transfer_s2")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


LOG = _setup_logging()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TLDV_BASE_URL = "https://pasta.tldv.io"
R2_OBJECT_PREFIX = "recordings"
_FFPROBE_ARGS = [
    "ffprobe", "-v", "error",
    "-show_entries", "format=duration",
    "-of", "json",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        LOG.error("Required environment variable not set: %s", name)
        sys.exit(2)
    return value


def _tldv_headers(api_key: str) -> dict:
    return {
        "x-api-key": api_key,
        "Accept": "application/json",
        "User-Agent": "DLIF-GHA-Transfer/1.0",
    }


def _get_signed_url(api_key: str, meeting_id: str) -> str:
    """
    Fetch the tl;dv signed download URL without following the redirect.
    Uses http.client directly so we can inspect the Location header before
    any redirect is followed (and before the URL reaches any log).
    """
    path = f"/v1alpha1/meetings/{meeting_id}/download"
    LOG.info("Requesting signed download URL for tl;dv meeting %s", meeting_id)

    try:
        conn = http.client.HTTPSConnection("pasta.tldv.io", timeout=15)
        conn.request("GET", path, headers=_tldv_headers(api_key))
        resp = conn.getresponse()
        status = resp.status
        location = resp.getheader("Location", "")
        body_bytes = resp.read()
        conn.close()

        if status in (301, 302, 303, 307, 308) and location:
            LOG.info("Got signed URL via redirect (HTTP %d)", status)
            return location

        if status == 200:
            body = json.loads(body_bytes.decode())
            signed = body.get("url") or body.get("downloadUrl") or ""
            if signed:
                LOG.info("Got signed URL from 200 JSON body")
                return signed

        LOG.error("Unexpected tl;dv API response: HTTP %d", status)
        sys.exit(1)

    except Exception as exc:  # noqa: BLE001
        LOG.error("Error contacting tl;dv API: %s", type(exc).__name__)
        sys.exit(1)


def _probe_duration(path: Path) -> float | None:
    """Run ffprobe on a local file and return duration in seconds."""
    try:
        result = subprocess.run(
            _FFPROBE_ARGS + ["-i", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception as exc:  # noqa: BLE001
        LOG.warning("ffprobe failed: %s", type(exc).__name__)
        return None


def _remux_faststart(src: Path, dst: Path) -> None:
    """Remux src -> dst with -movflags +faststart (copy, no re-encode)."""
    LOG.info("Remuxing to faststart: %s -> %s", src.name, dst.name)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-c", "copy",
        "-movflags", "+faststart",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        LOG.error("ffmpeg remux failed (rc=%d)", result.returncode)
        LOG.debug("ffmpeg stderr (sanitised): %s", _sanitize(result.stderr[-2000:]))
        sys.exit(1)
    LOG.info(
        "Remux complete: %s (%.1f MiB)",
        dst.name, dst.stat().st_size / 1024 / 1024,
    )


def _sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _r2_client():
    """Build a boto3 S3 client pointed at Cloudflare R2."""
    import boto3
    from botocore.client import Config

    account_id = _require_env("R2_ACCOUNT_ID")
    access_key_id = _require_env("R2_ACCESS_KEY_ID")
    secret_key = _require_env("R2_SECRET_ACCESS_KEY")
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"total_max_attempts": 10, "mode": "standard"},
            connect_timeout=30,
            read_timeout=300,
            tcp_keepalive=True,
        ),
    )


def _object_exists(client, bucket: str, key: str) -> bool:
    from botocore.exceptions import ClientError
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def _upload_file(client, local_path: Path, bucket: str, key: str) -> None:
    """Upload local_path to R2 using 16 MiB multipart chunks, up to 3 attempts."""
    from boto3.s3.transfer import TransferConfig
    from botocore.exceptions import BotoCoreError, ClientError

    config = TransferConfig(
        multipart_threshold=64 * 1024 * 1024,
        multipart_chunksize=16 * 1024 * 1024,
        max_concurrency=1,
        use_threads=False,
    )

    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            LOG.info("Upload attempt %d/%d -> R2:%s", attempt, attempts, key)
            t0 = time.monotonic()
            client.upload_file(
                str(local_path), bucket, key,
                ExtraArgs={"ContentType": "video/mp4"},
                Config=config,
            )
            elapsed = time.monotonic() - t0
            size_mib = local_path.stat().st_size / 1024 / 1024
            speed = size_mib / elapsed if elapsed > 0 else 0
            LOG.info(
                "Upload complete: %.1f MiB in %.1fs (%.2f MiB/s)",
                size_mib, elapsed, speed,
            )
            return
        except (BotoCoreError, ClientError):
            if attempt >= attempts:
                raise
            delay = 2 ** attempt
            LOG.warning("Upload attempt %d failed; retrying in %ds", attempt, delay)
            time.sleep(delay)


def _download_with_ffmpeg_reconnect(signed_url: str, dst: Path) -> None:
    """
    Download the tl;dv stream to dst via ffmpeg with reconnect flags.
    The signed URL is passed directly to ffmpeg (not printed here).
    """
    LOG.info("Downloading via ffmpeg reconnect -> %s", dst.name)
    cmd = [
        "ffmpeg", "-y",
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "30",
        "-i", signed_url,   # URL intentionally not logged
        "-c", "copy",
        str(dst),
    ]
    # Run ffmpeg; capture stderr so it cannot leak the URL to stdout/console
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        LOG.error("ffmpeg download failed (rc=%d)", result.returncode)
        LOG.debug("ffmpeg stderr (sanitised): %s", _sanitize(result.stderr[-2000:]))
        sys.exit(1)
    LOG.info(
        "Download complete: %s (%.1f MiB)",
        dst.name, dst.stat().st_size / 1024 / 1024,
    )


# ---------------------------------------------------------------------------
# Main transfer logic
# ---------------------------------------------------------------------------

def run_transfer(
    session_id: str,
    tldv_meeting_id: str,
    min_duration_seconds: int,
    dry_run: bool,
) -> None:
    # Validate basic UUID format
    import uuid
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise SystemExit(f"Safety check failed: invalid session UUID: {session_id}")

    if not tldv_meeting_id or len(tldv_meeting_id) < 5:
        raise SystemExit(f"Safety check failed: invalid tl;dv meeting ID: {tldv_meeting_id}")

    bucket = _require_env("R2_BUCKET_NAME")
    object_key = f"{R2_OBJECT_PREFIX}/{session_id}/recording.mp4"

    LOG.info("=== Session Transfer to R2 ===")
    LOG.info("Session ID  : %s", session_id)
    LOG.info("Meeting ID  : %s", tldv_meeting_id)
    LOG.info("R2 target   : %s/%s", bucket, object_key)
    LOG.info("Min duration: %ds  |  Dry-run: %s", min_duration_seconds, dry_run)

    if dry_run:
        LOG.info("[DRY RUN] Validating config — no I/O will be performed.")
        _require_env("TLDV_API_KEY")
        _require_env("R2_ACCOUNT_ID")
        _require_env("R2_ACCESS_KEY_ID")
        _require_env("R2_SECRET_ACCESS_KEY")
        LOG.info("[DRY RUN] All required env vars present.")
        LOG.info("[DRY RUN] Would download tl;dv meeting %s via ffmpeg", tldv_meeting_id)
        LOG.info("[DRY RUN] Would probe duration (min %ds)", min_duration_seconds)
        LOG.info("[DRY RUN] Would remux with -movflags +faststart")
        LOG.info("[DRY RUN] Would upload to R2: %s/%s", bucket, object_key)
        LOG.info("[DRY RUN] Would verify via head_object")
        LOG.info("[DRY RUN] Complete. No files written, no uploads performed.")
        return

    # Idempotency check — skip if already in R2
    client = _r2_client()
    if _object_exists(client, bucket, object_key):
        LOG.info(
            "Object already exists in R2: %s — skipping (idempotent).",
            object_key,
        )
        sys.exit(0)

    api_key = _require_env("TLDV_API_KEY")

    with tempfile.TemporaryDirectory(prefix="dlif_s2_") as tmp_dir:
        tmp = Path(tmp_dir)
        raw_path = tmp / "raw.mp4"
        faststart_path = tmp / "recording.mp4"

        # 1. Resolve signed URL (not logged)
        signed_url = _get_signed_url(api_key, tldv_meeting_id)

        # 2. Download
        _download_with_ffmpeg_reconnect(signed_url, raw_path)

        if not raw_path.exists() or raw_path.stat().st_size < 1024:
            LOG.error("Downloaded file is missing or suspiciously small.")
            sys.exit(1)

        # 3. Duration validation
        duration = _probe_duration(raw_path)
        if duration is None:
            LOG.error("ffprobe could not determine duration — aborting.")
            sys.exit(1)
        LOG.info("Probed duration: %.1fs (minimum: %ds)", duration, min_duration_seconds)
        if duration < min_duration_seconds:
            LOG.error(
                "Duration %.1fs is below minimum %ds — possible partial download.",
                duration, min_duration_seconds,
            )
            sys.exit(1)

        # 4. Remux to faststart
        _remux_faststart(raw_path, faststart_path)

        # 5. Compute checksum (for audit log)
        sha = _sha256_file(faststart_path)
        LOG.info("SHA-256 of faststart file: %s", sha)

        # 6. Upload
        _upload_file(client, faststart_path, bucket, object_key)

        # 7. Verify
        if not _object_exists(client, bucket, object_key):
            LOG.error("Upload verification FAILED: object not found in R2.")
            sys.exit(1)
        LOG.info("Upload verified: R2 head_object returned 200.")

        # 8. Summary
        LOG.info("=== Transfer complete ===")
        LOG.info("  R2 object : %s/%s", bucket, object_key)
        LOG.info("  Duration  : %.1fs (~%.0fmin)", duration, duration / 60)
        LOG.info("  SHA-256   : %s", sha)
        LOG.info("  File size : %.1f MiB", faststart_path.stat().st_size / 1024 / 1024)
        LOG.info("Next step: run process_session_recording_to_r2 locally to update Supabase.")

    LOG.info("Temporary files cleaned up.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Transfer a tl;dv session recording to Cloudflare R2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--session-id", required=True,
        help="DLIF session UUID (e.g. 2b27b883-d215-4530-8df9-340b74024b87)",
    )
    p.add_argument(
        "--tldv-meeting-id", required=True,
        help="tl;dv meeting ID (e.g. 6ab67739571e2f00130d01b6)",
    )
    p.add_argument(
        "--min-duration-seconds", type=int, default=5000,
        help="Minimum accepted video duration in seconds (default: 5000)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Validate config only — no downloads or uploads",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_transfer(
        session_id=args.session_id,
        tldv_meeting_id=args.tldv_meeting_id,
        min_duration_seconds=args.min_duration_seconds,
        dry_run=args.dry_run,
    )
