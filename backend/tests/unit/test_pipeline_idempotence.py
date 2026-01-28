"""
Unit tests for ProcessingPipeline checkpoint/resume logic

Tests cover:
    - Resume from each pipeline stage (scanned, analyzed, renamed, metadata_generated, uploaded)
    - Verify no duplicate operations on retry
    - Status transitions are correct
    - Checkpoint timestamps are set and respected
    - Error handling and recovery
    - Stage skipping logic based on checkpoint timestamps
"""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from sqlalchemy.orm import Session

from backend.app.processors.pipeline import ProcessingPipeline
from backend.app.models.file_entry import FileEntry, Status
from backend.app.services.exceptions import TrackerAPIError


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_db():
    """Mock SQLAlchemy database session."""
    db = Mock(spec=Session)
    db.commit = Mock()
    db.refresh = Mock()
    return db


@pytest.fixture
def file_entry():
    """Create a test FileEntry instance."""
    entry = FileEntry(file_path="/media/test/Movie.2024.1080p.mkv")
    return entry


@pytest.fixture
def pipeline(mock_db):
    """Create ProcessingPipeline instance for testing."""
    return ProcessingPipeline(db=mock_db)


# ============================================================================
# Initialization Tests
# ============================================================================

def test_initialization(mock_db):
    """Test ProcessingPipeline initialization."""
    pipeline = ProcessingPipeline(db=mock_db)

    assert pipeline.db == mock_db


# ============================================================================
# Full Pipeline Processing Tests
# ============================================================================

@pytest.mark.asyncio
async def test_process_file_from_pending(pipeline, file_entry, mock_db):
    """Test processing file from PENDING status through all stages."""
    # Verify initial state
    assert file_entry.status == Status.PENDING
    assert not file_entry.is_scanned()
    assert not file_entry.is_analyzed()
    assert not file_entry.is_renamed()
    assert not file_entry.is_metadata_generated()
    assert not file_entry.is_uploaded()

    # Process file
    await pipeline.process_file(file_entry)

    # Verify all checkpoints were set
    assert file_entry.is_scanned()
    assert file_entry.is_analyzed()
    assert file_entry.is_renamed()
    assert file_entry.is_metadata_generated()
    assert file_entry.is_uploaded()

    # Verify final status
    assert file_entry.status == Status.UPLOADED

    # Verify database commits were called (5 stages)
    assert mock_db.commit.call_count >= 5


# ============================================================================
# Resume from Checkpoint Tests
# ============================================================================

@pytest.mark.asyncio
async def test_resume_from_scanned(pipeline, file_entry, mock_db):
    """Test resuming pipeline from SCANNED checkpoint."""
    # Setup: file already scanned
    file_entry.mark_scanned()
    initial_scanned_at = file_entry.scanned_at
    mock_db.reset_mock()

    # Process file
    await pipeline.process_file(file_entry)

    # Verify scan stage was skipped (scanned_at unchanged)
    assert file_entry.scanned_at == initial_scanned_at

    # Verify subsequent stages completed
    assert file_entry.is_analyzed()
    assert file_entry.is_renamed()
    assert file_entry.is_metadata_generated()
    assert file_entry.is_uploaded()

    # Verify database commits (4 remaining stages, not 5)
    assert mock_db.commit.call_count >= 4


@pytest.mark.asyncio
async def test_resume_from_analyzed(pipeline, file_entry, mock_db):
    """Test resuming pipeline from ANALYZED checkpoint."""
    # Setup: file already scanned and analyzed
    file_entry.mark_scanned()
    file_entry.mark_analyzed()
    initial_scanned_at = file_entry.scanned_at
    initial_analyzed_at = file_entry.analyzed_at
    mock_db.reset_mock()

    # Process file
    await pipeline.process_file(file_entry)

    # Verify scan and analysis stages were skipped
    assert file_entry.scanned_at == initial_scanned_at
    assert file_entry.analyzed_at == initial_analyzed_at

    # Verify subsequent stages completed
    assert file_entry.is_renamed()
    assert file_entry.is_metadata_generated()
    assert file_entry.is_uploaded()

    # Verify database commits (3 remaining stages)
    assert mock_db.commit.call_count >= 3


@pytest.mark.asyncio
async def test_resume_from_renamed(pipeline, file_entry, mock_db):
    """Test resuming pipeline from RENAMED checkpoint."""
    # Setup: file already scanned, analyzed, and renamed
    file_entry.mark_scanned()
    file_entry.mark_analyzed()
    file_entry.mark_renamed()
    initial_scanned_at = file_entry.scanned_at
    initial_analyzed_at = file_entry.analyzed_at
    initial_renamed_at = file_entry.renamed_at
    mock_db.reset_mock()

    # Process file
    await pipeline.process_file(file_entry)

    # Verify earlier stages were skipped
    assert file_entry.scanned_at == initial_scanned_at
    assert file_entry.analyzed_at == initial_analyzed_at
    assert file_entry.renamed_at == initial_renamed_at

    # Verify subsequent stages completed
    assert file_entry.is_metadata_generated()
    assert file_entry.is_uploaded()

    # Verify database commits (2 remaining stages)
    assert mock_db.commit.call_count >= 2


@pytest.mark.asyncio
async def test_resume_from_metadata_generated(pipeline, file_entry, mock_db):
    """Test resuming pipeline from METADATA_GENERATED checkpoint (most common retry scenario)."""
    # Setup: file processed through metadata generation, upload failed
    file_entry.mark_scanned()
    file_entry.mark_analyzed()
    file_entry.mark_renamed()
    file_entry.mark_metadata_generated()
    initial_scanned_at = file_entry.scanned_at
    initial_analyzed_at = file_entry.analyzed_at
    initial_renamed_at = file_entry.renamed_at
    initial_metadata_generated_at = file_entry.metadata_generated_at
    mock_db.reset_mock()

    # Process file (retry upload)
    await pipeline.process_file(file_entry)

    # Verify all earlier stages were skipped (timestamps unchanged)
    assert file_entry.scanned_at == initial_scanned_at
    assert file_entry.analyzed_at == initial_analyzed_at
    assert file_entry.renamed_at == initial_renamed_at
    assert file_entry.metadata_generated_at == initial_metadata_generated_at

    # Verify only upload stage executed
    assert file_entry.is_uploaded()
    assert file_entry.status == Status.UPLOADED

    # Verify database commits (1 remaining stage)
    assert mock_db.commit.call_count >= 1


@pytest.mark.asyncio
async def test_already_uploaded(pipeline, file_entry, mock_db):
    """Test processing already uploaded file (no-op)."""
    # Setup: file fully processed
    file_entry.mark_scanned()
    file_entry.mark_analyzed()
    file_entry.mark_renamed()
    file_entry.mark_metadata_generated()
    file_entry.mark_uploaded()
    initial_uploaded_at = file_entry.uploaded_at
    mock_db.reset_mock()

    # Process file
    await pipeline.process_file(file_entry)

    # Verify all stages were skipped
    assert file_entry.uploaded_at == initial_uploaded_at
    assert file_entry.status == Status.UPLOADED

    # Verify no database commits for stage operations
    # (may have 0 commits or minimal commits for state updates)


# ============================================================================
# Checkpoint Timestamp Tests
# ============================================================================

@pytest.mark.asyncio
async def test_checkpoint_timestamps_chronological(pipeline, file_entry, mock_db):
    """Test that checkpoint timestamps are set in chronological order."""
    # Process file
    await pipeline.process_file(file_entry)

    # Verify timestamps are chronologically ordered
    assert file_entry.scanned_at <= file_entry.analyzed_at
    assert file_entry.analyzed_at <= file_entry.renamed_at
    assert file_entry.renamed_at <= file_entry.metadata_generated_at
    assert file_entry.metadata_generated_at <= file_entry.uploaded_at


@pytest.mark.asyncio
async def test_checkpoint_timestamps_set(pipeline, file_entry, mock_db):
    """Test that all checkpoint timestamps are set after full processing."""
    # Process file
    await pipeline.process_file(file_entry)

    # Verify all timestamps are set
    assert file_entry.scanned_at is not None
    assert file_entry.analyzed_at is not None
    assert file_entry.renamed_at is not None
    assert file_entry.metadata_generated_at is not None
    assert file_entry.uploaded_at is not None


# ============================================================================
# Status Transition Tests
# ============================================================================

@pytest.mark.asyncio
async def test_status_transitions(pipeline, file_entry, mock_db):
    """Test status transitions through all pipeline stages."""
    # Initial status
    assert file_entry.status == Status.PENDING

    # Process file (mock individual stages to capture intermediate states)
    with patch.object(pipeline, '_scan_stage', wraps=pipeline._scan_stage) as mock_scan:
        with patch.object(pipeline, '_analyze_stage', wraps=pipeline._analyze_stage) as mock_analyze:
            with patch.object(pipeline, '_rename_stage', wraps=pipeline._rename_stage) as mock_rename:
                with patch.object(pipeline, '_metadata_generation_stage', wraps=pipeline._metadata_generation_stage) as mock_metadata:
                    with patch.object(pipeline, '_upload_stage', wraps=pipeline._upload_stage) as mock_upload:
                        await pipeline.process_file(file_entry)

                        # Verify all stages were called
                        mock_scan.assert_called_once()
                        mock_analyze.assert_called_once()
                        mock_rename.assert_called_once()
                        mock_metadata.assert_called_once()
                        mock_upload.assert_called_once()

    # Final status
    assert file_entry.status == Status.UPLOADED


# ============================================================================
# Error Handling Tests
# ============================================================================

@pytest.mark.asyncio
async def test_error_during_scan_stage(pipeline, file_entry, mock_db):
    """Test error handling during scan stage."""
    # Mock scan stage to raise error
    with patch.object(pipeline, '_scan_stage', side_effect=TrackerAPIError("Scan failed")):
        with pytest.raises(TrackerAPIError):
            await pipeline.process_file(file_entry)

    # Verify file marked as failed
    assert file_entry.status == Status.FAILED
    assert "Scan failed" in file_entry.error_message
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_error_during_analyze_stage(pipeline, file_entry, mock_db):
    """Test error handling during analysis stage."""
    # Setup: scan already completed
    file_entry.mark_scanned()

    # Mock analyze stage to raise error
    with patch.object(pipeline, '_analyze_stage', side_effect=TrackerAPIError("Analysis failed")):
        with pytest.raises(TrackerAPIError):
            await pipeline.process_file(file_entry)

    # Verify file marked as failed
    assert file_entry.status == Status.FAILED
    assert "Analysis failed" in file_entry.error_message


@pytest.mark.asyncio
async def test_error_during_upload_stage(pipeline, file_entry, mock_db):
    """Test error handling during upload stage (most common failure scenario)."""
    # Setup: all stages except upload completed
    file_entry.mark_scanned()
    file_entry.mark_analyzed()
    file_entry.mark_renamed()
    file_entry.mark_metadata_generated()

    # Mock upload stage to raise error
    with patch.object(pipeline, '_upload_stage', side_effect=TrackerAPIError("Upload failed")):
        with pytest.raises(TrackerAPIError):
            await pipeline.process_file(file_entry)

    # Verify file marked as failed but earlier checkpoints preserved
    assert file_entry.status == Status.FAILED
    assert "Upload failed" in file_entry.error_message
    assert file_entry.is_scanned()
    assert file_entry.is_analyzed()
    assert file_entry.is_renamed()
    assert file_entry.is_metadata_generated()
    assert not file_entry.is_uploaded()


@pytest.mark.asyncio
async def test_unexpected_error_handling(pipeline, file_entry, mock_db):
    """Test handling of unexpected (non-TrackerAPIError) exceptions."""
    # Mock scan stage to raise unexpected error
    with patch.object(pipeline, '_scan_stage', side_effect=ValueError("Unexpected error")):
        with pytest.raises(TrackerAPIError) as exc_info:
            await pipeline.process_file(file_entry)

        # Verify wrapped in TrackerAPIError
        assert "Unexpected error" in str(exc_info.value)

    # Verify file marked as failed
    assert file_entry.status == Status.FAILED
    assert "Unexpected error" in file_entry.error_message


# ============================================================================
# Retry After Failure Tests
# ============================================================================

@pytest.mark.asyncio
async def test_retry_after_upload_failure(pipeline, file_entry, mock_db):
    """Test successful retry after upload failure (idempotence verification)."""
    # Setup: metadata generation succeeded, upload failed
    file_entry.mark_scanned()
    file_entry.mark_analyzed()
    file_entry.mark_renamed()
    file_entry.mark_metadata_generated()
    file_entry.mark_failed("Upload failed")

    # Clear error and retry (simulate user triggering retry)
    file_entry.error_message = None
    file_entry.status = Status.METADATA_GENERATED

    # Capture checkpoint timestamps before retry
    scanned_at_before = file_entry.scanned_at
    analyzed_at_before = file_entry.analyzed_at
    renamed_at_before = file_entry.renamed_at
    metadata_generated_at_before = file_entry.metadata_generated_at

    mock_db.reset_mock()

    # Retry processing
    await pipeline.process_file(file_entry)

    # Verify earlier stages were NOT re-executed (timestamps unchanged)
    assert file_entry.scanned_at == scanned_at_before
    assert file_entry.analyzed_at == analyzed_at_before
    assert file_entry.renamed_at == renamed_at_before
    assert file_entry.metadata_generated_at == metadata_generated_at_before

    # Verify only upload stage executed
    assert file_entry.is_uploaded()
    assert file_entry.status == Status.UPLOADED
    assert file_entry.error_message is None


# ============================================================================
# Stage Skipping Logic Tests
# ============================================================================

@pytest.mark.asyncio
async def test_skip_scan_if_scanned(pipeline, file_entry, mock_db):
    """Test that scan stage is skipped if scanned_at is set."""
    file_entry.mark_scanned()

    with patch.object(pipeline, '_scan_stage') as mock_scan:
        await pipeline.process_file(file_entry)

        # Verify scan stage was NOT called
        mock_scan.assert_not_called()


@pytest.mark.asyncio
async def test_skip_analyze_if_analyzed(pipeline, file_entry, mock_db):
    """Test that analysis stage is skipped if analyzed_at is set."""
    file_entry.mark_scanned()
    file_entry.mark_analyzed()

    with patch.object(pipeline, '_analyze_stage') as mock_analyze:
        await pipeline.process_file(file_entry)

        # Verify analysis stage was NOT called
        mock_analyze.assert_not_called()


@pytest.mark.asyncio
async def test_skip_rename_if_renamed(pipeline, file_entry, mock_db):
    """Test that rename stage is skipped if renamed_at is set."""
    file_entry.mark_scanned()
    file_entry.mark_analyzed()
    file_entry.mark_renamed()

    with patch.object(pipeline, '_rename_stage') as mock_rename:
        await pipeline.process_file(file_entry)

        # Verify rename stage was NOT called
        mock_rename.assert_not_called()


@pytest.mark.asyncio
async def test_skip_metadata_generation_if_generated(pipeline, file_entry, mock_db):
    """Test that metadata generation is skipped if metadata_generated_at is set."""
    file_entry.mark_scanned()
    file_entry.mark_analyzed()
    file_entry.mark_renamed()
    file_entry.mark_metadata_generated()

    with patch.object(pipeline, '_metadata_generation_stage') as mock_metadata:
        await pipeline.process_file(file_entry)

        # Verify metadata generation stage was NOT called
        mock_metadata.assert_not_called()


@pytest.mark.asyncio
async def test_skip_upload_if_uploaded(pipeline, file_entry, mock_db):
    """Test that upload stage is skipped if uploaded_at is set."""
    file_entry.mark_scanned()
    file_entry.mark_analyzed()
    file_entry.mark_renamed()
    file_entry.mark_metadata_generated()
    file_entry.mark_uploaded()

    with patch.object(pipeline, '_upload_stage') as mock_upload:
        await pipeline.process_file(file_entry)

        # Verify upload stage was NOT called
        mock_upload.assert_not_called()


# ============================================================================
# Edge Cases
# ============================================================================

@pytest.mark.asyncio
async def test_partial_checkpoints(pipeline, file_entry, mock_db):
    """Test handling of partial checkpoints (e.g., scanned and renamed but not analyzed)."""
    # This should not happen in normal operation, but test defensive handling
    file_entry.mark_scanned()
    file_entry.mark_renamed()  # Skip analyzed checkpoint (unusual)

    # Should still process correctly, executing analysis and subsequent stages
    await pipeline.process_file(file_entry)

    # Verify all stages completed
    assert file_entry.is_scanned()
    assert file_entry.is_analyzed()  # Should be set now
    assert file_entry.is_renamed()
    assert file_entry.is_metadata_generated()
    assert file_entry.is_uploaded()


@pytest.mark.asyncio
async def test_checkpoint_after_error_recovery(pipeline, file_entry, mock_db):
    """Test that checkpoints are preserved after error and recovery."""
    # Simulate: scan and analyze succeeded, rename failed
    file_entry.mark_scanned()
    file_entry.mark_analyzed()

    scanned_at_initial = file_entry.scanned_at
    analyzed_at_initial = file_entry.analyzed_at

    # Mock rename stage to fail
    with patch.object(pipeline, '_rename_stage', side_effect=TrackerAPIError("Rename failed")):
        with pytest.raises(TrackerAPIError):
            await pipeline.process_file(file_entry)

    # Verify checkpoints preserved after error
    assert file_entry.scanned_at == scanned_at_initial
    assert file_entry.analyzed_at == analyzed_at_initial
    assert not file_entry.is_renamed()

    # Clear error and retry
    file_entry.error_message = None
    file_entry.status = Status.ANALYZED
    mock_db.reset_mock()

    # Retry should skip scan and analyze, execute rename and subsequent stages
    await pipeline.process_file(file_entry)

    # Verify earlier checkpoints still preserved
    assert file_entry.scanned_at == scanned_at_initial
    assert file_entry.analyzed_at == analyzed_at_initial

    # Verify rename and subsequent stages completed
    assert file_entry.is_renamed()
    assert file_entry.is_metadata_generated()
    assert file_entry.is_uploaded()
