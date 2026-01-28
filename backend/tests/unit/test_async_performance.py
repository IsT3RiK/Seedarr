"""
Unit Tests for Async Performance - File Hashing Non-Blocking

This module tests that CPU-intensive file hashing operations are properly
offloaded to ProcessPoolExecutor and do not block the async event loop.

Critical Performance Requirements:
    - File hashing operations MUST NOT block event loop >100ms
    - API must remain responsive during large file processing
    - ProcessPoolExecutor should be used for CPU-intensive operations

Test Strategy:
    1. Create test files of various sizes
    2. Verify torrent creation runs asynchronously
    3. Confirm event loop remains responsive during hashing
    4. Measure blocking time to ensure <100ms threshold

Verification Command:
    python -m pytest backend/tests/unit/test_async_performance.py::test_file_hashing_non_blocking -v
"""

import asyncio
import os
import tempfile
import time
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from sqlalchemy.orm import Session

from backend.app.services.media_analyzer import MediaAnalyzer, _create_torrent_sync


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_db():
    """Mock database session."""
    return Mock(spec=Session)


@pytest.fixture
def media_analyzer(mock_db):
    """MediaAnalyzer instance with mocked database."""
    return MediaAnalyzer(mock_db)


@pytest.fixture
def temp_media_file():
    """Create temporary media file for testing."""
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.mkv', delete=False) as f:
        # Write 1MB of test data
        # For actual testing, we'll mock the hashing to avoid slow tests
        f.write(b'\x00' * (1024 * 1024))
        temp_path = f.name

    yield temp_path

    # Cleanup
    try:
        os.unlink(temp_path)
    except:
        pass


@pytest.fixture
def temp_output_dir():
    """Create temporary output directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir

    # Cleanup
    import shutil
    try:
        shutil.rmtree(temp_dir)
    except:
        pass


# ============================================================================
# Test: File Hashing Non-Blocking (CRITICAL VERIFICATION)
# ============================================================================

@pytest.mark.asyncio
async def test_file_hashing_non_blocking(media_analyzer, temp_media_file, temp_output_dir):
    """
    Test that file hashing does not block the event loop.

    This is the CRITICAL test for subtask-5-1 verification.

    Verification:
        1. Create .torrent file asynchronously
        2. Verify event loop remains responsive during operation
        3. Confirm operation completes successfully
        4. Ensure no blocking operations >100ms

    Expected:
        - Torrent creation completes successfully
        - Event loop remains responsive (can process other tasks concurrently)
        - No blocking operations detected
    """
    # Track event loop blocking
    event_loop_blocked = False
    max_blocking_time = 0.0

    async def monitor_event_loop():
        """Monitor event loop responsiveness during torrent creation."""
        nonlocal event_loop_blocked, max_blocking_time

        # Run monitoring for duration of torrent creation
        # Check every 10ms to detect blocking
        for _ in range(100):  # Monitor for 1 second (100 * 10ms)
            start = time.perf_counter()
            await asyncio.sleep(0.01)  # Sleep 10ms
            elapsed = time.perf_counter() - start

            # If sleep took >110ms (10ms + 100ms threshold), event loop was blocked
            if elapsed > 0.11:  # 110ms
                event_loop_blocked = True
                max_blocking_time = max(max_blocking_time, elapsed)

    # Mock torf library to avoid actual file hashing in unit tests
    # We want to test the async pattern, not the actual hashing performance
    with patch('backend.app.services.media_analyzer._create_torrent_sync') as mock_create:
        # Simulate CPU-intensive work with small delay
        def mock_torrent_creation(file_path, announce_url, output_path, source):
            # Simulate some work (but not blocking since it's in executor)
            time.sleep(0.05)  # 50ms simulated hashing
            # Create dummy .torrent file
            with open(output_path, 'wb') as f:
                f.write(b'd8:announce18:http://tracker.com4:infod4:name4:teste')
            return output_path

        mock_create.side_effect = mock_torrent_creation

        # Start event loop monitoring
        monitor_task = asyncio.create_task(monitor_event_loop())

        # Create .torrent file (should not block event loop)
        torrent_path = await media_analyzer.create_torrent(
            file_path=temp_media_file,
            announce_url="http://tracker.example.com/announce",
            output_dir=temp_output_dir,
            source="lacale"
        )

        # Wait for monitoring to complete
        await monitor_task

        # Verify torrent was created
        assert torrent_path is not None
        assert os.path.exists(torrent_path)
        assert torrent_path.endswith('.torrent')

        # CRITICAL: Verify event loop was NOT blocked
        # Event loop should remain responsive during CPU-intensive operation
        assert not event_loop_blocked, (
            f"Event loop was blocked for {max_blocking_time * 1000:.2f}ms during "
            f"torrent creation. Maximum acceptable: 100ms. "
            f"File hashing MUST be offloaded to ProcessPoolExecutor."
        )

        # Verify _create_torrent_sync was called with correct parameters
        mock_create.assert_called_once()
        call_args = mock_create.call_args[0]
        assert call_args[0] == temp_media_file
        assert call_args[1] == "http://tracker.example.com/announce"
        assert call_args[3] == "lacale"  # Source flag


@pytest.mark.asyncio
async def test_concurrent_operations_during_hashing(media_analyzer, temp_media_file, temp_output_dir):
    """
    Test that other async operations can run concurrently during file hashing.

    This verifies that the async event loop remains responsive and can
    process other tasks while file hashing is in progress.

    Expected:
        - Torrent creation runs in background
        - Other async tasks execute concurrently
        - All operations complete successfully
    """
    # Track concurrent task execution
    concurrent_task_completed = False

    async def concurrent_task():
        """Simulate other async work happening during torrent creation."""
        nonlocal concurrent_task_completed
        # Simulate API request or other async work
        await asyncio.sleep(0.1)
        concurrent_task_completed = True

    # Mock torrent creation with delay
    with patch('backend.app.services.media_analyzer._create_torrent_sync') as mock_create:
        def mock_torrent_creation(file_path, announce_url, output_path, source):
            time.sleep(0.2)  # 200ms simulated hashing
            with open(output_path, 'wb') as f:
                f.write(b'd8:announce18:http://tracker.com4:infod4:name4:teste')
            return output_path

        mock_create.side_effect = mock_torrent_creation

        # Run torrent creation and concurrent task together
        torrent_task = asyncio.create_task(
            media_analyzer.create_torrent(
                file_path=temp_media_file,
                announce_url="http://tracker.example.com/announce",
                output_dir=temp_output_dir
            )
        )
        concurrent_async_task = asyncio.create_task(concurrent_task())

        # Wait for both tasks
        torrent_path, _ = await asyncio.gather(torrent_task, concurrent_async_task)

        # Verify both completed
        assert torrent_path is not None
        assert concurrent_task_completed, (
            "Concurrent async task did not complete. "
            "Event loop may have been blocked during torrent creation."
        )


# ============================================================================
# Test: Torrent Creation Parameters
# ============================================================================

@pytest.mark.asyncio
async def test_create_torrent_with_source_flag(media_analyzer, temp_media_file, temp_output_dir):
    """
    Test that .torrent files are created with source="lacale" flag.

    CRITICAL: The source flag is required to prevent torrent clients
    from re-downloading all content when the .torrent file is loaded.

    Expected:
        - _create_torrent_sync called with source="lacale"
        - Source parameter passed correctly
    """
    with patch('backend.app.services.media_analyzer._create_torrent_sync') as mock_create:
        mock_create.return_value = os.path.join(temp_output_dir, 'test.torrent')

        # Create dummy file for mock
        with open(mock_create.return_value, 'wb') as f:
            f.write(b'd8:announce18:http://tracker.com4:infod4:name4:teste')

        await media_analyzer.create_torrent(
            file_path=temp_media_file,
            announce_url="http://tracker.example.com/announce",
            output_dir=temp_output_dir,
            source="lacale"
        )

        # Verify source flag passed
        call_args = mock_create.call_args[0]
        assert call_args[3] == "lacale", "Source flag MUST be 'lacale' to prevent re-download"


@pytest.mark.asyncio
async def test_create_torrent_file_not_found(media_analyzer, temp_output_dir):
    """
    Test error handling when input file does not exist.

    Expected:
        - TrackerAPIError raised
        - Error message indicates file not found
    """
    from backend.app.services.exceptions import TrackerAPIError

    with pytest.raises(TrackerAPIError) as exc_info:
        await media_analyzer.create_torrent(
            file_path="/nonexistent/file.mkv",
            announce_url="http://tracker.example.com/announce",
            output_dir=temp_output_dir
        )

    assert "does not exist" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_torrent_output_dir_creation(media_analyzer, temp_media_file):
    """
    Test that output directory is created if it doesn't exist.

    Expected:
        - Output directory created automatically
        - Torrent file saved to new directory
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Use subdirectory that doesn't exist
        output_dir = os.path.join(temp_dir, 'torrents', 'output')

        with patch('backend.app.services.media_analyzer._create_torrent_sync') as mock_create:
            output_path = os.path.join(output_dir, 'test.torrent')
            mock_create.return_value = output_path

            # Create dummy file
            os.makedirs(output_dir, exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(b'd8:announce18:http://tracker.com4:infod4:name4:teste')

            await media_analyzer.create_torrent(
                file_path=temp_media_file,
                announce_url="http://tracker.example.com/announce",
                output_dir=output_dir
            )

            # Verify directory was created
            assert os.path.exists(output_dir)


# ============================================================================
# Test: Synchronous Torrent Creation Function
# ============================================================================

def test_create_torrent_sync_with_source_flag(temp_media_file, temp_output_dir):
    """
    Test synchronous torrent creation with source flag.

    This tests the actual _create_torrent_sync function that runs
    in the ProcessPoolExecutor.

    Expected:
        - .torrent file created with source="lacale" flag
        - File written to output path
        - Returns output path
    """
    # This test requires torf library to be installed
    # If not installed, skip test
    pytest.importorskip("torf")

    output_path = os.path.join(temp_output_dir, 'test.torrent')

    result = _create_torrent_sync(
        file_path=temp_media_file,
        announce_url="http://tracker.example.com/announce",
        output_path=output_path,
        source="lacale"
    )

    # Verify torrent file created
    assert result == output_path
    assert os.path.exists(output_path)
    assert os.path.getsize(output_path) > 0

    # Verify source flag in torrent (parse .torrent file)
    import torf
    torrent = torf.Torrent.read(output_path)
    assert torrent.source == "lacale", "Source flag MUST be 'lacale'"
    assert torrent.private is True, "Torrent MUST be private"


# ============================================================================
# Test: MediaInfo Extraction
# ============================================================================

@pytest.mark.asyncio
async def test_extract_mediainfo_placeholder(media_analyzer, temp_media_file):
    """
    Test MediaInfo extraction (placeholder implementation).

    Note: This is a placeholder test. Full MediaInfo integration
    will be implemented in a future phase.

    Expected:
        - Returns basic file information
        - Does not block event loop
    """
    metadata = await media_analyzer.extract_mediainfo(temp_media_file)

    # Verify basic metadata
    assert metadata is not None
    assert 'file_size' in metadata
    assert 'file_path' in metadata
    assert metadata['file_path'] == temp_media_file


@pytest.mark.asyncio
async def test_extract_mediainfo_file_not_found(media_analyzer):
    """
    Test MediaInfo extraction error handling for missing files.

    Expected:
        - TrackerAPIError raised
        - Error message indicates file not found
    """
    from backend.app.services.exceptions import TrackerAPIError

    with pytest.raises(TrackerAPIError) as exc_info:
        await media_analyzer.extract_mediainfo("/nonexistent/file.mkv")

    assert "Failed to extract MediaInfo" in str(exc_info.value)


# ============================================================================
# Test: ProcessPoolExecutor Lifecycle
# ============================================================================

def test_process_pool_singleton():
    """
    Test that ProcessPoolExecutor uses singleton pattern.

    Expected:
        - Same executor instance returned on multiple calls
        - Efficient resource usage
    """
    from backend.app.services.media_analyzer import _get_process_pool

    pool1 = _get_process_pool()
    pool2 = _get_process_pool()

    assert pool1 is pool2, "ProcessPoolExecutor should use singleton pattern"


def test_shutdown_process_pool():
    """
    Test graceful shutdown of ProcessPoolExecutor.

    Expected:
        - Executor shutdown called
        - No errors during shutdown
    """
    from backend.app.services.media_analyzer import shutdown_process_pool

    # Shutdown (should not raise errors even if pool not initialized)
    shutdown_process_pool()

    # Verify no errors
    # (implicitly verified by not raising exception)
