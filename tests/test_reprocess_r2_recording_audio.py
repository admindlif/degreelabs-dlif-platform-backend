"""Tests for safe R2 recording audio remediation validation."""

import hashlib
from uuid import UUID

import pytest

from scripts.reprocess_r2_recording_audio import (
    _download_and_verify_hash,
    build_object_keys,
    validate_mobile_output,
)


SESSION_ID = UUID("c62b177f-c1bf-4814-ae97-cc072b7f6c60")


def _probe(*, profile: str = "LC", extradata_size: int = 5, duration: str = "120.0"):
    return {
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "profile": profile,
                "extradata_size": extradata_size,
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
        "format": {"duration": duration},
    }


def test_build_object_keys_scopes_backup_and_staging_to_session():
    canonical, backup, staging = build_object_keys(SESSION_ID, "run-token")

    assert canonical == f"recordings/{SESSION_ID}/recording.mp4"
    assert backup == (
        f"recordings-backup/mobile-audio/{SESSION_ID}/run-token/recording.mp4"
    )
    assert staging == (
        f"recordings-staging/mobile-audio/{SESSION_ID}/run-token/recording.mp4"
    )


def test_validate_mobile_output_accepts_verified_aac_lc():
    validate_mobile_output(_probe(), _probe())


@pytest.mark.parametrize(
    ("profile", "extradata_size"),
    [("-1", 0), ("HE-AAC", 5), ("LC", 0)],
)
def test_validate_mobile_output_rejects_incompatible_aac(
    profile: str,
    extradata_size: int,
):
    with pytest.raises(ValueError):
        validate_mobile_output(
            _probe(),
            _probe(profile=profile, extradata_size=extradata_size),
        )


def test_validate_mobile_output_rejects_duration_mismatch():
    with pytest.raises(ValueError, match="duration"):
        validate_mobile_output(_probe(duration="120"), _probe(duration="100"))


def test_download_verification_requires_matching_hash(tmp_path):
    payload = b"verified recording bytes"

    class FakeClient:
        @staticmethod
        def download_file(bucket, key, destination):
            del bucket, key
            with open(destination, "wb") as handle:
                handle.write(payload)

    _download_and_verify_hash(
        FakeClient(),
        "bucket",
        "recording.mp4",
        tmp_path / "downloaded.mp4",
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )

    with pytest.raises(RuntimeError, match="hash verification"):
        _download_and_verify_hash(
            FakeClient(),
            "bucket",
            "recording.mp4",
            tmp_path / "mismatch.mp4",
            len(payload),
            hashlib.sha256(b"different").hexdigest(),
        )
