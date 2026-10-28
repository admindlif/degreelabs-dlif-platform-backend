from datetime import datetime, timezone
import os
from unittest.mock import MagicMock, patch
from uuid import uuid4
import pytest
from botocore.exceptions import SSLError
from sqlalchemy.orm import Session
from app.models.cohort import Cohort
from app.models.phase import Phase
from app.models.program import Program
from app.models.session import (
    RecordingProcessingStatus,
    Session as DBSession,
    SessionStatus,
    SessionType,
)
from app.services.recording_processor import (
    RecordingProcessingResult,
    _cleanup_temp_files,
    _sanitize_error_message,
    process_session_recording_to_r2,
)
from app.services.tldv import TLDVPlaybackData

@pytest.fixture
def sample_session(db: Session) -> DBSession:
    program = Program(name='Test Program', code=f'TP_{uuid4().hex[:6]}')
    db.add(program)
    db.flush()
    phase = Phase(
        program_id=program.id,
        name='DISCOVER',
        code='DISCOVER',
        development_role='THINK',
        sequence=1,
    )
    db.add(phase)
    db.flush()
    cohort = Cohort(program_id=program.id, name='Test Cohort', code=f'TC_{uuid4().hex[:6]}')
    db.add(cohort)
    db.flush()
    session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=10,
        sequence=10,
        title='Test Recording Session',
        session_type=SessionType.LEARN_WORK,
        status=SessionStatus.COMPLETED,
        tldv_meeting_id='meeting_test_123',
        recording_duration_seconds=3600,
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session

def test_already_ready_and_r2_exists_no_reprocess(db: Session, sample_session: DBSession):
    expected_key = f'recordings/{sample_session.id}/recording.mp4'
    sample_session.recording_processing_status = RecordingProcessingStatus.READY
    sample_session.recording_r2_key = expected_key
    sample_session.recording_processed_at = datetime.now(timezone.utc)
    db.commit()
    with patch('app.services.recording_processor.object_exists', return_value=True) as mock_obj_exists, \
         patch('app.services.recording_processor.ensure_seekable_recording_file') as mock_ensure, \
         patch('app.services.recording_processor.upload_file') as mock_upload:
        result = process_session_recording_to_r2(sample_session.id, db=db)
        assert result.is_ready
        assert result.status == RecordingProcessingStatus.READY
        assert result.object_key == expected_key
        mock_ensure.assert_not_called()
        mock_upload.assert_not_called()
        mock_obj_exists.assert_called_with(expected_key)

def test_r2_exists_recovers_db_to_ready(db: Session, sample_session: DBSession):
    expected_key = f'recordings/{sample_session.id}/recording.mp4'
    assert sample_session.recording_processing_status == RecordingProcessingStatus.PENDING
    assert sample_session.recording_r2_key is None
    with patch('app.services.recording_processor.object_exists', return_value=True), \
         patch('app.services.recording_processor.ensure_seekable_recording_file') as mock_ensure, \
         patch('app.services.recording_processor.upload_file') as mock_upload:
        result = process_session_recording_to_r2(sample_session.id, db=db)
        assert result.is_ready
        assert result.status == RecordingProcessingStatus.READY
        assert result.object_key == expected_key
        mock_ensure.assert_not_called()
        mock_upload.assert_not_called()
        db.refresh(sample_session)
        assert sample_session.recording_processing_status == RecordingProcessingStatus.READY
        assert sample_session.recording_r2_key == expected_key
        assert sample_session.recording_processed_at is not None
        assert sample_session.recording_processing_error is None

def test_provider_recording_not_ready_remains_pending(db: Session, sample_session: DBSession):
    mock_client = MagicMock()
    mock_client.get_recording_playback.return_value = TLDVPlaybackData(
        status='processing',
        playback_url=None,
    )
    with patch('app.services.recording_processor.object_exists', return_value=False), \
         patch('app.services.recording_processor.get_tldv_client', return_value=mock_client), \
         patch('app.services.recording_processor.ensure_seekable_recording_file') as mock_ensure:
        result = process_session_recording_to_r2(sample_session.id, db=db)
        assert result.is_pending
        assert result.status == RecordingProcessingStatus.PENDING
        assert 'not ready' in (result.detail or '')
        mock_ensure.assert_not_called()
        db.refresh(sample_session)
        assert sample_session.recording_processing_status == RecordingProcessingStatus.PENDING
        assert sample_session.recording_processing_error is None

def test_provider_no_meeting_id_remains_pending(db: Session, sample_session: DBSession):
    sample_session.tldv_meeting_id = None
    db.commit()
    with patch('app.services.recording_processor.object_exists', return_value=False):
        result = process_session_recording_to_r2(sample_session.id, db=db)
        assert result.is_pending
        assert result.status == RecordingProcessingStatus.PENDING
        db.refresh(sample_session)
        assert sample_session.recording_processing_status == RecordingProcessingStatus.PENDING

def test_successful_processing_pipeline(db: Session, sample_session: DBSession):
    expected_key = f'recordings/{sample_session.id}/recording.mp4'
    mock_client = MagicMock()
    mock_client.get_recording_playback.return_value = TLDVPlaybackData(
        status='ready',
        playback_url='https://puttanesca.tldv.io/download.mp4?temp=1',
    )
    state = {'uploaded': False}
    def fake_object_exists(key):
        return state['uploaded']
    def fake_upload(local_path, object_key, content_type='video/mp4'):
        state['uploaded'] = True
    temp_seekable_path = f'/tmp/poc_media/session_{sample_session.id}_seekable.mp4'
    with patch('app.services.recording_processor.object_exists', side_effect=fake_object_exists), \
         patch('app.services.recording_processor.get_tldv_client', return_value=mock_client), \
         patch('app.services.recording_processor.ensure_seekable_recording_file', return_value=temp_seekable_path) as mock_ensure, \
         patch('app.services.recording_processor.upload_file', side_effect=fake_upload) as mock_upload:
        result = process_session_recording_to_r2(sample_session.id, db=db)
        assert result.is_ready
        assert result.status == RecordingProcessingStatus.READY
        assert result.object_key == expected_key
        mock_ensure.assert_called_once_with(sample_session)
        mock_upload.assert_called_once_with(
            local_path=temp_seekable_path,
            object_key=expected_key,
            content_type='video/mp4',
        )
        db.refresh(sample_session)
        assert sample_session.recording_processing_status == RecordingProcessingStatus.READY
        assert sample_session.recording_r2_key == expected_key
        assert sample_session.recording_processed_at is not None
        assert sample_session.recording_processing_error is None

def test_processing_failure_marks_failed(db: Session, sample_session: DBSession):
    mock_client = MagicMock()
    mock_client.get_recording_playback.return_value = TLDVPlaybackData(
        status='ready',
        playback_url='https://puttanesca.tldv.io/download.mp4',
    )

    with patch('app.services.recording_processor.object_exists', return_value=False), \
         patch('app.services.recording_processor.get_tldv_client', return_value=mock_client), \
         patch(
             'app.services.recording_processor.ensure_seekable_recording_file',
             side_effect=RuntimeError('FFmpeg produced invalid media output'),
         ):
        result = process_session_recording_to_r2(sample_session.id, db=db)

        assert result.is_failed
        assert result.status == RecordingProcessingStatus.FAILED
        assert 'FFmpeg produced invalid media output' in (result.error or '')

        db.refresh(sample_session)

        assert (
            sample_session.recording_processing_status
            == RecordingProcessingStatus.FAILED
        )
        assert (
            'FFmpeg produced invalid media output'
            in (sample_session.recording_processing_error or '')
        )


def test_transient_r2_ssl_failure_returns_pending(
    db: Session,
    sample_session: DBSession,
):
    mock_client = MagicMock()
    mock_client.get_recording_playback.return_value = TLDVPlaybackData(
        status='ready',
        playback_url='https://puttanesca.tldv.io/download.mp4',
    )

    temp_seekable_path = (
        f'/tmp/poc_media/session_{sample_session.id}_seekable.mp4'
    )

    ssl_error = SSLError(
        endpoint_url='https://r2.example.invalid/recording.mp4',
        error=RuntimeError('UNEXPECTED_EOF_WHILE_READING'),
    )

    with patch(
        'app.services.recording_processor.object_exists',
        return_value=False,
    ), patch(
        'app.services.recording_processor.get_tldv_client',
        return_value=mock_client,
    ), patch(
        'app.services.recording_processor.ensure_seekable_recording_file',
        return_value=temp_seekable_path,
    ), patch(
        'app.services.recording_processor.upload_file',
        side_effect=ssl_error,
    ) as mock_upload:

        result = process_session_recording_to_r2(
            sample_session.id,
            db=db,
        )

        assert result.is_pending
        assert result.status == RecordingProcessingStatus.PENDING
        assert 'retried automatically' in (result.detail or '')
        assert result.error is not None

        mock_upload.assert_called_once()

        db.refresh(sample_session)

        assert (
            sample_session.recording_processing_status
            == RecordingProcessingStatus.PENDING
        )
        assert sample_session.recording_processing_error is not None

def test_repeat_execution_after_success_does_not_reprocess(db: Session, sample_session: DBSession):
    expected_key = f'recordings/{sample_session.id}/recording.mp4'
    mock_client = MagicMock()
    mock_client.get_recording_playback.return_value = TLDVPlaybackData(
        status='ready',
        playback_url='https://puttanesca.tldv.io/download.mp4',
    )
    state = {'uploaded': False}
    def fake_object_exists(key):
        return state['uploaded']
    def fake_upload(local_path, object_key, content_type='video/mp4'):
        state['uploaded'] = True
    temp_seekable_path = f'/tmp/poc_media/session_{sample_session.id}_seekable.mp4'
    with patch('app.services.recording_processor.object_exists', side_effect=fake_object_exists), \
         patch('app.services.recording_processor.get_tldv_client', return_value=mock_client), \
         patch('app.services.recording_processor.ensure_seekable_recording_file', return_value=temp_seekable_path) as mock_ensure, \
         patch('app.services.recording_processor.upload_file', side_effect=fake_upload) as mock_upload:
        res1 = process_session_recording_to_r2(sample_session.id, db=db)
        assert res1.is_ready
        assert mock_ensure.call_count == 1
        assert mock_upload.call_count == 1
        res2 = process_session_recording_to_r2(sample_session.id, db=db)
        assert res2.is_ready
        assert mock_ensure.call_count == 1
        assert mock_upload.call_count == 1

def test_error_sanitization_removes_sensitive_query_strings():
    dirty = 'Failed download from https://puttanesca.tldv.io/download.mp4?sig=987654321 with HTTP 500'
    cleaned = _sanitize_error_message(dirty)
    assert 'sig=987654321' not in cleaned
    assert 'https://puttanesca.tldv.io/download.mp4' in cleaned

def test_cleanup_preserves_legacy_files():
    session_id = uuid4()
    poc_dir = os.path.join(os.getcwd(), 'tmp', 'poc_media')
    os.makedirs(poc_dir, exist_ok=True)
    session_raw = os.path.join(poc_dir, f'session_{session_id}_raw.mp4')
    session_seekable = os.path.join(poc_dir, f'session_{session_id}_seekable.mp4')
    legacy_file = os.path.join(poc_dir, 'session_2_seekable.mp4')
    for p in [session_raw, session_seekable, legacy_file]:
        with open(p, 'w') as f:
            f.write('test')
    try:
        _cleanup_temp_files(session_id, seekable_file=session_seekable)
        assert not os.path.exists(session_raw)
        assert not os.path.exists(session_seekable)
        assert os.path.exists(legacy_file)
    finally:
        if os.path.exists(legacy_file):
            os.remove(legacy_file)
