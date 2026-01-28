"""
Queue Service

Provides high-level queue management functionality.

Features:
- Add/remove items from queue
- Batch operations
- Queue statistics
- Priority management
"""

import logging
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session

from app.models.processing_queue import (
    ProcessingQueue,
    QueuePriority,
    QueueStatus
)
from app.models.file_entry import FileEntry, FileStatus

logger = logging.getLogger(__name__)


class QueueService:
    """
    Service for managing the processing queue.

    Provides methods for:
    - Adding files to queue (single or batch)
    - Managing queue priorities
    - Retrieving queue status and statistics
    - Cancelling/retrying items
    """

    def __init__(self, db: Session):
        """
        Initialize queue service.

        Args:
            db: Database session
        """
        self.db = db

    # ===========================================================================
    # Add to Queue
    # ===========================================================================

    def add_file(
        self,
        file_entry_id: int,
        priority: str = "normal",
        skip_approval: bool = False
    ) -> Optional[ProcessingQueue]:
        """
        Add a single file to the processing queue.

        Args:
            file_entry_id: ID of the file entry
            priority: Priority level (high, normal, low)
            skip_approval: Skip approval step

        Returns:
            Queue item or None if file not found
        """
        # Validate file exists
        file_entry = self.db.query(FileEntry).filter(FileEntry.id == file_entry_id).first()
        if not file_entry:
            logger.warning(f"File entry {file_entry_id} not found")
            return None

        # Map priority string to enum
        priority_enum = QueuePriority(priority) if priority in [p.value for p in QueuePriority] else QueuePriority.NORMAL

        queue_item = ProcessingQueue.add_to_queue(
            self.db,
            file_entry_id=file_entry_id,
            priority=priority_enum,
            skip_approval=skip_approval
        )

        logger.info(f"Added file {file_entry_id} to queue with priority {priority_enum.value}")
        return queue_item

    def add_files_batch(
        self,
        file_entry_ids: List[int],
        priority: str = "normal",
        skip_approval: bool = False
    ) -> Dict[str, Any]:
        """
        Add multiple files to the queue.

        Args:
            file_entry_ids: List of file entry IDs
            priority: Priority for all items
            skip_approval: Skip approval step

        Returns:
            Dictionary with success count and results
        """
        results = {
            "added": [],
            "already_queued": [],
            "not_found": [],
            "errors": []
        }

        for file_entry_id in file_entry_ids:
            try:
                queue_item = self.add_file(file_entry_id, priority, skip_approval)
                if queue_item:
                    if queue_item.attempts == 0 and queue_item.status == QueueStatus.PENDING:
                        results["added"].append(file_entry_id)
                    else:
                        results["already_queued"].append(file_entry_id)
                else:
                    results["not_found"].append(file_entry_id)
            except Exception as e:
                logger.error(f"Error adding file {file_entry_id} to queue: {e}")
                results["errors"].append({"id": file_entry_id, "error": str(e)})

        logger.info(
            f"Batch add completed: {len(results['added'])} added, "
            f"{len(results['already_queued'])} already queued, "
            f"{len(results['not_found'])} not found"
        )

        return results

    # ===========================================================================
    # Queue Management
    # ===========================================================================

    def get_queue_items(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[ProcessingQueue]:
        """
        Get queue items with optional filtering.

        Args:
            status: Filter by status (pending, processing, completed, failed, cancelled)
            limit: Maximum items to return
            offset: Number of items to skip

        Returns:
            List of queue items
        """
        query = self.db.query(ProcessingQueue)

        if status:
            try:
                status_enum = QueueStatus(status)
                query = query.filter(ProcessingQueue.status == status_enum)
            except ValueError:
                pass

        return (
            query
            .order_by(ProcessingQueue.priority, ProcessingQueue.added_at.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_next_pending(self, count: int = 1) -> List[ProcessingQueue]:
        """
        Get next pending items to process.

        Args:
            count: Number of items to get

        Returns:
            List of pending items ordered by priority
        """
        return ProcessingQueue.get_pending(self.db, limit=count)

    def cancel_item(self, queue_id: int) -> bool:
        """
        Cancel a queue item.

        Args:
            queue_id: Queue item ID

        Returns:
            True if cancelled
        """
        item = ProcessingQueue.get_by_id(self.db, queue_id)
        if not item:
            return False

        if item.status == QueueStatus.PROCESSING:
            logger.warning(f"Cannot cancel processing item {queue_id}")
            return False

        item.mark_cancelled(self.db)
        logger.info(f"Queue item {queue_id} cancelled")
        return True

    def retry_item(self, queue_id: int) -> bool:
        """
        Retry a failed item.

        Args:
            queue_id: Queue item ID

        Returns:
            True if reset for retry
        """
        item = ProcessingQueue.get_by_id(self.db, queue_id)
        if not item:
            return False

        if item.status not in (QueueStatus.FAILED, QueueStatus.CANCELLED):
            return False

        item.reset_for_retry(self.db)
        logger.info(f"Queue item {queue_id} reset for retry")
        return True

    def remove_item(self, queue_id: int) -> bool:
        """
        Remove an item from the queue.

        Args:
            queue_id: Queue item ID

        Returns:
            True if removed
        """
        item = ProcessingQueue.get_by_id(self.db, queue_id)
        if not item:
            return False

        if item.status == QueueStatus.PROCESSING:
            logger.warning(f"Cannot remove processing item {queue_id}")
            return False

        self.db.delete(item)
        self.db.commit()
        logger.info(f"Queue item {queue_id} removed")
        return True

    def update_priority(self, queue_id: int, priority: str) -> bool:
        """
        Update priority of a queue item.

        Args:
            queue_id: Queue item ID
            priority: New priority (high, normal, low)

        Returns:
            True if updated
        """
        item = ProcessingQueue.get_by_id(self.db, queue_id)
        if not item:
            return False

        if item.status == QueueStatus.PROCESSING:
            return False

        try:
            item.priority = QueuePriority(priority)
            self.db.commit()
            logger.info(f"Queue item {queue_id} priority updated to {priority}")
            return True
        except ValueError:
            return False

    # ===========================================================================
    # Statistics
    # ===========================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get queue statistics.

        Returns:
            Dictionary with queue statistics
        """
        status_counts = ProcessingQueue.count_by_status(self.db)

        # Get processing items for active count
        processing = ProcessingQueue.get_processing(self.db)

        # Calculate totals
        total = sum(status_counts.values())
        pending = status_counts.get('pending', 0)
        completed = status_counts.get('completed', 0)
        failed = status_counts.get('failed', 0)

        return {
            "total": total,
            "pending": pending,
            "processing": len(processing),
            "completed": completed,
            "failed": failed,
            "cancelled": status_counts.get('cancelled', 0),
            "status_breakdown": status_counts,
            "success_rate": (completed / (completed + failed) * 100) if (completed + failed) > 0 else 0
        }

    def clear_completed(self, older_than_hours: int = 24) -> int:
        """
        Remove completed items older than specified hours.

        Args:
            older_than_hours: Remove items older than this

        Returns:
            Number of items removed
        """
        from datetime import datetime, timedelta

        cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)

        deleted = (
            self.db.query(ProcessingQueue)
            .filter(ProcessingQueue.status == QueueStatus.COMPLETED)
            .filter(ProcessingQueue.completed_at < cutoff)
            .delete()
        )
        self.db.commit()

        logger.info(f"Cleared {deleted} completed queue items older than {older_than_hours} hours")
        return deleted


def get_queue_service(db: Session) -> QueueService:
    """Get a queue service instance."""
    return QueueService(db)
