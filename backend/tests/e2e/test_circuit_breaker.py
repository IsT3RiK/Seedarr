"""
End-to-End test for FlareSolverr circuit breaker

This test verifies the circuit breaker pattern for FlareSolverr failures:
    1. Stop FlareSolverr service (simulate connection failure)
    2. Attempt upload 3 times
    3. Verify circuit breaker opens
    4. Check logs show circuit open state
    5. Restart FlareSolverr (restore connection)
    6. Wait for health check
    7. Verify circuit closes and upload succeeds

This is a critical E2E test that validates the circuit breaker pattern
provides graceful degradation when FlareSolverr is unavailable and
automatic recovery when service is restored.

Environment Variables:
    - USE_REAL_SERVICES: Set to 'true' to test against real services
    - FLARESOLVERR_URL: FlareSolverr service URL (default: http://localhost:8191)
    - TRACKER_URL: La Cale tracker URL
    - TRACKER_PASSKEY: Tracker passkey
"""

import asyncio
import os
import pytest
import tempfile
import time
from unittest.mock import Mock, patch, AsyncMock, MagicMock, call
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import requests

from backend.app.models.base import Base
from backend.app.models.file_entry import FileEntry, Status
from backend.app.models.settings import Settings
from backend.app.models.tmdb_cache import TMDBCache
from backend.app.models.tags import Tags
from backend.app.processors.pipeline import ProcessingPipeline
from backend.app.services.media_analyzer import MediaAnalyzer
from backend.app.services.nfo_validator import NFOValidator
from backend.app.services.cloudflare_session_manager import CloudflareSessionManager, CircuitBreakerState
from backend.app.services.lacale_client import LaCaleClient
from backend.app.adapters.lacale_adapter import LaCaleAdapter
from backend.app.services.exceptions import TrackerAPIError, CloudflareBypassError, NetworkRetryableError


# ============================================================================
# Test Configuration
# ============================================================================

USE_REAL_SERVICES = os.getenv('USE_REAL_SERVICES', 'false').lower() == 'true'
FLARESOLVERR_URL = os.getenv('FLARESOLVERR_URL', 'http://localhost:8191')
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
        qbittorrent_host='localhost:8080',
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
def cloudflare_session_manager():
    """Create CloudflareSessionManager instance."""
    return CloudflareSessionManager(
        flaresolverr_url=FLARESOLVERR_URL,
        max_timeout=60000
    )


@pytest.fixture
def lacale_adapter(cloudflare_session_manager):
    """Create LaCaleAdapter instance."""
    return LaCaleAdapter(
        tracker_url=TRACKER_URL,
        passkey=TRACKER_PASSKEY,
        cloudflare_session_manager=cloudflare_session_manager
    )


# ============================================================================
# E2E Tests
# ============================================================================

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_failures(cloudflare_session_manager):
    """
    Test 1: Circuit breaker opens after 3 consecutive failures

    Verification steps:
        1. Stop FlareSolverr service (mock connection error)
        2. Attempt authentication 3 times
        3. Verify circuit breaker opens
        4. Verify 4th attempt fails fast without calling FlareSolverr
        5. Check logs show circuit open state
    """
    # Mock FlareSolverr connection failure
    with patch('requests.post') as mock_post:
        # Configure mock to raise ConnectionError (FlareSolverr unavailable)
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Failed to connect to FlareSolverr"
        )

        # Attempt 1: Should fail and record failure
        with pytest.raises(NetworkRetryableError) as exc_info:
            await cloudflare_session_manager.get_session(TRACKER_URL)
        assert "Failed to connect to FlareSolverr" in str(exc_info.value)
        assert cloudflare_session_manager.failure_count == 1
        assert cloudflare_session_manager.circuit_state == CircuitBreakerState.CLOSED

        # Attempt 2: Should fail and record failure
        with pytest.raises(NetworkRetryableError):
            await cloudflare_session_manager.get_session(TRACKER_URL)
        assert cloudflare_session_manager.failure_count == 2
        assert cloudflare_session_manager.circuit_state == CircuitBreakerState.CLOSED

        # Attempt 3: Should fail and OPEN circuit
        with pytest.raises(NetworkRetryableError):
            await cloudflare_session_manager.get_session(TRACKER_URL)
        assert cloudflare_session_manager.failure_count == 3
        assert cloudflare_session_manager.circuit_state == CircuitBreakerState.OPEN

        # Verify logs show circuit open state
        status = cloudflare_session_manager.get_status()
        assert status['state'] == 'open'
        assert status['failure_count'] == 3
        assert 'circuit_reopens_in_seconds' in status

        # Attempt 4: Should fail fast without calling FlareSolverr
        mock_post.reset_mock()  # Reset call count
        with pytest.raises(CloudflareBypassError) as exc_info:
            await cloudflare_session_manager.get_session(TRACKER_URL)
        assert "Circuit breaker OPEN" in str(exc_info.value)
        assert mock_post.call_count == 0  # FlareSolverr NOT called (fail fast)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_circuit_breaker_closes_after_service_recovery(cloudflare_session_manager):
    """
    Test 2: Circuit breaker closes after FlareSolverr service recovery

    Verification steps:
        1. Open circuit by simulating 3 failures
        2. Wait for circuit timeout (or transition to HALF_OPEN)
        3. Restore FlareSolverr connection (mock successful response)
        4. Perform health check
        5. Verify circuit closes and upload succeeds
    """
    # Step 1: Open circuit with 3 failures
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Failed to connect to FlareSolverr"
        )

        # 3 failed attempts to open circuit
        for _ in range(3):
            try:
                await cloudflare_session_manager.get_session(TRACKER_URL)
            except (NetworkRetryableError, CloudflareBypassError):
                pass

        assert cloudflare_session_manager.circuit_state == CircuitBreakerState.OPEN
        assert cloudflare_session_manager.failure_count == 3

    # Step 2: Wait for circuit timeout (or manually transition to HALF_OPEN)
    # For testing purposes, manually adjust last_failure_time to simulate timeout
    from datetime import datetime, timedelta
    cloudflare_session_manager.last_failure_time = datetime.utcnow() - timedelta(
        seconds=cloudflare_session_manager.CIRCUIT_OPEN_DURATION + 1
    )

    # Step 3: Restore FlareSolverr connection (mock successful response)
    with patch('requests.post') as mock_post, \
         patch('requests.get') as mock_get:

        # Mock successful FlareSolverr response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'solution': {
                'cookies': [
                    {'name': 'cf_clearance', 'value': 'test_token_123'},
                    {'name': 'session_id', 'value': 'session_abc'}
                ]
            }
        }
        mock_post.return_value = mock_response

        # Mock successful health check
        mock_health_response = Mock()
        mock_health_response.status_code = 200
        mock_get.return_value = mock_health_response

        # Step 4: Perform health check
        is_healthy = await cloudflare_session_manager.health_check()
        assert is_healthy is True

        # Step 5: Attempt authentication - circuit should transition to HALF_OPEN, then CLOSED
        session = await cloudflare_session_manager.get_session(TRACKER_URL)

        # Verify circuit closed
        assert cloudflare_session_manager.circuit_state == CircuitBreakerState.CLOSED
        assert cloudflare_session_manager.failure_count == 0
        assert cloudflare_session_manager.last_failure_time is None

        # Verify session has cookies
        assert len(session.cookies) == 2
        assert session.cookies.get('cf_clearance') == 'test_token_123'
        assert session.cookies.get('session_id') == 'session_abc'

        # Verify status shows closed state
        status = cloudflare_session_manager.get_status()
        assert status['state'] == 'closed'
        assert status['failure_count'] == 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_complete_upload_flow_with_circuit_breaker(temp_db, lacale_adapter):
    """
    Test 3: Complete upload flow with circuit breaker pattern

    Verification steps:
        1. Create file entry and start pipeline
        2. Simulate FlareSolverr failure during upload
        3. Verify circuit breaker opens after 3 attempts
        4. Restore FlareSolverr service
        5. Retry upload and verify success
    """
    # Create file entry
    file_entry = FileEntry(
        file_path='/tmp/test_video.mkv',
        status=Status.PENDING
    )
    temp_db.add(file_entry)
    temp_db.commit()

    # Mock all pipeline stages except upload
    with patch('backend.app.processors.pipeline.ProcessingPipeline._scan_stage') as mock_scan, \
         patch('backend.app.processors.pipeline.ProcessingPipeline._analyze_stage') as mock_analyze, \
         patch('backend.app.processors.pipeline.ProcessingPipeline._rename_stage') as mock_rename, \
         patch('backend.app.processors.pipeline.ProcessingPipeline._metadata_generation_stage') as mock_metadata:

        # Configure stage mocks as no-op
        mock_scan.return_value = None
        mock_analyze.return_value = None
        mock_rename.return_value = None
        mock_metadata.return_value = None

        # Step 1: Simulate FlareSolverr failure during upload
        with patch('requests.post') as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError(
                "Failed to connect to FlareSolverr"
            )

            # Create pipeline with adapter
            pipeline = ProcessingPipeline(
                db=temp_db,
                tracker_adapter=lacale_adapter
            )

            # Attempt pipeline processing - should fail at upload stage
            with pytest.raises(TrackerAPIError) as exc_info:
                await pipeline.process_file(file_entry)

            # Verify failure recorded
            assert file_entry.status == Status.FAILED
            assert "Failed to connect to FlareSolverr" in file_entry.error_message

        # Step 2: Reset file entry for retry and verify circuit is open
        file_entry.status = Status.METADATA_GENERATED  # Resume from upload stage
        file_entry.error_message = None
        temp_db.commit()

        # Verify circuit breaker opened
        cloudflare_manager = lacale_adapter.cloudflare_session_manager
        assert cloudflare_manager.circuit_state == CircuitBreakerState.OPEN

        # Step 3: Restore FlareSolverr service
        # Manually close circuit or wait for timeout
        cloudflare_manager.reset_circuit_breaker()
        assert cloudflare_manager.circuit_state == CircuitBreakerState.CLOSED

        # Mock successful FlareSolverr and upload
        with patch('requests.post') as mock_post:
            # Mock successful FlareSolverr response
            flaresolverr_response = Mock()
            flaresolverr_response.status_code = 200
            flaresolverr_response.json.return_value = {
                'solution': {
                    'cookies': [
                        {'name': 'cf_clearance', 'value': 'test_token_123'},
                        {'name': 'session_id', 'value': 'session_abc'}
                    ]
                }
            }

            # Mock successful upload response
            upload_response = Mock()
            upload_response.status_code = 200
            upload_response.json.return_value = {
                'success': True,
                'torrent_id': 12345,
                'download_url': f'{TRACKER_URL}/download.php?id=12345'
            }

            # Configure mock to return different responses for different calls
            mock_post.side_effect = [flaresolverr_response, upload_response]

            # Mock qBittorrent injection
            with patch('backend.app.processors.pipeline.ProcessingPipeline._inject_to_qbittorrent') as mock_qbit:
                mock_qbit.return_value = None

                # Step 4: Retry pipeline - should succeed
                await pipeline.process_file(file_entry)

                # Verify success
                assert file_entry.status == Status.UPLOADED
                assert file_entry.error_message is None
                assert file_entry.uploaded_at is not None

                # Verify circuit remains closed
                assert cloudflare_manager.circuit_state == CircuitBreakerState.CLOSED
                assert cloudflare_manager.failure_count == 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_circuit_breaker_half_open_failure(cloudflare_session_manager):
    """
    Test 4: Circuit breaker reopens if HALF_OPEN request fails

    Verification steps:
        1. Open circuit with 3 failures
        2. Wait for timeout (transition to HALF_OPEN)
        3. Attempt request that fails
        4. Verify circuit reopens (back to OPEN)
    """
    # Step 1: Open circuit with 3 failures
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Failed to connect to FlareSolverr"
        )

        # 3 failed attempts to open circuit
        for _ in range(3):
            try:
                await cloudflare_session_manager.get_session(TRACKER_URL)
            except (NetworkRetryableError, CloudflareBypassError):
                pass

        assert cloudflare_session_manager.circuit_state == CircuitBreakerState.OPEN

    # Step 2: Wait for timeout (manually adjust for testing)
    from datetime import datetime, timedelta
    cloudflare_session_manager.last_failure_time = datetime.utcnow() - timedelta(
        seconds=cloudflare_session_manager.CIRCUIT_OPEN_DURATION + 1
    )

    # Step 3: Attempt request that fails in HALF_OPEN state
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Still failing"
        )

        # This should transition to HALF_OPEN, then back to OPEN on failure
        with pytest.raises(NetworkRetryableError):
            await cloudflare_session_manager.get_session(TRACKER_URL)

        # Verify circuit reopened
        assert cloudflare_session_manager.circuit_state == CircuitBreakerState.OPEN
        assert cloudflare_session_manager.failure_count == 4  # Incremented again


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_circuit_breaker_status_reporting(cloudflare_session_manager):
    """
    Test 5: Circuit breaker status reporting

    Verification steps:
        1. Get status with CLOSED circuit
        2. Open circuit
        3. Get status with OPEN circuit
        4. Verify status includes failure count and timing info
    """
    # Step 1: Get status with CLOSED circuit
    status = cloudflare_session_manager.get_status()
    assert status['state'] == 'closed'
    assert status['failure_count'] == 0
    assert status['max_failures'] == 3
    assert status['last_failure_time'] is None
    assert status['flaresolverr_url'] == FLARESOLVERR_URL
    assert status['max_timeout_ms'] == 60000
    assert 'circuit_reopens_in_seconds' not in status

    # Step 2: Open circuit with 3 failures
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Failed to connect"
        )

        for _ in range(3):
            try:
                await cloudflare_session_manager.get_session(TRACKER_URL)
            except (NetworkRetryableError, CloudflareBypassError):
                pass

    # Step 3: Get status with OPEN circuit
    status = cloudflare_session_manager.get_status()
    assert status['state'] == 'open'
    assert status['failure_count'] == 3
    assert status['last_failure_time'] is not None
    assert 'circuit_reopens_in_seconds' in status
    assert status['circuit_reopens_in_seconds'] >= 0
    assert status['circuit_reopens_in_seconds'] <= cloudflare_session_manager.CIRCUIT_OPEN_DURATION


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_circuit_breaker_manual_reset(cloudflare_session_manager):
    """
    Test 6: Manual circuit breaker reset

    Verification steps:
        1. Open circuit with failures
        2. Perform health check
        3. Manually reset circuit
        4. Verify circuit closed and failures cleared
    """
    # Step 1: Open circuit with 3 failures
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Failed to connect"
        )

        for _ in range(3):
            try:
                await cloudflare_session_manager.get_session(TRACKER_URL)
            except (NetworkRetryableError, CloudflareBypassError):
                pass

        assert cloudflare_session_manager.circuit_state == CircuitBreakerState.OPEN
        assert cloudflare_session_manager.failure_count == 3

    # Step 2: Perform health check
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        is_healthy = await cloudflare_session_manager.health_check()
        assert is_healthy is True

    # Step 3: Manually reset circuit
    cloudflare_session_manager.reset_circuit_breaker()

    # Step 4: Verify circuit closed and failures cleared
    assert cloudflare_session_manager.circuit_state == CircuitBreakerState.CLOSED
    assert cloudflare_session_manager.failure_count == 0
    assert cloudflare_session_manager.last_failure_time is None

    status = cloudflare_session_manager.get_status()
    assert status['state'] == 'closed'
    assert status['failure_count'] == 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_circuit_breaker_concurrent_requests(cloudflare_session_manager):
    """
    Test 7: Circuit breaker with concurrent requests

    Verification steps:
        1. Open circuit with failures
        2. Attempt multiple concurrent requests
        3. Verify all fail fast without calling FlareSolverr
        4. Verify circuit remains open
    """
    # Step 1: Open circuit with 3 failures
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Failed to connect"
        )

        for _ in range(3):
            try:
                await cloudflare_session_manager.get_session(TRACKER_URL)
            except (NetworkRetryableError, CloudflareBypassError):
                pass

        assert cloudflare_session_manager.circuit_state == CircuitBreakerState.OPEN

    # Step 2: Attempt multiple concurrent requests
    with patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Still failing"
        )

        # Launch 5 concurrent requests
        tasks = [
            cloudflare_session_manager.get_session(TRACKER_URL)
            for _ in range(5)
        ]

        # All should fail with CloudflareBypassError (circuit open)
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Verify all failed with circuit breaker error
        for result in results:
            assert isinstance(result, CloudflareBypassError)
            assert "Circuit breaker OPEN" in str(result)

        # Verify FlareSolverr was NOT called (fail fast)
        assert mock_post.call_count == 0

        # Verify circuit remains open
        assert cloudflare_session_manager.circuit_state == CircuitBreakerState.OPEN


# ============================================================================
# Integration with Real Services (Optional)
# ============================================================================

@pytest.mark.e2e
@pytest.mark.skipif(not USE_REAL_SERVICES, reason="Real services testing disabled")
@pytest.mark.asyncio
async def test_circuit_breaker_with_real_flaresolverr():
    """
    Test 8: Circuit breaker with real FlareSolverr service (optional)

    This test requires a real FlareSolverr service running.
    Set USE_REAL_SERVICES=true to enable.

    Verification steps:
        1. Stop FlareSolverr service (manual step)
        2. Verify circuit opens after failures
        3. Start FlareSolverr service (manual step)
        4. Verify circuit closes and requests succeed
    """
    manager = CloudflareSessionManager(
        flaresolverr_url=FLARESOLVERR_URL,
        max_timeout=60000
    )

    print(f"\n=== Testing with real FlareSolverr at {FLARESOLVERR_URL} ===")

    # Test 1: Health check
    is_healthy = await manager.health_check()
    print(f"Health check: {'HEALTHY' if is_healthy else 'UNHEALTHY'}")

    if is_healthy:
        # Test 2: Successful authentication
        session = await manager.get_session(TRACKER_URL)
        assert session is not None
        assert len(session.cookies) > 0
        print(f"Successfully authenticated with {len(session.cookies)} cookies")

        # Test 3: Circuit breaker status
        status = manager.get_status()
        print(f"Circuit state: {status['state']}")
        print(f"Failures: {status['failure_count']}/{status['max_failures']}")
    else:
        print("FlareSolverr service is not available. Test skipped.")
        pytest.skip("FlareSolverr service not available")
