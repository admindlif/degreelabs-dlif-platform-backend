"""Safely normalize one existing R2 recording for mobile audio playback.

Dry-run is the default. Execution requires both ``--execute`` and an exact
session-ID confirmation. The original object is copied to a unique backup key
before the corrected object can replace the canonical key. Cloud objects are
never deleted by this script.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any
from uuid import UUID, uuid4

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import Config


TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=64 * 1024 * 1024,
    multipart_chunksize=16 * 1024 * 1024,
    max_concurrency=1,
    use_threads=False,
)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_object_keys(session_id: UUID, run_token: str) -> tuple[str, str, str]:
    session_value = str(session_id)
    canonical = f"recordings/{session_value}/recording.mp4"
    backup = (
        f"recordings-backup/mobile-audio/{session_value}/"
        f"{run_token}/recording.mp4"
    )
    staging = (
        f"recordings-staging/mobile-audio/{session_value}/"
        f"{run_token}/recording.mp4"
    )
    return canonical, backup, staging


def _probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries",
            (
                "stream=codec_type,codec_name,profile,extradata_size,"
                "sample_rate,channels:format=duration"
            ),
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("ffprobe could not inspect the recording")
    return json.loads(result.stdout)


def validate_mobile_output(
    source_probe: dict[str, Any],
    output_probe: dict[str, Any],
) -> None:
    output_streams = output_probe.get("streams", [])
    video_streams = [
        stream for stream in output_streams if stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream for stream in output_streams if stream.get("codec_type") == "audio"
    ]
    if len(video_streams) != 1 or len(audio_streams) != 1:
        raise ValueError("Corrected recording must contain one video and one audio stream")

    audio = audio_streams[0]
    profile = str(audio.get("profile", "")).strip().lower()
    if audio.get("codec_name") != "aac" or profile not in {
        "lc",
        "aac lc",
        "low complexity",
    }:
        raise ValueError("Corrected recording audio is not AAC-LC")
    if int(audio.get("extradata_size") or 0) <= 0:
        raise ValueError("Corrected recording lacks AAC decoder configuration")
    if int(audio.get("sample_rate") or 0) != 48_000:
        raise ValueError("Corrected recording audio is not 48 kHz")
    if int(audio.get("channels") or 0) != 2:
        raise ValueError("Corrected recording audio is not stereo")

    source_video = next(
        (
            stream
            for stream in source_probe.get("streams", [])
            if stream.get("codec_type") == "video"
        ),
        None,
    )
    if not source_video or video_streams[0].get("codec_name") != source_video.get(
        "codec_name"
    ):
        raise ValueError("Corrected recording did not preserve the video codec")

    source_duration = float(source_probe.get("format", {}).get("duration") or 0)
    output_duration = float(output_probe.get("format", {}).get("duration") or 0)
    if source_duration <= 0 or output_duration <= 0:
        raise ValueError("Recording duration could not be validated")
    tolerance = max(1.0, source_duration * 0.005)
    if abs(source_duration - output_duration) > tolerance:
        raise ValueError("Corrected recording duration differs from the original")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _client():
    return boto3.client(
        "s3",
        endpoint_url=_required_env("R2_ENDPOINT"),
        aws_access_key_id=_required_env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=_required_env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"total_max_attempts": 10, "mode": "standard"},
            connect_timeout=30,
            read_timeout=300,
            tcp_keepalive=True,
        ),
    )


def _copy_object(client, bucket: str, source_key: str, destination_key: str) -> None:
    client.copy(
        {"Bucket": bucket, "Key": source_key},
        bucket,
        destination_key,
        Config=TRANSFER_CONFIG,
    )


def _head_size(client, bucket: str, key: str) -> int:
    return int(client.head_object(Bucket=bucket, Key=key)["ContentLength"])


def _download_and_verify_hash(
    client,
    bucket: str,
    key: str,
    destination: Path,
    expected_size: int,
    expected_sha256: str,
) -> None:
    client.download_file(bucket, key, str(destination))
    if destination.stat().st_size != expected_size:
        raise RuntimeError(f"Downloaded object size verification failed for {key}")
    if _sha256(destination) != expected_sha256:
        raise RuntimeError(f"Downloaded object hash verification failed for {key}")


def reprocess(session_id: UUID, execute: bool, confirmation: str) -> None:
    bucket = _required_env("R2_BUCKET_NAME")
    client = _client()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_token = f"{timestamp}-{uuid4().hex[:8]}"
    canonical_key, backup_key, staging_key = build_object_keys(
        session_id,
        run_token,
    )

    original_size = _head_size(client, bucket, canonical_key)
    print(f"Canonical object found: {canonical_key}")
    print(f"Canonical size: {original_size} bytes")
    print(f"Planned backup key: {backup_key}")
    print(f"Planned staging key: {staging_key}")

    if not execute:
        print("Dry run complete. No R2 objects were written.")
        return
    if confirmation.strip() != str(session_id):
        raise ValueError("Execution confirmation must exactly match the session UUID")

    replacement_started = False
    backup_verified = False

    with tempfile.TemporaryDirectory(prefix="dlif-recording-audio-") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "original.mp4"
        output_path = temp_path / "mobile-compatible.mp4"
        backup_path = temp_path / "verified-backup.mp4"
        staging_path = temp_path / "verified-staging.mp4"
        canonical_path = temp_path / "verified-canonical.mp4"
        rollback_path = temp_path / "verified-rollback.mp4"

        print("Downloading canonical object...")
        client.download_file(bucket, canonical_key, str(source_path))
        if source_path.stat().st_size != original_size:
            raise RuntimeError("Downloaded original size does not match R2")
        original_sha256 = _sha256(source_path)
        print(f"Original SHA-256: {original_sha256}")

        print("Creating uniquely versioned backup copy...")
        _copy_object(client, bucket, canonical_key, backup_key)
        if _head_size(client, bucket, backup_key) != original_size:
            raise RuntimeError("Backup size verification failed")
        _download_and_verify_hash(
            client,
            bucket,
            backup_key,
            backup_path,
            original_size,
            original_sha256,
        )
        backup_verified = True

        source_probe = _probe(source_path)
        command = [
            "ffmpeg",
            "-y",
            "-i", str(source_path),
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-profile:a", "aac_low",
            "-ar", "48000",
            "-ac", "2",
            "-b:a", "160k",
            "-movflags", "+faststart",
            str(output_path),
        ]
        print("Encoding mobile-compatible AAC-LC audio...")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("FFmpeg failed to create the corrected recording")

        validate_mobile_output(source_probe, _probe(output_path))
        output_size = output_path.stat().st_size
        output_sha256 = _sha256(output_path)
        print(f"Corrected size: {output_size} bytes")
        print(f"Corrected SHA-256: {output_sha256}")

        print("Uploading and verifying staging object...")
        client.upload_file(
            str(output_path),
            bucket,
            staging_key,
            ExtraArgs={"ContentType": "video/mp4"},
            Config=TRANSFER_CONFIG,
        )
        if _head_size(client, bucket, staging_key) != output_size:
            raise RuntimeError("Staging object size verification failed")
        _download_and_verify_hash(
            client,
            bucket,
            staging_key,
            staging_path,
            output_size,
            output_sha256,
        )

        try:
            print("Replacing canonical object from verified staging copy...")
            replacement_started = True
            _copy_object(client, bucket, staging_key, canonical_key)
            if _head_size(client, bucket, canonical_key) != output_size:
                raise RuntimeError("Canonical replacement size verification failed")
            _download_and_verify_hash(
                client,
                bucket,
                canonical_key,
                canonical_path,
                output_size,
                output_sha256,
            )
        except Exception:
            if backup_verified and replacement_started:
                print("Replacement verification failed; restoring canonical backup...")
                _copy_object(client, bucket, backup_key, canonical_key)
                if _head_size(client, bucket, canonical_key) != original_size:
                    raise RuntimeError("Automatic rollback verification failed")
                _download_and_verify_hash(
                    client,
                    bucket,
                    canonical_key,
                    rollback_path,
                    original_size,
                    original_sha256,
                )
                print("Automatic rollback completed and verified.")
            raise

    print("Recording remediation completed successfully.")
    print(f"Backup retained at: {backup_key}")
    print(f"Staging copy retained at: {staging_key}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely normalize one existing R2 recording to AAC-LC",
    )
    parser.add_argument("--session-id", required=True, type=UUID)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args()

    reprocess(args.session_id, args.execute, args.confirmation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
