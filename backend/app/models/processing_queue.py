"""
Processing Queue Database Model

Persistent queue for file processing with priority support.

Features:
- Priority-based processing (high, normal, low)
- Retry tracking with configurable max attempts
- Status tracking for monitoring
- Timestamps for analytics
"""

from datetime import datetime
from enum import Enum
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import Session, relationship
from typing import Optional, List

from .base import Base


class QueuePriority(str, Enum):
    """Queue priority levels."""
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    @property
    def sort_order(self) -> int:
        """Return numeric sort order (lower = higher priority)."""
        return {"high": 0, "normal": 1, "low": 2}[self.value]


class QueueStatus(str, Enum):
    """Queue item status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessingQueue(Base):
    """
    Persistent processing queue model.

    Stores queue items for async processing with:
    - Priority-based ordering
    - Retry tracking
    - Processing status
    """

    __tablename__ = 'processing_queue'

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Reference to file entry being processed
    file_entry_id = Column(Integer, ForeignKey('file_entries.id'), nullable=False, index=True)

    # Queue settings
    priority = Column(SQLEnum(QueuePriority), nullable=False, default=QueuePriority.NORMAL)
    status = Column(SQLEnum(QueueStatus), nullable=False, default=QueueStatus.PENDING, index=True)

    # Retry tracking
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    last_error = Column(String(2000), nullable=True)

    # Processing options
    skip_approval = Column(Integer, nullable=False, default=0)  # SQLite doesn't have Boolean, use 0/1

    # Timestamps
    added_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship to FileEntry
    file_entry = relationship("FileEntry", backref="queue_items")

    def __init__(self, **kwargs):
        """Initialize queue item."""
        super().__init__(**kwargs)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'file_entry_id': self.file_entry_id,
            'priority': self.priority.value if self.priority else None,
            'status': self.status.value if self.status else None,
            'attempts': self.attempts,
            'max_attempts': self.max_attempts,
            'last_error': self.last_error,
            'skip_approval': bool(self.skip_approval),
            'added_at': self.added_at.isoformat() if self.added_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    # ===========================================================================
    # Query Methods
    # ===========================================================================

    @classmethod
    def get_by_id(cls, db: Session, queue_id: int) -> Optional['ProcessingQueue']:
        """Get queue item by ID."""
        return db.query(cls).filter(cls.id == queue_id).first()

    @classmethod
    def get_by_file_entry_id(cls, db: Session, file_entry_id: int) -> Optional['ProcessingQueue']:
        """Get queue item by file entry ID."""
        return db.query(cls).filter(cls.file_entry_id == file_entry_id).first()

    @classmethod
    def get_pending(cls, db: Session, limit: int = 10) -> List['ProcessingQueue']:
        """
        Get pending queue items ordered by priority and add time.

        Args:
            db: Database session
            limit: Maximum items to return

        Returns:
            List of pending queue items
        """
        return (
            db.query(cls)
            .filter(cls.status == QueueStatus.PENDING)
            .filter(cls.attempts < cls.max_attempts)
            .order_by(
                # Priority order: high=0, normal=1, low=2
                cls.priority,
                cls.added_at.asc()
            )
            .limit(limit)
            .all()
        )

    @classmethod
    def get_processing(cls, db: Session) -> List['ProcessingQueue']:
        """Get items currently being processed."""
        return db.query(cls).filter(cls.status == QueueStatus.PROCESSING).all()

    @classmethod
    def get_failed(cls, db: Session, limit: int = 50) -> List['ProcessingQueue']:
        """Get failed queue items."""
        return (
            db.query(cls)
            .filter(cls.status == QueueStatus.FAILED)
            .order_by(cls.updated_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def count_by_status(cls, db: Session) -> dict:
        """Get count of items by status."""
        from sqlalchemy import func
        results = (
            db.query(cls.status, func.count(cls.id))
            .group_by(cls.status)
            .all()
        )
        return {status.value if status else 'unknown': count for status, count in results}

    # ===========================================================================
    # Status Update Methods
    # ===========================================================================

    def mark_processing(self, db: Session) -> None:
        """Mark item as processing."""
        self.status = QueueStatus.PROCESSING
        self.started_at = datetime.utcnow()
        self.attempts += 1
        db.commit()

    def mark_completed(self, db: Session) -> None:
        """Mark item as completed."""
        self.status = QueueStatus.COMPLETED
        self.completed_at = datetime.utcnow()
        self.last_error = None
        db.commit()

    def mark_failed(self, db: Session, error: str) -> None:
        """
        Mark item as failed.

        If attempts < max_attempts, revert to pending for retry.
        """
        self.last_error = error[:2000] if error else None

        if self.attempts >= self.max_attempts:
            self.status = QueueStatus.FAILED
        else:
            self.status = QueueStatus.PENDING

        db.commit()

    def mark_cancelled(self, db: Session) -> None:
        """Mark item as cancelled."""
        self.status = QueueStatus.CANCELLED
        db.commit()

    def reset_for_retry(self, db: Session) -> None:
        """Reset item for retry."""
        self.status = QueueStatus.PENDING
        self.attempts = 0
        self.last_error = None
        self.started_at = None
        self.completed_at = None
        db.commit()

    # ===========================================================================
    # Factory Methods
    # ===========================================================================

    @classmethod
    def add_to_queue(
        cls,
        db: Session,
        file_entry_id: int,
        priority: QueuePriority = QueuePriority.NORMAL,
        skip_approval: bool = False,
        max_attempts: int = 3
    ) -> 'ProcessingQueue':
        """
        Add a file entry to the processing queue.

        Args:
            db: Database session
            file_entry_id: ID of the file entry to process
            priority: Queue priority
            skip_approval: Whether to skip the approval step
            max_attempts: Maximum retry attempts

        Returns:
            Created queue item
        """
        # Check if already in queue
        existing = cls.get_by_file_entry_id(db, file_entry_id)
        if existing:
            # Reset if failed/cancelled
            if existing.status in (QueueStatus.FAILED, QueueStatus.CANCELLED):
                existing.reset_for_retry(db)
                existing.priority = priority
                existing.skip_approval = 1 if skip_approval else 0
                existing.max_attempts = max_attempts
                db.commit()
            return existing

        # Create new queue item
        queue_item = cls(
            file_entry_id=file_entry_id,
            priority=priority,
            skip_approval=1 if skip_approval else 0,
            max_attempts=max_attempts
        )
        db.add(queue_item)
        db.commit()
        db.refresh(queue_item)
        return queue_item

    @classmethod
    def remove_from_queue(cls, db: Session, file_entry_id: int) -> bool:
        """
        Remove a file entry from the queue.

        Args:
            db: Database session
            file_entry_id: ID of the file entry

        Returns:
            True if removed, False if not found
        """
        item = cls.get_by_file_entry_id(db, file_entry_id)
        if item:
            db.delete(item)
            db.commit()
            return True
        return False

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"<ProcessingQueue(id={self.id}, file_entry_id={self.file_entry_id}, "
            f"priority={self.priority.value}, status={self.status.value}, "
            f"attempts={self.attempts}/{self.max_attempts})>"
        )
