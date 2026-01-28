"""
Batch Service

Service for managing batch processing operations.

Features:
- Create and manage batch jobs
- Execute batch processing with concurrency control
- Progress tracking and reporting
- Integration with queue system
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.batch_job import BatchJob, BatchStatus
from app.models.file_entry import FileEntry
from app.models.processing_queue import ProcessingQueue, QueuePriority
from app.services.notification_service import get_notification_service

logger = logging.getLogger(__name__)


class BatchService:
    """
    Service for batch processing operations.

    Provides methods for:
    - Creating batch jobs
    - Executing batches
    - Tracking progress
    - Cancelling batches
    """

    def __init__(self, db: Session):
        """
        Initialize batch service.

        Args:
            db: Database session
        """
        self.db = db

    def create_batch(
        self,
        file_entry_ids: List[int],
        name: Optional[str] = None,
        priority: str = 'normal',
        skip_approval: bool = False,
        max_concurrent: int = 2
    ) -> BatchJob:
        """
        Create a new batch job.

        Args:
            file_entry_ids: List of file entry IDs to process
            name: Optional batch name
            priority: Processing priority (high, normal, low)
            skip_approval: Skip the approval step
            max_concurrent: Maximum concurrent processing

        Returns:
            Created BatchJob
        """
        # Validate file entries exist
        valid_ids = []
        for file_id in file_entry_ids:
            entry = self.db.query(FileEntry).filter(FileEntry.id == file_id).first()
            if entry:
                valid_ids.append(file_id)
            else:
                logger.warning(f"File entry {file_id} not found, skipping")

        if not valid_ids:
            raise ValueError("No valid file entries found")

        # Create batch job
        batch = BatchJob.create_batch(
            db=self.db,
            file_entry_ids=valid_ids,
            name=name or f"Batch {datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
            priority=priority,
            skip_approval=skip_approval,
            max_concurrent=max_concurrent
        )

        logger.info(f"Created batch job {batch.id} with {len(valid_ids)} files")
        return batch

    async def start_batch(self, batch_id: int) -> Dict[str, Any]:
        """
        Start processing a batch job.

        Adds all files to the queue and returns immediately.
        Processing happens asynchronously via the queue worker.

        Args:
            batch_id: Batch job ID

        Returns:
            Start result
        """
        batch = BatchJob.get_by_id(self.db, batch_id)
        if not batch:
            return {'success': False, 'error': 'Batch not found'}

        if batch.status not in [BatchStatus.PENDING.value, BatchStatus.CANCELLED.value]:
            return {'success': False, 'error': f'Batch cannot be started (status: {batch.status})'}

        # Mark batch as started
        batch.mark_started(self.db)

        # Map priority
        priority_map = {
            'high': QueuePriority.HIGH,
            'normal': QueuePriority.NORMAL,
            'low': QueuePriority.LOW
        }
        queue_priority = priority_map.get(batch.priority, QueuePriority.NORMAL)

        # Add files to queue
        added = 0
        for file_entry_id in batch.file_entry_ids:
            try:
                ProcessingQueue.add_to_queue(
                    db=self.db,
                    file_entry_id=file_entry_id,
                    priority=queue_priority,
                    skip_approval=bool(batch.skip_approval)
                )
                added += 1
            except Exception as e:
                logger.error(f"Failed to add file {file_entry_id} to queue: {e}")

        logger.info(f"Batch {batch_id} started, added {added} files to queue")

        return {
            'success': True,
            'batch_id': batch_id,
            'files_queued': added,
            'total_files': batch.total_count
        }

    async def execute_batch_sync(self, batch_id: int) -> Dict[str, Any]:
        """
        Execute a batch synchronously (blocking).

        Processes all files in the batch with concurrency control.
        Useful for immediate processing without queue.

        Args:
            batch_id: Batch job ID

        Returns:
            Execution result
        """
        from app.processors.pipeline import process_file

        batch = BatchJob.get_by_id(self.db, batch_id)
        if not batch:
            return {'success': False, 'error': 'Batch not found'}

        batch.mark_started(self.db)

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(batch.max_concurrent)

        async def process_item(file_entry_id: int):
            """Process a single item with semaphore."""
            async with semaphore:
                try:
                    file_entry = self.db.query(FileEntry).filter(
                        FileEntry.id == file_entry_id
                    ).first()

                    if not file_entry:
                        batch.mark_item_completed(
                            self.db, file_entry_id,
                            success=False,
                            error="File entry not found"
                        )
                        return

                    result = await process_file(
                        file_entry,
                        skip_approval=bool(batch.skip_approval)
                    )

                    batch.mark_item_completed(
                        self.db, file_entry_id,
                        success=result.get('success', False),
                        error=result.get('error'),
                        result_data=result
                    )

                except Exception as e:
                    logger.error(f"Error processing file {file_entry_id}: {e}")
                    batch.mark_item_completed(
                        self.db, file_entry_id,
                        success=False,
                        error=str(e)
                    )

        # Process all items concurrently with limit
        tasks = [process_item(fid) for fid in batch.file_entry_ids]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Refresh batch to get final state
        self.db.refresh(batch)

        # Send notification
        try:
            notification_service = get_notification_service(self.db)
            await notification_service.notify_batch_complete(
                total=batch.total_count,
                successful=batch.success_count,
                failed=batch.failed_count,
                batch_id=batch.id
            )
        except Exception as e:
            logger.warning(f"Failed to send batch notification: {e}")

        return {
            'success': True,
            'batch_id': batch_id,
            'status': batch.status,
            'total': batch.total_count,
            'successful': batch.success_count,
            'failed': batch.failed_count,
            'results': batch.results
        }

    def cancel_batch(self, batch_id: int) -> Dict[str, Any]:
        """
        Cancel a batch job.

        Note: Already processing items will complete.

        Args:
            batch_id: Batch job ID

        Returns:
            Cancellation result
        """
        batch = BatchJob.get_by_id(self.db, batch_id)
        if not batch:
            return {'success': False, 'error': 'Batch not found'}

        if batch.status in [BatchStatus.COMPLETED.value, BatchStatus.FAILED.value]:
            return {'success': False, 'error': f'Batch already finished (status: {batch.status})'}

        # Remove pending items from queue
        removed = 0
        for file_entry_id in batch.file_entry_ids:
            queue_item = ProcessingQueue.get_by_file_entry_id(self.db, file_entry_id)
            if queue_item and queue_item.status.value == 'pending':
                ProcessingQueue.remove_from_queue(self.db, file_entry_id)
                removed += 1

        batch.mark_cancelled(self.db)
        logger.info(f"Batch {batch_id} cancelled, removed {removed} items from queue")

        return {
            'success': True,
            'batch_id': batch_id,
            'items_removed': removed
        }

    def get_batch_status(self, batch_id: int) -> Optional[Dict[str, Any]]:
        """
        Get batch job status.

        Args:
            batch_id: Batch job ID

        Returns:
            Batch status dictionary or None
        """
        batch = BatchJob.get_by_id(self.db, batch_id)
        if not batch:
            return None
        return batch.to_dict()

    def get_active_batches(self) -> List[Dict[str, Any]]:
        """Get active (pending or processing) batches."""
        batches = BatchJob.get_active(self.db)
        return [b.to_dict() for b in batches]

    def get_recent_batches(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent batches."""
        batches = BatchJob.get_recent(self.db, limit)
        return [b.to_dict() for b in batches]


def get_batch_service(db: Session) -> BatchService:
    """Get a batch service instance."""
    return BatchService(db)
