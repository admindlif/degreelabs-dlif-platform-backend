"""
Unit tests for tl;dv meeting discovery candidate matching logic in scripts/discover_tldv_meetings.py.
"""

from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

from app.models.session import Session as DBSession
from app.services.tldv import TLDVMeetingData
from scripts.discover_tldv_meetings import _is_candidate


def test_is_candidate_matches_valid_meeting():
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
    session = DBSession(
        id=uuid4(),
        session_number=1,
        title="DLIF Session 1",
        start_at=now,
    )
    meeting = TLDVMeetingData(
        meeting_id="tldv-meet-123",
        name="DLIF Cohort Onboarding",
        happened_at=(now + timedelta(minutes=15)).isoformat(),
        duration_seconds=4200,
        recording_url=None,
    )
    assert _is_candidate(meeting, session, already_used_meeting_ids=set()) is True


def test_is_candidate_rejects_missing_keyword():
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
    session = DBSession(
        id=uuid4(),
        session_number=1,
        title="DLIF Session 1",
        start_at=now,
    )
    meeting = TLDVMeetingData(
        meeting_id="tldv-meet-123",
        name="Unrelated Team Sync",
        happened_at=now.isoformat(),
        duration_seconds=4200,
        recording_url=None,
    )
    assert _is_candidate(meeting, session, already_used_meeting_ids=set()) is False


def test_is_candidate_rejects_short_duration():
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
    session = DBSession(
        id=uuid4(),
        session_number=1,
        title="DLIF Session 1",
        start_at=now,
    )
    meeting = TLDVMeetingData(
        meeting_id="tldv-meet-123",
        name="DLIF Quick Check",
        happened_at=now.isoformat(),
        duration_seconds=1200,  # Only 20 min < 3600
        recording_url=None,
    )
    assert _is_candidate(meeting, session, already_used_meeting_ids=set()) is False


def test_is_candidate_rejects_outside_window():
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
    session = DBSession(
        id=uuid4(),
        session_number=1,
        title="DLIF Session 1",
        start_at=now,
    )
    meeting = TLDVMeetingData(
        meeting_id="tldv-meet-123",
        name="DLIF Session",
        happened_at=(now + timedelta(hours=3)).isoformat(),  # 3h > 2h window
        duration_seconds=3600,
        recording_url=None,
    )
    assert _is_candidate(meeting, session, already_used_meeting_ids=set()) is False


def test_is_candidate_rejects_already_used_meeting_id():
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
    session = DBSession(
        id=uuid4(),
        session_number=1,
        title="DLIF Session 1",
        start_at=now,
    )
    meeting = TLDVMeetingData(
        meeting_id="tldv-meet-already-used",
        name="DLIF Session",
        happened_at=now.isoformat(),
        duration_seconds=3600,
        recording_url=None,
    )
    assert _is_candidate(meeting, session, already_used_meeting_ids={"tldv-meet-already-used"}) is False


def test_is_candidate_matches_conference_id():
    """Matching google_meet_code / conference_id matches even if title is non-standard."""
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
    session = DBSession(
        id=uuid4(),
        session_number=2,
        title="Session 2",
        start_at=now,
        google_meet_code="abc-defg-hij",
    )
    meeting = TLDVMeetingData(
        meeting_id="tldv-meet-conf-match",
        name="DegreeLabs Cohort Call",  # Does not contain "dlif"
        happened_at=now.isoformat(),
        duration_seconds=3600,
        conference_id="abc-defg-hij",
    )
    assert _is_candidate(meeting, session, already_used_meeting_ids=set()) is True

