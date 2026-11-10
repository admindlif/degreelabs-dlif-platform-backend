"""Unit coverage for mobile-compatible recording preparation."""

import json
from subprocess import CompletedProcess
from unittest.mock import patch

from app.services.tldv import (
    _build_mobile_compatible_remux_command,
    _has_mobile_compatible_aac,
)


def test_mobile_remux_copies_video_and_encodes_aac_lc():
    command = _build_mobile_compatible_remux_command("input.mp4", "output.mp4")

    assert command[:5] == ["ffmpeg", "-y", "-i", "input.mp4", "-map"]
    assert command[-1] == "output.mp4"
    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-profile:a") + 1] == "aac_low"
    assert command[command.index("-ar") + 1] == "48000"
    assert command[command.index("-ac") + 1] == "2"
    assert command[command.index("-movflags") + 1] == "+faststart"


def test_mobile_aac_probe_accepts_lc_with_decoder_configuration():
    probe_result = CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {
                "streams": [
                    {
                        "codec_name": "aac",
                        "profile": "LC",
                        "extradata_size": 5,
                    }
                ]
            }
        ),
        stderr="",
    )

    with patch("app.services.tldv.subprocess.run", return_value=probe_result):
        assert _has_mobile_compatible_aac("recording.mp4") is True


def test_mobile_aac_probe_rejects_provider_audio_without_configuration():
    probe_result = CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {
                "streams": [
                    {
                        "codec_name": "aac",
                        "profile": "-1",
                    }
                ]
            }
        ),
        stderr="",
    )

    with patch("app.services.tldv.subprocess.run", return_value=probe_result):
        assert _has_mobile_compatible_aac("recording.mp4") is False


def test_mobile_aac_probe_fails_closed_on_invalid_output():
    probe_result = CompletedProcess(
        args=[],
        returncode=0,
        stdout="not-json",
        stderr="",
    )

    with patch("app.services.tldv.subprocess.run", return_value=probe_result):
        assert _has_mobile_compatible_aac("recording.mp4") is False
