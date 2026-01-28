"""
Batch Job Database Model

Model for tracking batch processing jobs.

Features:
- Group multiple file entries into a batch
- Track overall batch progress
- Support for batch-level settings
"""

from datetime import datetime
from enum import Enum
from sqlalchemy import Column, Integer, String, DateTime, JSON, Text
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from .base import Base


class BatchStatus(str, Enum):
    """Batch job status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"  # Some succeeded, some failed
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchJob(Base):
    """
    Batch processing job model.

    Groups multiple file entries for batch processing.
    """

    __tablename__ = 'batch_jobs'

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Batch metadata
    name = Column(String(255), nullable=True)  # Optional batch name
    status = Column(String(50), nullable=False, default=BatchStatus.PENDING.value)

    # File entry IDs (JSON array)
    file_entry_ids = Column(JSON, nullable=False, default=list)

    # Progress tracking
    total_count = Column(Integer, nullable=False, default=0)
    processed_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)

    # Processing options
    priority = Column(String(20), nullable=False, default='normal')
    skip_approval = Column(Integer, nullable=False, default=0)
    max_concurrent = Column(Integer, nullable=False, default=2)

    # Results tracking (JSON: {file_entry_id: {status, error, ...}})
    results = Column(JSON, nullable=True, default=dict)

    # Error summary
    error_summary = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, **kwargs):
        """Initialize batch job."""
        if 'file_entry_ids' in kwargs:
            kwargs['total_count'] = len(kwargs['file_entry_ids'])
        super().__init__(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status,
            'file_entry_ids': self.file_entry_ids,
            'total_count': self.total_count,
            'processed_count': self.processed_count,
            'success_count': self.success_count,
            'failed_count': self.failed_count,
            'priority': self.priority,
            'skip_approval': bool(self.skip_approval),
            'max_concurrent': self.max_concurrent,
            'results': self.results,
            'error_summary': self.error_summary,
            'progress_percent': self._calculate_progress(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def _calculate_progress(self) -> float:
        """Calculate progress percentage."""
        if self.total_count == 0:
            return 0.0
        return round((self.processed_count / self.total_count) * 100, 1)

    # ===========================================================================
    # Status Update Methods
    # ===========================================================================

    def mark_started(self, db: Session) -> None:
        """Mark batch as started."""
        self.status = BatchStatus.PROCESSING.value
        self.started_at = datetime.utcnow()
        db.commit()

    def mark_item_completed(
        self,
        db: Session,
        file_entry_id: int,
        success: bool,
        error: Optional[str] = None,
        result_data: Optional[dict] = None
    ) -> None:
        """
        Mark an item as completed.

        Args:
            db: Database session
            file_entry_id: Completed file entry ID
            success: Whether processing succeeded
            error: Error message if failed
            result_data: Additional result data
        """
        self.processed_count += 1
        if success:
            self.success_count += 1
        else:
            self.failed_count += 1

        # Update results
        results = self.results or {}
        results[str(file_entry_id)] = {
            'success': success,
            'error': error,
            'completed_at': datetime.utcnow().isoformat(),
            **(result_data or {})
        }
        self.results = results

        # Check if batch is complete
        if self.processed_count >= self.total_count:
            self._finalize_batch(db)
        else:
            db.commit()

    def _finalize_batch(self, db: Session) -> None:
        """Finalize batch when all items are processed."""
        self.completed_at = datetime.utcnow()

        if self.failed_count == 0:
            self.status = BatchStatus.COMPLETED.value
        elif self.success_count == 0:
            self.status = BatchStatus.FAILED.value
        else:
            self.status = BatchStatus.PARTIAL.value

        # Generate error summary
        if self.failed_count > 0:
            errors = []
            for file_id, result in (self.results or {}).items():
                if not result.get('success') and result.get('error'):
                    errors.append(f"File {file_id}: {result['error']}")
            self.error_summary = '\n'.join(errors[:10])  # Limit to 10 errors

        db.commit()

    def mark_cancelled(self, db: Session) -> None:
        """Mark batch as cancelled."""
        self.status = BatchStatus.CANCELLED.value
        self.completed_at = datetime.utcnow()
        db.commit()

    # ===========================================================================
    # Query Methods
    # ===========================================================================

    @classmethod
    def get_by_id(cls, db: Session, batch_id: int) -> Optional['BatchJob']:
        """Get batch by ID."""
        return db.query(cls).filter(cls.id == batch_id).first()

    @classmethod
    def get_active(cls, db: Session) -> List['BatchJob']:
        """Get active (pending or processing) batches."""
        return (
            db.query(cls)
            .filter(cls.status.in_([BatchStatus.PENDING.value, BatchStatus.PROCESSING.value]))
            .order_by(cls.created_at.desc())
            .all()
        )

    @classmethod
    def get_recent(cls, db: Session, limit: int = 20) -> List['BatchJob']:
        """Get recent batches."""
        return (
            db.query(cls)
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def create_batch(
        cls,
        db: Session,
        file_entry_ids: List[int],
        name: Optional[str] = None,
        priority: str = 'normal',
        skip_approval: bool = False,
        max_concurrent: int = 2
    ) -> 'BatchJob':
        """
        Create a new batch job.

        Args:
            db: Database session
            file_entry_ids: List of file entry IDs to process
            name: Optional batch name
            priority: Processing priority
            skip_approval: Skip approval step
            max_concurrent: Maximum concurrent processing

        Returns:
            Created batch job
        """
        batch = cls(
            name=name,
            file_entry_ids=file_entry_ids,
            total_count=len(file_entry_ids),
            priority=priority,
            skip_approval=1 if skip_approval else 0,
            max_concurrent=max_concurrent
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"<BatchJob(id={self.id}, status={self.status}, "
            f"progress={self.processed_count}/{self.total_count})>"
        )
