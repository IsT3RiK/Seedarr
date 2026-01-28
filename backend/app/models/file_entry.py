"""
FileEntry Database Model for Seedarr v2.0

This module defines the FileEntry model for tracking files through the processing pipeline.
Each file entry represents a media file being processed and includes checkpoint timestamps
for idempotent pipeline resumption.

Pipeline Stages:
    1. PENDING - File discovered, awaiting scan
    2. SCANNED - File scanned, basic info extracted
    3. ANALYZED - MediaInfo analysis and TMDB validation complete
    4. RENAMED - File renamed according to release format
    5. METADATA_GENERATED - .torrent and NFO files created
    6. UPLOADED - Successfully uploaded to tracker
    7. FAILED - Processing failed (with error details)

Idempotence Strategy:
    - Each stage sets a checkpoint timestamp (e.g., scanned_at, analyzed_at)
    - On retry, pipeline checks timestamps to skip completed stages
    - Prevents duplicate work and allows resumption from failure point

Features:
    - Pipeline checkpoint timestamps for idempotent operations
    - Status tracking through all processing stages
    - Error tracking for failed entries
    - File path and metadata storage
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum as SQLEnum, JSON
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from typing import Optional, List
import enum
import json

from .base import Base


class Status(enum.Enum):
    """
    File processing status enumeration.

    Represents the current stage of a file in the processing pipeline.
    Status transitions are sequential and map to checkpoint timestamps.
    """
    PENDING = "pending"
    SCANNED = "scanned"
    ANALYZED = "analyzed"
    PENDING_APPROVAL = "pending_approval"  # Waiting for user approval
    APPROVED = "approved"  # User approved, ready for preparation
    PREPARING = "preparing"  # Creating hardlinks, screenshots, etc.
    RENAMED = "renamed"
    METADATA_GENERATED = "metadata_generated"
    UPLOADED = "uploaded"
    FAILED = "failed"


class TrackerStatus(enum.Enum):
    """
    Per-tracker upload status enumeration.

    Tracks the upload status for each individual tracker independently,
    allowing partial success scenarios where some trackers succeed while others fail.
    """
    PENDING = "pending"              # Not yet attempted
    SUCCESS = "success"              # Upload succeeded
    FAILED = "failed"                # Upload failed (can be retried)
    SKIPPED_DUPLICATE = "skipped_duplicate"  # Skipped due to duplicate detection
    RETRYING = "retrying"            # Currently being retried


class FileEntry(Base):
    """
    Database model for tracking files through the processing pipeline.

    This model stores file metadata and pipeline checkpoint timestamps to enable
    idempotent processing. If a pipeline stage fails, the file can be retried
    from the last successful checkpoint without repeating completed work.

    Table Structure:
        - id: Primary key (auto-increment)
        - file_path: Original file path (absolute)
        - status: Current pipeline status (enum)
        - error_message: Error details if status is FAILED
        - created_at: Entry creation timestamp
        - updated_at: Last modification timestamp

        Pipeline Checkpoints (timestamps):
        - scanned_at: File scan completion time
        - analyzed_at: MediaInfo analysis completion time
        - renamed_at: File rename completion time
        - metadata_generated_at: .torrent and NFO generation completion time
        - uploaded_at: Tracker upload completion time

    Idempotent Resume Logic:
        - If scanned_at is set, skip scan stage
        - If analyzed_at is set, skip analysis stage
        - If renamed_at is set, skip rename stage
        - If metadata_generated_at is set, skip metadata generation (reuse .torrent/.nfo)
        - If uploaded_at is set, file fully processed

    Example:
        >>> entry = FileEntry(file_path="/media/Movie.2024.1080p.mkv")
        >>> db.add(entry)
        >>> db.commit()
        >>>
        >>> # Mark scan complete
        >>> entry.mark_scanned()
        >>>
        >>> # Check if already analyzed
        >>> if not entry.is_analyzed():
        >>>     # Perform analysis
        >>>     entry.mark_analyzed()
    """

    __tablename__ = 'file_entries'

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # File information
    file_path = Column(String(1000), nullable=False, unique=True)
    status = Column(SQLEnum(Status), nullable=False, default=Status.PENDING)

    # Error tracking
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Pipeline checkpoint timestamps for idempotent operations
    scanned_at = Column(DateTime, nullable=True)
    analyzed_at = Column(DateTime, nullable=True)
    renamed_at = Column(DateTime, nullable=True)
    metadata_generated_at = Column(DateTime, nullable=True)
    uploaded_at = Column(DateTime, nullable=True)

    # ============================================================================
    # Upload metadata fields - populated during pipeline processing
    # ============================================================================

    # Release information
    release_name = Column(String(500), nullable=True)  # Formatted release name (e.g., "Movie.2024.1080p.BluRay.x264-GROUP")

    # Tracker category and tags
    category_id = Column(String(50), nullable=True)  # Tracker category ID (e.g., "1" for Films)
    tag_ids = Column(JSON, nullable=True)  # List of tracker tag IDs (e.g., ["10", "15", "20"])

    # TMDB metadata
    tmdb_id = Column(String(50), nullable=True)  # TMDB ID
    tmdb_type = Column(String(20), nullable=True)  # "movie" or "tv"
    cover_url = Column(String(1000), nullable=True)  # Cover image URL from TMDB
    description = Column(Text, nullable=True)  # Plot/description from TMDB

    # Generated file paths
    torrent_path = Column(String(1000), nullable=True)  # Path to generated .torrent file
    nfo_path = Column(String(1000), nullable=True)  # Path to generated .nfo file

    # MediaInfo data (stored as JSON for flexibility)
    mediainfo_data = Column(JSON, nullable=True)  # Full MediaInfo extraction

    # Upload result (legacy - single tracker)
    tracker_torrent_id = Column(String(100), nullable=True)  # Torrent ID returned by tracker
    tracker_torrent_url = Column(String(500), nullable=True)  # Torrent URL on tracker

    # Multi-tracker support
    torrent_paths = Column(JSON, nullable=True)  # {"lacale": "/path/to.torrent", "c411": "..."}
    upload_results = Column(JSON, nullable=True)  # {"lacale": {"id": "123", "url": "..."}, ...}
    tracker_release_names = Column(JSON, nullable=True)  # {"c411": "Custom.Name-FW", ...} - per-tracker release names from naming_template

    # Granular per-tracker status tracking (v2.1)
    # Structure: {"lacale": {"status": "success", "torrent_id": "123", "torrent_url": "...", "error": null, "retry_count": 0}, ...}
    tracker_statuses = Column(JSON, nullable=True)

    # Duplicate check results (v2.1) - persisted results from last check
    # Structure: {"has_duplicates": bool, "checked_at": "ISO datetime", "results": {tracker_slug: {...}}}
    duplicate_check_results = Column(JSON, nullable=True)

    # Approval workflow fields (v2.1)
    approval_requested_at = Column(DateTime, nullable=True)  # When PENDING_APPROVAL was set
    approved_at = Column(DateTime, nullable=True)  # When user approved
    preparing_at = Column(DateTime, nullable=True)  # When PREPARING started
    approved_by = Column(String(100), nullable=True)  # Username who approved
    corrections = Column(JSON, nullable=True)  # Audit trail of user corrections
    final_release_name = Column(String(500), nullable=True)  # User-corrected release name

    # Release structure fields (v2.1 - hardlinks module)
    release_dir = Column(String(1000), nullable=True)  # Path to release folder
    prepared_media_path = Column(String(1000), nullable=True)  # Path to hardlinked/copied media file

    # Screenshot fields (v2.1)
    screenshot_paths = Column(JSON, nullable=True)  # List of local screenshot paths
    screenshot_urls = Column(JSON, nullable=True)  # List of uploaded screenshot URLs with BBCode

    def __init__(self, file_path: str):
        """
        Initialize FileEntry with file path.

        Args:
            file_path: Absolute path to the media file
        """
        self.file_path = file_path
        self.status = Status.PENDING
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    # Checkpoint helper methods

    def is_scanned(self) -> bool:
        """Check if file has been scanned."""
        return self.scanned_at is not None

    def is_analyzed(self) -> bool:
        """Check if file has been analyzed."""
        return self.analyzed_at is not None

    def is_renamed(self) -> bool:
        """Check if file has been renamed."""
        return self.renamed_at is not None

    def is_metadata_generated(self) -> bool:
        """Check if metadata (.torrent, NFO) has been generated."""
        return self.metadata_generated_at is not None

    def is_uploaded(self) -> bool:
        """Check if file has been uploaded to tracker."""
        return self.uploaded_at is not None

    def mark_scanned(self) -> None:
        """Mark file as scanned and update status."""
        self.scanned_at = datetime.utcnow()
        self.status = Status.SCANNED
        self.updated_at = datetime.utcnow()

    def mark_analyzed(self) -> None:
        """Mark file as analyzed and update status."""
        self.analyzed_at = datetime.utcnow()
        self.status = Status.ANALYZED
        self.updated_at = datetime.utcnow()

    def mark_renamed(self) -> None:
        """Mark file as renamed and update status."""
        self.renamed_at = datetime.utcnow()
        self.status = Status.RENAMED
        self.updated_at = datetime.utcnow()

    def mark_metadata_generated(self) -> None:
        """Mark metadata generation as complete and update status."""
        self.metadata_generated_at = datetime.utcnow()
        self.status = Status.METADATA_GENERATED
        self.updated_at = datetime.utcnow()

    def mark_uploaded(self) -> None:
        """Mark file as uploaded and update status."""
        self.uploaded_at = datetime.utcnow()
        self.status = Status.UPLOADED
        self.updated_at = datetime.utcnow()

    def mark_failed(self, error_message: str) -> None:
        """
        Mark file as failed with error message.

        Args:
            error_message: Description of the failure
        """
        self.error_message = error_message
        self.status = Status.FAILED
        self.updated_at = datetime.utcnow()

    def reset_from_checkpoint(self, checkpoint: Status) -> None:
        """
        Reset file entry to retry from a specific checkpoint.

        Clears all checkpoint timestamps after the specified stage,
        allowing the pipeline to resume from that point.

        Args:
            checkpoint: Status to resume from (e.g., Status.ANALYZED to retry rename)
        """
        if checkpoint in [Status.PENDING, Status.SCANNED]:
            self.scanned_at = None
        if checkpoint in [Status.PENDING, Status.SCANNED, Status.ANALYZED]:
            self.analyzed_at = None
        if checkpoint in [Status.PENDING, Status.SCANNED, Status.ANALYZED, Status.RENAMED]:
            self.renamed_at = None
        if checkpoint in [Status.PENDING, Status.SCANNED, Status.ANALYZED, Status.RENAMED, Status.METADATA_GENERATED]:
            self.metadata_generated_at = None
        if checkpoint in [Status.PENDING, Status.SCANNED, Status.ANALYZED, Status.RENAMED, Status.METADATA_GENERATED, Status.UPLOADED]:
            self.uploaded_at = None

        self.status = checkpoint
        self.error_message = None
        self.updated_at = datetime.utcnow()

    @classmethod
    def get_by_path(cls, db: Session, file_path: str) -> Optional['FileEntry']:
        """
        Get file entry by file path.

        Args:
            db: SQLAlchemy database session
            file_path: File path to search for

        Returns:
            FileEntry if found, None otherwise
        """
        return db.query(cls).filter(cls.file_path == file_path).first()

    @classmethod
    def get_by_status(cls, db: Session, status: Status) -> List['FileEntry']:
        """
        Get all file entries with a specific status.

        Args:
            db: SQLAlchemy database session
            status: Status to filter by

        Returns:
            List of FileEntry objects with the specified status
        """
        return db.query(cls).filter(cls.status == status).all()

    @classmethod
    def get_pending(cls, db: Session) -> List['FileEntry']:
        """Get all pending file entries."""
        return cls.get_by_status(db, Status.PENDING)

    @classmethod
    def get_failed(cls, db: Session) -> List['FileEntry']:
        """Get all failed file entries."""
        return cls.get_by_status(db, Status.FAILED)

    @classmethod
    def create_or_get(cls, db: Session, file_path: str, reset_failed: bool = True) -> 'FileEntry':
        """
        Create new file entry or get existing one.

        If entry exists with FAILED status and reset_failed=True, it will be
        reset to PENDING so it can be re-processed.

        Args:
            db: SQLAlchemy database session
            file_path: File path
            reset_failed: If True, reset FAILED entries to PENDING

        Returns:
            FileEntry (new or existing), or None if entry exists but not reset
        """
        entry = cls.get_by_path(db, file_path)
        if entry is None:
            entry = cls(file_path=file_path)
            db.add(entry)
            db.commit()
            db.refresh(entry)
        elif entry.status == Status.FAILED and reset_failed:
            # Reset failed entry so it can be re-processed
            entry.status = Status.PENDING
            entry.error_message = None
            entry.scanned_at = None
            entry.analyzed_at = None
            entry.renamed_at = None
            entry.metadata_generated_at = None
            entry.uploaded_at = None
            entry.torrent_path = None
            entry.nfo_path = None
            db.commit()
            db.refresh(entry)
        return entry

    # ============================================================================
    # Metadata helper methods
    # ============================================================================

    def get_tag_ids(self) -> List[str]:
        """Get tag IDs as a list."""
        if self.tag_ids:
            return self.tag_ids if isinstance(self.tag_ids, list) else []
        return []

    def set_tag_ids(self, tags: List[str]) -> None:
        """Set tag IDs from a list."""
        self.tag_ids = tags
        self.updated_at = datetime.utcnow()

    def add_tag_id(self, tag_id: str) -> None:
        """Add a single tag ID."""
        current_tags = self.get_tag_ids()
        if tag_id not in current_tags:
            current_tags.append(tag_id)
            self.tag_ids = current_tags
            self.updated_at = datetime.utcnow()

    def set_upload_metadata(
        self,
        release_name: str,
        category_id: str,
        tag_ids: List[str],
        tmdb_id: Optional[str] = None,
        tmdb_type: Optional[str] = None,
        cover_url: Optional[str] = None,
        description: Optional[str] = None
    ) -> None:
        """
        Set all upload metadata at once.

        Args:
            release_name: Formatted release name
            category_id: Tracker category ID
            tag_ids: List of tracker tag IDs
            tmdb_id: Optional TMDB ID
            tmdb_type: Optional TMDB type (movie/tv)
            cover_url: Optional cover image URL
            description: Optional plot description
        """
        self.release_name = release_name
        self.category_id = category_id
        self.tag_ids = tag_ids
        self.tmdb_id = tmdb_id
        self.tmdb_type = tmdb_type
        self.cover_url = cover_url
        self.description = description
        self.updated_at = datetime.utcnow()

    def set_upload_result(self, torrent_id: str, torrent_url: str) -> None:
        """
        Set upload result from tracker (legacy single-tracker method).

        Args:
            torrent_id: Torrent ID returned by tracker
            torrent_url: Torrent URL on tracker
        """
        self.tracker_torrent_id = torrent_id
        self.tracker_torrent_url = torrent_url
        self.updated_at = datetime.utcnow()

    # ============================================================================
    # Multi-tracker helper methods
    # ============================================================================

    def get_torrent_paths(self) -> dict:
        """Get torrent paths dictionary."""
        return self.torrent_paths if self.torrent_paths else {}

    def set_torrent_path_for_tracker(self, tracker_slug: str, path: str) -> None:
        """
        Set torrent path for a specific tracker.

        Args:
            tracker_slug: Tracker slug (e.g., "lacale", "c411")
            path: Path to the .torrent file
        """
        paths = self.get_torrent_paths()
        paths[tracker_slug] = path
        self.torrent_paths = paths
        self.updated_at = datetime.utcnow()

    def get_torrent_path_for_tracker(self, tracker_slug: str) -> Optional[str]:
        """
        Get torrent path for a specific tracker.

        Args:
            tracker_slug: Tracker slug

        Returns:
            Path to .torrent file or None
        """
        return self.get_torrent_paths().get(tracker_slug)

    def get_upload_results(self) -> dict:
        """Get upload results dictionary."""
        return self.upload_results if self.upload_results else {}

    def set_upload_result_for_tracker(
        self,
        tracker_slug: str,
        torrent_id: str,
        torrent_url: str,
        **extra_data
    ) -> None:
        """
        Set upload result for a specific tracker.

        Args:
            tracker_slug: Tracker slug (e.g., "lacale", "c411")
            torrent_id: Torrent ID returned by tracker
            torrent_url: Torrent URL on tracker
            **extra_data: Additional data to store
        """
        results = self.get_upload_results()
        results[tracker_slug] = {
            'torrent_id': torrent_id,
            'torrent_url': torrent_url,
            'uploaded_at': datetime.utcnow().isoformat(),
            **extra_data
        }
        self.upload_results = results
        self.updated_at = datetime.utcnow()

    def get_upload_result_for_tracker(self, tracker_slug: str) -> Optional[dict]:
        """
        Get upload result for a specific tracker.

        Args:
            tracker_slug: Tracker slug

        Returns:
            Upload result dict or None
        """
        return self.get_upload_results().get(tracker_slug)

    def is_uploaded_to_tracker(self, tracker_slug: str) -> bool:
        """
        Check if file has been uploaded to a specific tracker.

        Args:
            tracker_slug: Tracker slug

        Returns:
            True if uploaded, False otherwise
        """
        result = self.get_upload_result_for_tracker(tracker_slug)
        return result is not None and result.get('torrent_id') is not None

    # ============================================================================
    # Tracker-specific release name methods (v2.2)
    # ============================================================================

    def get_tracker_release_names(self) -> dict:
        """Get tracker-specific release names dictionary."""
        return self.tracker_release_names if self.tracker_release_names else {}

    def set_tracker_release_name(self, tracker_slug: str, release_name: str) -> None:
        """
        Set release name for a specific tracker.

        Args:
            tracker_slug: Tracker slug (e.g., "lacale", "c411")
            release_name: Tracker-specific release name from naming_template
        """
        names = self.get_tracker_release_names()
        names[tracker_slug] = release_name
        self.tracker_release_names = names
        self.updated_at = datetime.utcnow()

    def get_tracker_release_name(self, tracker_slug: str) -> Optional[str]:
        """
        Get release name for a specific tracker.

        Args:
            tracker_slug: Tracker slug

        Returns:
            Tracker-specific release name or None (use default release_name)
        """
        return self.get_tracker_release_names().get(tracker_slug)

    def get_effective_release_name_for_tracker(self, tracker_slug: str) -> str:
        """
        Get the effective release name for a tracker (tracker-specific or default).

        Args:
            tracker_slug: Tracker slug

        Returns:
            Tracker-specific name if set, otherwise the default release_name
        """
        tracker_name = self.get_tracker_release_name(tracker_slug)
        return tracker_name or self.get_effective_release_name()

    # ============================================================================
    # Granular tracker status methods (v2.1)
    # ============================================================================

    def get_tracker_statuses(self) -> dict:
        """Get all tracker statuses dictionary."""
        return self.tracker_statuses if self.tracker_statuses else {}

    def set_tracker_status(
        self,
        tracker_slug: str,
        status: str,
        torrent_id: Optional[str] = None,
        torrent_url: Optional[str] = None,
        error: Optional[str] = None,
        retry_count: int = 0
    ) -> None:
        """
        Set the status for a specific tracker.

        Args:
            tracker_slug: Tracker slug (e.g., "lacale", "c411")
            status: TrackerStatus value (pending, success, failed, skipped_duplicate, retrying)
            torrent_id: Torrent ID if successfully uploaded
            torrent_url: Torrent URL if successfully uploaded
            error: Error message if failed
            retry_count: Number of retry attempts
        """
        statuses = self.get_tracker_statuses()
        statuses[tracker_slug] = {
            'status': status,
            'torrent_id': torrent_id,
            'torrent_url': torrent_url,
            'error': error,
            'retry_count': retry_count,
            'updated_at': datetime.utcnow().isoformat()
        }
        self.tracker_statuses = statuses
        self.updated_at = datetime.utcnow()
        # Force SQLAlchemy to detect JSON column change
        flag_modified(self, 'tracker_statuses')

    def get_tracker_status(self, tracker_slug: str) -> Optional[dict]:
        """
        Get status for a specific tracker.

        Args:
            tracker_slug: Tracker slug

        Returns:
            Status dict or None if not found
        """
        return self.get_tracker_statuses().get(tracker_slug)

    def get_failed_trackers(self) -> List[str]:
        """
        Get list of tracker slugs that failed upload.

        Returns:
            List of tracker slugs with status "failed"
        """
        failed = []
        for slug, data in self.get_tracker_statuses().items():
            if data.get('status') == TrackerStatus.FAILED.value:
                failed.append(slug)
        return failed

    def get_successful_trackers(self) -> List[str]:
        """
        Get list of tracker slugs with successful uploads.

        Returns:
            List of tracker slugs with status "success"
        """
        successful = []
        for slug, data in self.get_tracker_statuses().items():
            if data.get('status') == TrackerStatus.SUCCESS.value:
                successful.append(slug)
        return successful

    def get_skipped_trackers(self) -> List[str]:
        """
        Get list of tracker slugs skipped due to duplicates.

        Returns:
            List of tracker slugs with status "skipped_duplicate"
        """
        skipped = []
        for slug, data in self.get_tracker_statuses().items():
            if data.get('status') == TrackerStatus.SKIPPED_DUPLICATE.value:
                skipped.append(slug)
        return skipped

    def all_trackers_completed(self) -> bool:
        """
        Check if all trackers have completed (success, failed, or skipped).

        Returns:
            True if no tracker is pending or retrying
        """
        statuses = self.get_tracker_statuses()
        if not statuses:
            return False

        completed_statuses = {
            TrackerStatus.SUCCESS.value,
            TrackerStatus.FAILED.value,
            TrackerStatus.SKIPPED_DUPLICATE.value
        }

        for data in statuses.values():
            if data.get('status') not in completed_statuses:
                return False
        return True

    def increment_tracker_retry(self, tracker_slug: str) -> int:
        """
        Increment retry count for a tracker and set status to retrying.

        Args:
            tracker_slug: Tracker slug

        Returns:
            New retry count
        """
        statuses = self.get_tracker_statuses()
        if tracker_slug in statuses:
            current_count = statuses[tracker_slug].get('retry_count', 0)
            statuses[tracker_slug]['retry_count'] = current_count + 1
            statuses[tracker_slug]['status'] = TrackerStatus.RETRYING.value
            statuses[tracker_slug]['updated_at'] = datetime.utcnow().isoformat()
            self.tracker_statuses = statuses
            self.updated_at = datetime.utcnow()
            return current_count + 1
        return 0

    # ============================================================================
    # Approval workflow methods (v2.1)
    # ============================================================================

    def is_pending_approval(self) -> bool:
        """Check if file is waiting for user approval."""
        return self.approval_requested_at is not None and self.approved_at is None

    def is_approved(self) -> bool:
        """Check if file has been approved."""
        return self.approved_at is not None

    def is_preparing(self) -> bool:
        """Check if file is being prepared (hardlinks, screenshots)."""
        return self.preparing_at is not None

    def mark_pending_approval(self) -> None:
        """Mark file as waiting for user approval."""
        self.approval_requested_at = datetime.utcnow()
        self.status = Status.PENDING_APPROVAL
        self.updated_at = datetime.utcnow()

    def mark_approved(self, approved_by: Optional[str] = None, corrections: Optional[dict] = None) -> None:
        """
        Mark file as approved by user.

        Args:
            approved_by: Username who approved (optional)
            corrections: Dict of user corrections (optional)
        """
        self.approved_at = datetime.utcnow()
        self.status = Status.APPROVED
        if approved_by:
            self.approved_by = approved_by
        if corrections:
            # Merge with existing corrections for audit trail
            existing = self.corrections or {}
            existing[datetime.utcnow().isoformat()] = corrections
            self.corrections = existing
        self.updated_at = datetime.utcnow()

    def mark_preparing(self) -> None:
        """Mark file as being prepared (hardlinks, screenshots, etc.)."""
        self.preparing_at = datetime.utcnow()
        self.status = Status.PREPARING
        self.updated_at = datetime.utcnow()

    def apply_corrections(self, final_release_name: Optional[str] = None, tmdb_id: Optional[str] = None) -> None:
        """
        Apply user corrections before approval.

        Args:
            final_release_name: Corrected release name
            tmdb_id: Corrected TMDB ID
        """
        corrections = {}
        if final_release_name and final_release_name != self.release_name:
            corrections['release_name'] = {'old': self.release_name, 'new': final_release_name}
            self.final_release_name = final_release_name
        if tmdb_id and tmdb_id != self.tmdb_id:
            corrections['tmdb_id'] = {'old': self.tmdb_id, 'new': tmdb_id}
            self.tmdb_id = tmdb_id
        if corrections:
            existing = self.corrections or {}
            existing[datetime.utcnow().isoformat()] = corrections
            self.corrections = existing
        self.updated_at = datetime.utcnow()

    def get_effective_release_name(self) -> str:
        """
        Get the effective release name (corrected or original).

        Returns:
            final_release_name if set, otherwise release_name
        """
        return self.final_release_name or self.release_name or ""

    # ============================================================================
    # Screenshot helper methods (v2.1)
    # ============================================================================

    @property
    def file_size(self) -> Optional[int]:
        """
        Get file size in bytes.

        Attempts to get file size from:
        1. mediainfo_data (if available)
        2. File system (if file exists)

        Returns:
            File size in bytes or None if unavailable
        """
        import os

        # Try to get from mediainfo_data first
        if self.mediainfo_data:
            # file_size is stored at root level (from pipeline.py)
            if 'file_size' in self.mediainfo_data:
                try:
                    return int(self.mediainfo_data['file_size'])
                except (ValueError, TypeError):
                    pass

        # Fallback: get from file system
        if self.file_path and os.path.exists(self.file_path):
            try:
                return os.path.getsize(self.file_path)
            except OSError:
                pass

        return None

    def get_screenshot_paths(self) -> List[str]:
        """Get list of local screenshot paths."""
        return self.screenshot_paths if self.screenshot_paths else []

    def set_screenshot_paths(self, paths: List[str]) -> None:
        """Set local screenshot paths."""
        self.screenshot_paths = paths
        self.updated_at = datetime.utcnow()

    def get_screenshot_urls(self) -> List[dict]:
        """Get list of uploaded screenshot URLs with metadata."""
        return self.screenshot_urls if self.screenshot_urls else []

    def set_screenshot_urls(self, urls: List[dict]) -> None:
        """
        Set uploaded screenshot URLs.

        Args:
            urls: List of dicts with 'url', 'thumb_url', 'bbcode' keys
        """
        self.screenshot_urls = urls
        self.updated_at = datetime.utcnow()

    def to_dict(self) -> dict:
        """Convert file entry to dictionary."""
        return {
            'id': self.id,
            'file_path': self.file_path,
            'status': self.status.value if self.status else None,
            'error_message': self.error_message,
            'release_name': self.release_name,
            'final_release_name': self.final_release_name,
            'category_id': self.category_id,
            'tag_ids': self.get_tag_ids(),
            'tmdb_id': self.tmdb_id,
            'tmdb_type': self.tmdb_type,
            'cover_url': self.cover_url,
            'description': self.description,
            'torrent_path': self.torrent_path,
            'nfo_path': self.nfo_path,
            'mediainfo_data': self.mediainfo_data,
            'tracker_torrent_id': self.tracker_torrent_id,
            'tracker_torrent_url': self.tracker_torrent_url,
            # Multi-tracker fields
            'torrent_paths': self.get_torrent_paths(),
            'upload_results': self.get_upload_results(),
            'tracker_release_names': self.get_tracker_release_names(),
            # Granular tracker statuses (v2.1)
            'tracker_statuses': self.get_tracker_statuses(),
            'failed_trackers': self.get_failed_trackers(),
            'successful_trackers': self.get_successful_trackers(),
            # Approval workflow (v2.1)
            'approved_by': self.approved_by,
            'corrections': self.corrections,
            # Release structure (v2.1)
            'release_dir': self.release_dir,
            'prepared_media_path': self.prepared_media_path,
            # Screenshots (v2.1)
            'screenshot_paths': self.get_screenshot_paths(),
            'screenshot_urls': self.get_screenshot_urls(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'checkpoints': {
                'scanned_at': self.scanned_at.isoformat() if self.scanned_at else None,
                'analyzed_at': self.analyzed_at.isoformat() if self.analyzed_at else None,
                'approval_requested_at': self.approval_requested_at.isoformat() if self.approval_requested_at else None,
                'approved_at': self.approved_at.isoformat() if self.approved_at else None,
                'preparing_at': self.preparing_at.isoformat() if self.preparing_at else None,
                'renamed_at': self.renamed_at.isoformat() if self.renamed_at else None,
                'metadata_generated_at': self.metadata_generated_at.isoformat() if self.metadata_generated_at else None,
                'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            }
        }

    def __repr__(self) -> str:
        """String representation of file entry."""
        return (
            f"<FileEntry(id={self.id}, path='{self.file_path}', "
            f"status={self.status.value})>"
        )
