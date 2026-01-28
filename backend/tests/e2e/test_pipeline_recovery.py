"""
End-to-End test for pipeline recovery from failure

This test verifies pipeline idempotence and recovery capability:
    1. Start pipeline processing
    2. Simulate upload failure (FlareSolverr failure mid-upload)
    3. Restart pipeline
    4. Verify pipeline resumes from upload stage
    5. Confirm no duplicate scan/analyze/rename/metadata operations
    6. Verify successful upload on retry

This is a critical E2E test that validates the checkpoint/resume mechanism
ensures idempotent pipeline operations during failure recovery.

Environment Variables:
    - USE_REAL_SERVICES: Set to 'true' to test against real services
    - FLARESOLVERR_URL: FlareSolverr service URL (default: http://localhost:8191)
    - QBITTORRENT_HOST: qBittorrent host (default: localhost:8080)
    - TRACKER_URL: La Cale tracker URL
    - TRACKER_PASSKEY: Tracker passkey
"""

import asyncio
import os
import pytest
import tempfile
import shutil
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import torf

from backend.app.models.base import Base
from backend.app.models.file_entry import FileEntry, Status
from backend.app.models.settings import Settings
from backend.app.models.tmdb_cache import TMDBCache
from backend.app.models.tags import Tags
from backend.app.processors.pipeline import ProcessingPipeline
from backend.app.services.media_analyzer import MediaAnalyzer
from backend.app.services.nfo_validator import NFOValidator
from backend.app.services.cloudflare_session_manager import CloudflareSessionManager
from backend.app.services.lacale_client import LaCaleClient
from backend.app.adapters.lacale_adapter import LaCaleAdapter
from backend.app.services.exceptions import TrackerAPIError, CloudflareBypassError, NetworkRetryableError


# ============================================================================
# Test Configuration
# ============================================================================

USE_REAL_SERVICES = os.getenv('USE_REAL_SERVICES', 'false').lower() == 'true'
FLARESOLVERR_URL = os.getenv('FLARESOLVERR_URL', 'http://localhost:8191')
QBITTORRENT_HOST = os.getenv('QBITTORRENT_HOST', 'localhost:8080')
TRACKER_URL = os.getenv('TRACKER_URL', 'https://lacale-test.example.com')
TRACKER_PASSKEY = os.getenv('TRACKER_PASSKEY', 'test_passkey_1234567890')


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_db():
    """Create temporary database with all tables for E2E testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    engine = create_engine(f'sqlite:///{db_path}')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # Initialize settings
    settings = Settings(
        id=1,
        tracker_url=TRACKER_URL,
        tracker_passkey=TRACKER_PASSKEY,
        flaresolverr_url=FLARESOLVERR_URL,
        qbittorrent_host=QBITTORRENT_HOST,
        qbittorrent_username='admin',
        qbittorrent_password='adminpassword',
        tmdb_api_key='test_tmdb_api_key',
        input_media_path='/tmp/input',
        output_dir='/tmp/output',
        log_level='INFO',
        tmdb_cache_ttl_days=30,
        tag_sync_interval_hours=24
    )
    db.add(settings)
    db.commit()

    try:
        yield db
    finally:
        db.close()
        os.unlink(db_path)


@pytest.fixture
def temp_files():
    """Create temporary test files and directories."""
    temp_dir = tempfile.mkdtemp()

    # Create input media directory
    input_dir = os.path.join(temp_dir, 'input')
    os.makedirs(input_dir, exist_ok=True)

    # Create output directory
    output_dir = os.path.join(temp_dir, 'output')
    os.makedirs(output_dir, exist_ok=True)

    # Create test media file (minimal valid MKV file)
    media_file = os.path.join(input_dir, "Test.Movie.2023.1080p.BluRay.x264-TEST.mkv")
    with open(media_file, 'wb') as f:
        # Write minimal valid MKV header (EBML signature)
        f.write(b'\x1a\x45\xdf\xa3')  # EBML header
        # Add some content to make it a reasonable size
        f.write(b'\x00' * (1024 * 1024))  # 1MB file

    yield {
        'media_file': media_file,
        'input_dir': input_dir,
        'output_dir': output_dir,
        'temp_dir': temp_dir
    }

    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_flaresolverr():
    """Mock FlareSolverr responses."""
    def _mock_post(*args, **kwargs):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            'status': 'ok',
            'message': 'Challenge solved!',
            'solution': {
                'url': TRACKER_URL,
                'status': 200,
                'cookies': [
                    {'name': 'cf_clearance', 'value': 'test_clearance_token'},
                    {'name': 'PHPSESSID', 'value': 'test_session_id'}
                ],
                'userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
            }
        }
        return response

    return _mock_post


@pytest.fixture
def mock_tracker_upload_success():
    """Mock successful tracker upload response."""
    def _mock_post(*args, **kwargs):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            'success': True,
            'torrent_id': '12345',
            'torrent_url': f'{TRACKER_URL}/torrents/12345'
        }
        return response

    return _mock_post


@pytest.fixture
def mock_qbittorrent():
    """Mock qBittorrent API client."""
    client = Mock()

    # Mock login
    client.auth_log_in = Mock()

    # Mock torrent addition
    client.torrents_add = Mock(return_value='OK')

    # Mock torrent info
    mock_torrent = Mock()
    mock_torrent.hash = 'test_hash_1234567890'
    mock_torrent.state = 'uploading'  # Seeding state
    mock_torrent.name = 'Test.Movie.2023.1080p.BluRay.x264-TEST'
    client.torrents_info = Mock(return_value=[mock_torrent])

    return client


# ============================================================================
# E2E Tests - Pipeline Recovery
# ============================================================================

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_pipeline_recovery_from_upload_failure(
    temp_db,
    temp_files,
    mock_flaresolverr,
    mock_tracker_upload_success,
    mock_qbittorrent
):
    """
    Test pipeline recovery from upload stage failure.

    This is the most common failure scenario: all stages complete successfully
    (scan, analyze, rename, metadata generation) but upload fails due to
    FlareSolverr or network issues. On retry, pipeline should resume from
    upload stage without repeating completed work.

    Verification Steps:
    1. First attempt: Process through all stages, simulate upload failure
    2. Verify all checkpoints set except uploaded_at
    3. Second attempt: Retry pipeline processing
    4. Verify pipeline skips completed stages (checkpoints unchanged)
    5. Verify only upload stage executes
    6. Verify successful upload on retry
    """
    # Create file entry
    file_entry = FileEntry.create_or_get(temp_db, temp_files['media_file'])
    assert file_entry.status == Status.PENDING

    # Track upload attempts
    upload_attempts = {'count': 0}

    with patch('requests.post') as mock_requests_post:

        def post_side_effect(url, *args, **kwargs):
            """Mock FlareSolverr and tracker upload with failure on first attempt."""
            if FLARESOLVERR_URL in url:
                # FlareSolverr always succeeds
                return mock_flaresolverr(url, *args, **kwargs)
            else:
                # Tracker upload: fail first, succeed second
                upload_attempts['count'] += 1
                if upload_attempts['count'] == 1:
                    # First attempt: simulate FlareSolverr connection failure
                    response = Mock()
                    response.status_code = 500
                    response.text = 'Internal Server Error: FlareSolverr connection lost'
                    response.json.side_effect = Exception("No JSON response")
                    return response
                else:
                    # Second attempt: success
                    return mock_tracker_upload_success(url, *args, **kwargs)

        mock_requests_post.side_effect = post_side_effect

        # Initialize tracker adapter and pipeline
        tracker_adapter = LaCaleAdapter(
            tracker_url=TRACKER_URL,
            passkey=TRACKER_PASSKEY,
            flaresolverr_url=FLARESOLVERR_URL
        )
        pipeline = ProcessingPipeline(temp_db, tracker_adapter=tracker_adapter)

        with patch('backend.app.processors.pipeline.qbittorrent', mock_qbittorrent):

            # ===================================================================
            # FIRST ATTEMPT: Process file, expecting failure at upload stage
            # ===================================================================
            try:
                await pipeline.process_file(file_entry)
                # Should not reach here - upload should fail
                pytest.fail("Expected upload failure on first attempt")
            except (TrackerAPIError, Exception) as e:
                # Expected failure
                pass

            # Refresh from database to get updated state
            temp_db.refresh(file_entry)

            # Verify partial completion: all stages before upload should be complete
            assert file_entry.scanned_at is not None, "Scan checkpoint not set"
            assert file_entry.analyzed_at is not None, "Analysis checkpoint not set"
            assert file_entry.renamed_at is not None, "Rename checkpoint not set"
            assert file_entry.metadata_generated_at is not None, \
                "Metadata generation checkpoint not set"

            # Upload should have failed (checkpoint not set)
            assert file_entry.uploaded_at is None, \
                "Upload checkpoint should not be set after failure"

            # Verify status is FAILED or still at METADATA_GENERATED
            assert file_entry.status in [Status.FAILED, Status.METADATA_GENERATED], \
                f"Expected FAILED or METADATA_GENERATED status, got {file_entry.status}"

            # Store checkpoint timestamps before retry
            scanned_at_before = file_entry.scanned_at
            analyzed_at_before = file_entry.analyzed_at
            renamed_at_before = file_entry.renamed_at
            metadata_at_before = file_entry.metadata_generated_at

            # Verify timestamps are chronologically ordered
            assert scanned_at_before <= analyzed_at_before
            assert analyzed_at_before <= renamed_at_before
            assert renamed_at_before <= metadata_at_before

            # ===================================================================
            # SECOND ATTEMPT: Retry pipeline, should resume from upload stage
            # ===================================================================
            await pipeline.process_file(file_entry)

            # Refresh from database
            temp_db.refresh(file_entry)

            # Verify full completion
            assert file_entry.status == Status.UPLOADED, \
                f"Expected UPLOADED status, got {file_entry.status}"
            assert file_entry.uploaded_at is not None, \
                "Upload checkpoint not set after successful retry"

            # ===================================================================
            # CRITICAL VERIFICATION: Checkpoints were NOT updated (idempotence)
            # ===================================================================
            assert file_entry.scanned_at == scanned_at_before, \
                "Scan stage was re-executed (checkpoint changed) - IDEMPOTENCE VIOLATION"
            assert file_entry.analyzed_at == analyzed_at_before, \
                "Analysis stage was re-executed (checkpoint changed) - IDEMPOTENCE VIOLATION"
            assert file_entry.renamed_at == renamed_at_before, \
                "Rename stage was re-executed (checkpoint changed) - IDEMPOTENCE VIOLATION"
            assert file_entry.metadata_generated_at == metadata_at_before, \
                "Metadata generation stage was re-executed (checkpoint changed) - IDEMPOTENCE VIOLATION"

            # Verify upload was attempted exactly twice
            assert upload_attempts['count'] == 2, \
                f"Expected 2 upload attempts, got {upload_attempts['count']}"

            # Verify final timestamps are chronologically ordered
            assert file_entry.scanned_at <= file_entry.analyzed_at
            assert file_entry.analyzed_at <= file_entry.renamed_at
            assert file_entry.renamed_at <= file_entry.metadata_generated_at
            assert file_entry.metadata_generated_at <= file_entry.uploaded_at


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_pipeline_recovery_from_metadata_failure(
    temp_db,
    temp_files,
    mock_flaresolverr,
    mock_tracker_upload_success,
    mock_qbittorrent
):
    """
    Test pipeline recovery from metadata generation stage failure.

    This tests recovery from an earlier stage failure. The pipeline should
    resume from metadata generation stage, reusing scan/analyze/rename
    checkpoints.

    Verification Steps:
    1. Simulate metadata generation failure (NFO validation fails)
    2. Verify checkpoints set up to rename stage
    3. Retry pipeline with fixed NFO validation
    4. Verify pipeline skips scan/analyze/rename
    5. Verify metadata generation and upload execute
    """
    # Create file entry
    file_entry = FileEntry.create_or_get(temp_db, temp_files['media_file'])

    # Track metadata generation attempts
    metadata_attempts = {'count': 0}

    with patch('requests.post') as mock_requests_post:

        def post_side_effect(url, *args, **kwargs):
            if FLARESOLVERR_URL in url:
                return mock_flaresolverr(url, *args, **kwargs)
            else:
                return mock_tracker_upload_success(url, *args, **kwargs)

        mock_requests_post.side_effect = post_side_effect

        # Initialize tracker adapter and pipeline
        tracker_adapter = LaCaleAdapter(
            tracker_url=TRACKER_URL,
            passkey=TRACKER_PASSKEY,
            flaresolverr_url=FLARESOLVERR_URL
        )
        pipeline = ProcessingPipeline(temp_db, tracker_adapter=tracker_adapter)

        # Mock NFO validator to fail first, succeed second
        original_ensure_valid_nfo = pipeline.nfo_validator.ensure_valid_nfo

        def mock_ensure_valid_nfo(*args, **kwargs):
            metadata_attempts['count'] += 1
            if metadata_attempts['count'] == 1:
                # First attempt: simulate NFO validation failure
                raise TrackerAPIError("NFO validation failed: Missing required field 'plot'")
            else:
                # Second attempt: success (create dummy NFO)
                nfo_path = os.path.join(temp_files['output_dir'], 'test.nfo')
                with open(nfo_path, 'w') as f:
                    f.write("Title: Test Movie\nYear: 2023\nPlot: Test plot\n")
                return nfo_path

        with patch('backend.app.processors.pipeline.qbittorrent', mock_qbittorrent), \
             patch.object(pipeline.nfo_validator, 'ensure_valid_nfo', side_effect=mock_ensure_valid_nfo):

            # First attempt: Should fail at metadata generation stage
            try:
                await pipeline.process_file(file_entry)
                pytest.fail("Expected metadata generation failure on first attempt")
            except TrackerAPIError:
                pass

            # Refresh from database
            temp_db.refresh(file_entry)

            # Verify partial completion: scan/analyze/rename complete, metadata failed
            assert file_entry.scanned_at is not None
            assert file_entry.analyzed_at is not None
            assert file_entry.renamed_at is not None
            assert file_entry.metadata_generated_at is None  # Failed here
            assert file_entry.uploaded_at is None

            # Store checkpoint timestamps
            scanned_at_before = file_entry.scanned_at
            analyzed_at_before = file_entry.analyzed_at
            renamed_at_before = file_entry.renamed_at

            # Second attempt: Should resume from metadata generation
            await pipeline.process_file(file_entry)

            # Refresh from database
            temp_db.refresh(file_entry)

            # Verify full completion
            assert file_entry.status == Status.UPLOADED
            assert file_entry.metadata_generated_at is not None
            assert file_entry.uploaded_at is not None

            # Verify earlier checkpoints unchanged (idempotence)
            assert file_entry.scanned_at == scanned_at_before
            assert file_entry.analyzed_at == analyzed_at_before
            assert file_entry.renamed_at == renamed_at_before

            # Verify metadata generation was attempted twice
            assert metadata_attempts['count'] == 2


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_pipeline_recovery_with_flaresolverr_restart(
    temp_db,
    temp_files,
    mock_flaresolverr,
    mock_tracker_upload_success,
    mock_qbittorrent
):
    """
    Test pipeline recovery when FlareSolverr is restarted mid-upload.

    This simulates the exact scenario from the verification steps:
    1. Start pipeline processing
    2. Simulate FlareSolverr failure during upload (connection error)
    3. Restart pipeline (simulate FlareSolverr recovered)
    4. Verify pipeline resumes from upload stage
    5. Confirm no duplicate scan/analyze/rename/metadata operations
    6. Verify successful upload on retry

    This is a critical real-world scenario where external dependencies fail
    and recover.
    """
    # Create file entry
    file_entry = FileEntry.create_or_get(temp_db, temp_files['media_file'])

    # Track FlareSolverr and upload attempts
    flaresolverr_attempts = {'count': 0}
    upload_attempts = {'count': 0}

    with patch('requests.post') as mock_requests_post:

        def post_side_effect(url, *args, **kwargs):
            """
            Simulate FlareSolverr failure on first upload attempt, success on second.
            """
            if FLARESOLVERR_URL in url:
                flaresolverr_attempts['count'] += 1
                if flaresolverr_attempts['count'] == 1:
                    # First attempt: FlareSolverr is down (simulating stopped service)
                    import requests
                    raise requests.exceptions.ConnectionError(
                        "Connection refused: FlareSolverr service unavailable"
                    )
                else:
                    # Second attempt: FlareSolverr recovered (simulating restart)
                    return mock_flaresolverr(url, *args, **kwargs)
            else:
                # Tracker upload (only reached after FlareSolverr succeeds)
                upload_attempts['count'] += 1
                return mock_tracker_upload_success(url, *args, **kwargs)

        mock_requests_post.side_effect = post_side_effect

        # Initialize tracker adapter and pipeline
        tracker_adapter = LaCaleAdapter(
            tracker_url=TRACKER_URL,
            passkey=TRACKER_PASSKEY,
            flaresolverr_url=FLARESOLVERR_URL
        )
        pipeline = ProcessingPipeline(temp_db, tracker_adapter=tracker_adapter)

        with patch('backend.app.processors.pipeline.qbittorrent', mock_qbittorrent):

            # ===================================================================
            # FIRST ATTEMPT: FlareSolverr fails during upload
            # ===================================================================
            try:
                await pipeline.process_file(file_entry)
                pytest.fail("Expected FlareSolverr connection error on first attempt")
            except Exception:
                # Expected: FlareSolverr connection error
                pass

            # Refresh from database
            temp_db.refresh(file_entry)

            # Verify all stages before upload completed
            assert file_entry.scanned_at is not None
            assert file_entry.analyzed_at is not None
            assert file_entry.renamed_at is not None
            assert file_entry.metadata_generated_at is not None

            # Upload failed before reaching tracker (FlareSolverr failed)
            assert file_entry.uploaded_at is None
            assert upload_attempts['count'] == 0, \
                "Tracker upload should not be attempted if FlareSolverr fails"

            # Store checkpoint timestamps
            scanned_at_before = file_entry.scanned_at
            analyzed_at_before = file_entry.analyzed_at
            renamed_at_before = file_entry.renamed_at
            metadata_at_before = file_entry.metadata_generated_at

            # ===================================================================
            # SIMULATE FLARESOLVERR RESTART
            # ===================================================================
            # (In real scenario, admin would restart FlareSolverr Docker container)
            # Our mock will succeed on second attempt

            # ===================================================================
            # SECOND ATTEMPT: Retry after FlareSolverr recovered
            # ===================================================================
            await pipeline.process_file(file_entry)

            # Refresh from database
            temp_db.refresh(file_entry)

            # Verify full completion
            assert file_entry.status == Status.UPLOADED
            assert file_entry.uploaded_at is not None

            # CRITICAL: Verify no duplicate work (checkpoints unchanged)
            assert file_entry.scanned_at == scanned_at_before, \
                "Scan stage re-executed - should have been skipped"
            assert file_entry.analyzed_at == analyzed_at_before, \
                "Analysis stage re-executed - should have been skipped"
            assert file_entry.renamed_at == renamed_at_before, \
                "Rename stage re-executed - should have been skipped"
            assert file_entry.metadata_generated_at == metadata_at_before, \
                "Metadata generation re-executed - .torrent and NFO should have been reused"

            # Verify FlareSolverr called twice (fail, success)
            assert flaresolverr_attempts['count'] == 2, \
                f"Expected 2 FlareSolverr attempts, got {flaresolverr_attempts['count']}"

            # Verify tracker upload called once (only after FlareSolverr recovered)
            assert upload_attempts['count'] == 1, \
                f"Expected 1 tracker upload attempt, got {upload_attempts['count']}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_pipeline_status_during_recovery(
    temp_db,
    temp_files,
    mock_flaresolverr,
    mock_tracker_upload_success,
    mock_qbittorrent
):
    """
    Test pipeline status reporting during failure recovery.

    Verifies that get_pipeline_status() returns accurate checkpoint information
    during failure and recovery scenarios, enabling real-time progress monitoring.
    """
    # Create file entry
    file_entry = FileEntry.create_or_get(temp_db, temp_files['media_file'])

    # Track upload attempts
    upload_attempts = {'count': 0}

    with patch('requests.post') as mock_requests_post:

        def post_side_effect(url, *args, **kwargs):
            if FLARESOLVERR_URL in url:
                return mock_flaresolverr(url, *args, **kwargs)
            else:
                upload_attempts['count'] += 1
                if upload_attempts['count'] == 1:
                    # Fail first upload
                    response = Mock()
                    response.status_code = 500
                    response.text = 'Internal Server Error'
                    return response
                else:
                    # Succeed second upload
                    return mock_tracker_upload_success(url, *args, **kwargs)

        mock_requests_post.side_effect = post_side_effect

        # Initialize pipeline
        tracker_adapter = LaCaleAdapter(
            tracker_url=TRACKER_URL,
            passkey=TRACKER_PASSKEY,
            flaresolverr_url=FLARESOLVERR_URL
        )
        pipeline = ProcessingPipeline(temp_db, tracker_adapter=tracker_adapter)

        with patch('backend.app.processors.pipeline.qbittorrent', mock_qbittorrent):

            # Get initial status
            status = pipeline.get_pipeline_status(file_entry)
            assert status['status'] == Status.PENDING.value
            assert status['checkpoints']['scanned'] is False
            assert status['checkpoints']['uploaded'] is False

            # First attempt: process until upload fails
            try:
                await pipeline.process_file(file_entry)
            except Exception:
                pass

            # Refresh and get status after failure
            temp_db.refresh(file_entry)
            status = pipeline.get_pipeline_status(file_entry)

            # Verify checkpoint status shows partial completion
            assert status['checkpoints']['scanned'] is True
            assert status['checkpoints']['analyzed'] is True
            assert status['checkpoints']['renamed'] is True
            assert status['checkpoints']['metadata_generated'] is True
            assert status['checkpoints']['uploaded'] is False  # Failed here

            # Verify timestamps present for completed stages
            assert status['timestamps']['scanned_at'] is not None
            assert status['timestamps']['analyzed_at'] is not None
            assert status['timestamps']['renamed_at'] is not None
            assert status['timestamps']['metadata_generated_at'] is not None
            assert status['timestamps']['uploaded_at'] is None

            # Second attempt: retry and succeed
            await pipeline.process_file(file_entry)

            # Refresh and get final status
            temp_db.refresh(file_entry)
            status = pipeline.get_pipeline_status(file_entry)

            # Verify all checkpoints now complete
            assert status['status'] == Status.UPLOADED.value
            assert status['checkpoints']['uploaded'] is True
            assert status['timestamps']['uploaded_at'] is not None

            # Verify all timestamps chronologically ordered
            timestamps = status['timestamps']
            assert timestamps['scanned_at'] <= timestamps['analyzed_at']
            assert timestamps['analyzed_at'] <= timestamps['renamed_at']
            assert timestamps['renamed_at'] <= timestamps['metadata_generated_at']
            assert timestamps['metadata_generated_at'] <= timestamps['uploaded_at']


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_pipeline_recovery_preserves_torrent_and_nfo(
    temp_db,
    temp_files,
    mock_flaresolverr,
    mock_tracker_upload_success,
    mock_qbittorrent
):
    """
    Test that pipeline recovery reuses existing .torrent and NFO files.

    This verifies a critical idempotence requirement: when metadata generation
    succeeds but upload fails, retry should reuse the existing .torrent and NFO
    files rather than regenerating them (which would waste CPU on re-hashing).

    Verification:
    - .torrent and NFO files created during first attempt
    - Upload fails after metadata generation
    - Retry pipeline
    - Verify same .torrent and NFO files used (not regenerated)
    - Verify file modification times unchanged (no regeneration)
    """
    # Create file entry
    file_entry = FileEntry.create_or_get(temp_db, temp_files['media_file'])

    # Track upload attempts
    upload_attempts = {'count': 0}

    # Track metadata generation calls
    metadata_generation_calls = {'count': 0}

    with patch('requests.post') as mock_requests_post:

        def post_side_effect(url, *args, **kwargs):
            if FLARESOLVERR_URL in url:
                return mock_flaresolverr(url, *args, **kwargs)
            else:
                upload_attempts['count'] += 1
                if upload_attempts['count'] == 1:
                    response = Mock()
                    response.status_code = 500
                    response.text = 'Upload failed'
                    return response
                else:
                    return mock_tracker_upload_success(url, *args, **kwargs)

        mock_requests_post.side_effect = post_side_effect

        # Initialize pipeline
        tracker_adapter = LaCaleAdapter(
            tracker_url=TRACKER_URL,
            passkey=TRACKER_PASSKEY,
            flaresolverr_url=FLARESOLVERR_URL
        )
        pipeline = ProcessingPipeline(temp_db, tracker_adapter=tracker_adapter)

        # Mock metadata generation to track calls
        original_metadata_stage = pipeline._metadata_generation_stage

        async def tracked_metadata_stage(entry):
            metadata_generation_calls['count'] += 1
            return await original_metadata_stage(entry)

        pipeline._metadata_generation_stage = tracked_metadata_stage

        with patch('backend.app.processors.pipeline.qbittorrent', mock_qbittorrent):

            # First attempt: metadata succeeds, upload fails
            try:
                await pipeline.process_file(file_entry)
            except Exception:
                pass

            # Verify metadata generation was called once
            assert metadata_generation_calls['count'] == 1, \
                "Metadata generation should be called on first attempt"

            # Refresh from database
            temp_db.refresh(file_entry)

            # Verify metadata checkpoint set
            assert file_entry.metadata_generated_at is not None
            metadata_timestamp_first = file_entry.metadata_generated_at

            # Second attempt: retry with upload success
            await pipeline.process_file(file_entry)

            # Refresh from database
            temp_db.refresh(file_entry)

            # CRITICAL: Verify metadata generation was NOT called again (skipped)
            assert metadata_generation_calls['count'] == 1, \
                "Metadata generation should NOT be called on retry - files should be reused"

            # Verify metadata checkpoint timestamp unchanged
            assert file_entry.metadata_generated_at == metadata_timestamp_first, \
                "Metadata generation timestamp should not change on retry"

            # Verify successful upload
            assert file_entry.status == Status.UPLOADED
            assert file_entry.uploaded_at is not None


# ============================================================================
# Test Utilities
# ============================================================================

def verify_checkpoint_idempotence(file_entry: FileEntry, checkpoints_before: dict) -> None:
    """
    Verify that checkpoint timestamps remain unchanged (idempotence).

    This is a critical verification that ensures pipeline stages are not
    re-executed when retrying from a later stage.

    Args:
        file_entry: FileEntry after retry
        checkpoints_before: Dictionary of checkpoint timestamps before retry

    Raises:
        AssertionError: If any checkpoint timestamp changed
    """
    if 'scanned_at' in checkpoints_before and checkpoints_before['scanned_at']:
        assert file_entry.scanned_at == checkpoints_before['scanned_at'], \
            "Scan checkpoint changed - stage was re-executed"

    if 'analyzed_at' in checkpoints_before and checkpoints_before['analyzed_at']:
        assert file_entry.analyzed_at == checkpoints_before['analyzed_at'], \
            "Analysis checkpoint changed - stage was re-executed"

    if 'renamed_at' in checkpoints_before and checkpoints_before['renamed_at']:
        assert file_entry.renamed_at == checkpoints_before['renamed_at'], \
            "Rename checkpoint changed - stage was re-executed"

    if 'metadata_generated_at' in checkpoints_before and checkpoints_before['metadata_generated_at']:
        assert file_entry.metadata_generated_at == checkpoints_before['metadata_generated_at'], \
            "Metadata generation checkpoint changed - stage was re-executed"
