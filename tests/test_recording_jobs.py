"""
Tests for Automatic Batch Recording Job (app.services.recording_jobs)
Verifies:
A. ended + pending + tldv_meeting_id session is selected
B. future session is not selected
C. cancelled session is not selected
D. ready session is not selected
E. processing session is not selected
F. failed session is not selected
G. session without tldv_meeting_id is not selected
H. provider-not-ready result does not stop batch
I. one failed session does not stop later sessions
J. limit is respected
K. deterministic ordering works (oldest ended first)
L. summary counts are correct
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.models.cohort import Cohort
from app.models.phase import Phase
from app.models.program import Program
from app.models.session import (
    RecordingProcessingStatus,
    Session as DBSession,
    SessionStatus,
    SessionType,
)
from app.services.recording_jobs import (
    RecordingBatchResult,
    claim_next_pending_recording_candidate,
    get_pending_recording_candidates,
    process_pending_recordings,
    recover_stale_processing_recordings,

)
from app.services.recording_processor import RecordingProcessingResult


@pytest.fixture
def batch_setup(db: Session):
    program = Program(name="Batch Test Program", code=f"BTP_{uuid4().hex[:6]}")
    db.add(program)
    db.flush()

    phase = Phase(
        program_id=program.id,
        name="DISCOVER",
        code=f"DISC_{uuid4().hex[:6]}",
        development_role="THINK",
        sequence=1,
    )
    db.add(phase)
    db.flush()

    cohort = Cohort(program_id=program.id, name="Batch Test Cohort", code=f"BTC_{uuid4().hex[:6]}")
    db.add(cohort)
    db.commit()

    yield {"cohort": cohort, "phase": phase}

    db.query(DBSession).filter(DBSession.cohort_id == cohort.id).delete()
    db.delete(cohort)
    db.delete(phase)
    db.delete(program)
    db.commit()


def test_candidate_selection_filtering(db: Session, batch_setup: dict):
    """
    Tests A through G:
    Only sessions meeting all criteria:
    - end_at in past
    - status != cancelled
    - recording_processing_status == pending
    - tldv_meeting_id is present and non-empty
    must be selected.
    """
    cohort = batch_setup["cohort"]
    phase = batch_setup["phase"]
    now_utc = datetime.now(timezone.utc)
    past_end = now_utc - timedelta(hours=2)
    future_end = now_utc + timedelta(hours=2)

    # A: Eligible candidate
    valid_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=1,
        sequence=1,
        title="Valid Ended Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=past_end,
        tldv_meeting_id="meeting_valid_1",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )

    # B: Future session (not ended)
    future_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=2,
        sequence=2,
        title="Future Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=future_end,
        tldv_meeting_id="meeting_future_2",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )

    # C: Cancelled session
    cancelled_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=3,
        sequence=3,
        title="Cancelled Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.CANCELLED,
        end_at=past_end,
        tldv_meeting_id="meeting_cancelled_3",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )

    # D: Ready session (already processed)
    ready_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=4,
        sequence=4,
        title="Ready Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=past_end,
        tldv_meeting_id="meeting_ready_4",
        recording_processing_status=RecordingProcessingStatus.READY,
    )

    # E: Processing session (in-flight)
    processing_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=5,
        sequence=5,
        title="In-flight Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=past_end,
        tldv_meeting_id="meeting_proc_5",
        recording_processing_status=RecordingProcessingStatus.PROCESSING,
    )

    # F: Failed session (requires manual or separate retry flow)
    failed_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=6,
        sequence=6,
        title="Failed Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=past_end,
        tldv_meeting_id="meeting_failed_6",
        recording_processing_status=RecordingProcessingStatus.FAILED,
    )

    # G: Ended + pending but no tldv_meeting_id
    no_tldv_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=7,
        sequence=7,
        title="No tl;dv Meeting ID",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=past_end,
        tldv_meeting_id=None,
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )

    # G2: Empty string tldv_meeting_id
    empty_tldv_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=8,
        sequence=8,
        title="Empty tl;dv Meeting ID",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=past_end,
        tldv_meeting_id="",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )

    db.add_all([
        valid_session,
        future_session,
        cancelled_session,
        ready_session,
        processing_session,
        failed_session,
        no_tldv_session,
        empty_tldv_session,
    ])
    db.commit()

    candidates = get_pending_recording_candidates(db, limit=100)
    candidate_ids = {c.id for c in candidates if c.cohort_id == cohort.id}

    assert valid_session.id in candidate_ids
    assert future_session.id not in candidate_ids
    assert cancelled_session.id not in candidate_ids
    assert ready_session.id not in candidate_ids
    assert processing_session.id not in candidate_ids
    assert failed_session.id not in candidate_ids
    assert no_tldv_session.id not in candidate_ids
    assert empty_tldv_session.id not in candidate_ids


def test_batch_limit_and_deterministic_ordering(db: Session, batch_setup: dict):
    """
    Tests J and K:
    - Limit is respected.
    - Deterministic ordering: oldest ended first (end_at ASC, id ASC).
    """
    cohort = batch_setup["cohort"]
    phase = batch_setup["phase"]
    now_utc = datetime.now(timezone.utc)

    # First clean any previous stray sessions for a clean count
    db.query(DBSession).filter(DBSession.recording_processing_status == RecordingProcessingStatus.PENDING).delete()
    db.commit()

    sessions = []
    # Create 5 sessions ended at different hours in past: 5h, 4h, 3h, 2h, 1h ago
    for i in range(1, 6):
        s = DBSession(
            cohort_id=cohort.id,
            phase_id=phase.id,
            session_number=10 + i,
            sequence=10 + i,
            title=f"Ordering Session {i}",
            session_type=SessionType.LEARN_WORK,
            status=SessionStatus.SCHEDULED,
            end_at=now_utc - timedelta(hours=6 - i),
            tldv_meeting_id=f"meeting_order_{i}",
            recording_processing_status=RecordingProcessingStatus.PENDING,
        )
        sessions.append(s)

    db.add_all(sessions)
    db.commit()

    # Request limit=3
    candidates = get_pending_recording_candidates(db, limit=3)

    assert len(candidates) == 3
    # Candidate 0 must be oldest (5h ago -> sessions[0])
    # Candidate 1 must be next (4h ago -> sessions[1])
    # Candidate 2 must be next (3h ago -> sessions[2])
    assert candidates[0].id == sessions[0].id
    assert candidates[1].id == sessions[1].id
    assert candidates[2].id == sessions[2].id


def test_provider_not_ready_does_not_stop_batch(db: Session, batch_setup: dict):
    """
    Tests H:
    If process_session_recording_to_r2 returns PENDING (provider not ready),
    the batch continues and records pending count without failure.
    """
    cohort = batch_setup["cohort"]
    phase = batch_setup["phase"]
    now_utc = datetime.now(timezone.utc)

    db.query(DBSession).filter(DBSession.recording_processing_status == RecordingProcessingStatus.PENDING).delete()
    db.commit()

    s1 = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=21,
        sequence=21,
        title="Session Provider Pending",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=now_utc - timedelta(hours=2),
        tldv_meeting_id="meeting_pnr_1",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )
    s2 = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=22,
        sequence=22,
        title="Session Ready",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=now_utc - timedelta(hours=1),
        tldv_meeting_id="meeting_pnr_2",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )
    db.add_all([s1, s2])
    db.commit()

    def fake_processor(session_id, db=None):
        if session_id == s1.id:
            return RecordingProcessingResult(
                session_id=session_id,
                status=RecordingProcessingStatus.PENDING,
                detail="Provider recording not ready yet",
            )
        return RecordingProcessingResult(
            session_id=session_id,
            status=RecordingProcessingStatus.READY,
            object_key=f"recordings/{session_id}/recording.mp4",
        )

    with patch("app.services.recording_jobs.process_session_recording_to_r2", side_effect=fake_processor):
        result = process_pending_recordings(db=db, limit=10)

        assert s1.id in result.session_ids
        assert s2.id in result.session_ids
        assert result.pending == 1
        assert result.ready == 1
        assert result.failed == 0


def test_failure_isolation_does_not_stop_batch(db: Session, batch_setup: dict):
    """
    Tests I and L:
    If one session raises an unexpected exception or fails genuine processing,
    subsequent sessions in the batch continue normally. Summary counts are accurate.
    """
    cohort = batch_setup["cohort"]
    phase = batch_setup["phase"]
    now_utc = datetime.now(timezone.utc)

    db.query(DBSession).filter(DBSession.recording_processing_status == RecordingProcessingStatus.PENDING).delete()
    db.commit()

    s1 = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=31,
        sequence=31,
        title="Session Succeeds First",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=now_utc - timedelta(hours=3),
        tldv_meeting_id="meeting_fail_1",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )
    s2 = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=32,
        sequence=32,
        title="Session That Crashes",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=now_utc - timedelta(hours=2),
        tldv_meeting_id="meeting_fail_2",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )
    s3 = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=33,
        sequence=33,
        title="Session Succeeds After Crash",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.SCHEDULED,
        end_at=now_utc - timedelta(hours=1),
        tldv_meeting_id="meeting_fail_3",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )
    db.add_all([s1, s2, s3])
    db.commit()

    def fake_processor(session_id, db=None):
        if session_id == s2.id:
            raise RuntimeError("Simulated network crash during session 2")
        return RecordingProcessingResult(
            session_id=session_id,
            status=RecordingProcessingStatus.READY,
            object_key=f"recordings/{session_id}/recording.mp4",
        )

    with patch("app.services.recording_jobs.process_session_recording_to_r2", side_effect=fake_processor):
        result = process_pending_recordings(db=db, limit=10)

        assert s1.id in result.session_ids
        assert s2.id in result.session_ids
        assert s3.id in result.session_ids
        assert result.ready == 2
        assert result.failed == 1
        assert str(s2.id) in result.errors
        assert "Simulated network crash" in result.errors[str(s2.id)]

def test_recover_stale_processing_recordings(
    db: Session,
    batch_setup: dict,
):
    """
    Stale PROCESSING sessions are reset to PENDING.

    Recent PROCESSING sessions and READY/FAILED sessions
    must remain unchanged.
    """
    cohort = batch_setup["cohort"]
    phase = batch_setup["phase"]

    now_utc = datetime.now(timezone.utc)
    past_end = now_utc - timedelta(hours=10)

    stale_processing = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=30,
        sequence=30,
        title="Stale Processing Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.COMPLETED,
        end_at=past_end,
        tldv_meeting_id="meeting_stale_processing",
        recording_processing_status=RecordingProcessingStatus.PROCESSING,
        recording_processing_error="old worker stopped",
        updated_at=now_utc - timedelta(hours=7),
    )

    recent_processing = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=31,
        sequence=31,
        title="Recent Processing Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.COMPLETED,
        end_at=past_end,
        tldv_meeting_id="meeting_recent_processing",
        recording_processing_status=RecordingProcessingStatus.PROCESSING,
        updated_at=now_utc - timedelta(hours=1),
    )

    ready_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=32,
        sequence=32,
        title="Ready Recovery Test Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.COMPLETED,
        end_at=past_end,
        tldv_meeting_id="meeting_ready_recovery",
        recording_processing_status=RecordingProcessingStatus.READY,
        updated_at=now_utc - timedelta(hours=8),
    )

    failed_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=33,
        sequence=33,
        title="Failed Recovery Test Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.COMPLETED,
        end_at=past_end,
        tldv_meeting_id="meeting_failed_recovery",
        recording_processing_status=RecordingProcessingStatus.FAILED,
        updated_at=now_utc - timedelta(hours=8),
    )

    db.add_all(
        [
            stale_processing,
            recent_processing,
            ready_session,
            failed_session,
        ]
    )
    db.commit()

    recovered = recover_stale_processing_recordings(
        db=db,
        stale_after_hours=6,
        as_of=now_utc,
    )

    db.refresh(stale_processing)
    db.refresh(recent_processing)
    db.refresh(ready_session)
    db.refresh(failed_session)

    assert recovered == 1

    assert (
        stale_processing.recording_processing_status
        == RecordingProcessingStatus.PENDING
    )
    assert stale_processing.recording_processing_error is None

    assert (
        recent_processing.recording_processing_status
        == RecordingProcessingStatus.PROCESSING
    )

    assert (
        ready_session.recording_processing_status
        == RecordingProcessingStatus.READY
    )

    assert (
        failed_session.recording_processing_status
        == RecordingProcessingStatus.FAILED
    )
def test_concurrent_worker_skips_locked_candidate(
    db: Session,
    batch_setup: dict,
):
    """
    A second database worker must not claim a row that another
    worker currently holds with a row-level lock.
    """
    cohort = batch_setup["cohort"]
    phase = batch_setup["phase"]
    now_utc = datetime.now(timezone.utc)

    candidate = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=40,
        sequence=40,
        title="Concurrent Claim Session",
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.COMPLETED,
        end_at=now_utc - timedelta(hours=2),
        tldv_meeting_id="meeting_concurrent_claim",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )

    db.add(candidate)
    db.commit()
    db.refresh(candidate)

    # Exclude any unrelated eligible rows already present in the test DB.
    unrelated_ids = {
        session.id
        for session in get_pending_recording_candidates(
            db=db,
            limit=1000,
            as_of=now_utc,
        )
        if session.id != candidate.id
    }

    WorkerSession = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )

    worker_a = WorkerSession()
    worker_b = WorkerSession()

    try:
        # Worker A acquires the PostgreSQL row lock and deliberately
        # keeps the transaction open.
        locked_candidate = (
            worker_a.query(DBSession)
            .filter(DBSession.id == candidate.id)
            .with_for_update()
            .one()
        )

        assert locked_candidate.id == candidate.id

        # Worker B uses SKIP LOCKED and therefore must not obtain
        # the row currently locked by Worker A.
        worker_b_result = claim_next_pending_recording_candidate(
            db=worker_b,
            as_of=now_utc,
            exclude_ids=unrelated_ids,
        )

        assert worker_b_result is None

        # Release Worker A's lock.
        worker_a.rollback()
        worker_b.rollback()

        # Now Worker B should be able to claim the exact same row.
        worker_b_result = claim_next_pending_recording_candidate(
            db=worker_b,
            as_of=now_utc,
            exclude_ids=unrelated_ids,
        )

        assert worker_b_result is not None
        assert worker_b_result.id == candidate.id
        assert (
            worker_b_result.recording_processing_status
            == RecordingProcessingStatus.PROCESSING
        )

    finally:
        worker_a.rollback()
        worker_b.rollback()
        worker_a.close()
        worker_b.close()
