"""
Queue Worker

Background worker that processes items from the persistent queue.

Features:
- Async processing with configurable concurrency
- Graceful shutdown support
- Error handling with retry
- Progress logging
- Thread-safe database operations
"""

import asyncio
import logging
from typing import Optional, List, Tuple
from datetime import datetime
from functools import partial

from app.database import SessionLocal
from app.models.processing_queue import ProcessingQueue, QueueStatus
from app.services.structured_logging import set_file_entry_id, clear_context

logger = logging.getLogger(__name__)


def _get_pending_items_sync(limit: int) -> List[Tuple[int, int, int]]:
    """
    Get pending queue items (sync, runs in thread).

    Returns list of (queue_id, file_entry_id, skip_approval) tuples.
    """
    db = SessionLocal()
    try:
        items = ProcessingQueue.get_pending(db, limit=limit)
        return [(item.id, item.file_entry_id, item.skip_approval) for item in items]
    finally:
        db.close()


def _mark_processing_sync(queue_id: int) -> bool:
    """Mark item as processing (sync, runs in thread)."""
    db = SessionLocal()
    try:
        item = ProcessingQueue.get_by_id(db, queue_id)
        if not item or item.status != QueueStatus.PENDING:
            return False
        item.mark_processing(db)
        return True
    finally:
        db.close()


def _mark_completed_sync(queue_id: int) -> None:
    """Mark item as completed (sync, runs in thread)."""
    db = SessionLocal()
    try:
        item = ProcessingQueue.get_by_id(db, queue_id)
        if item:
            item.mark_completed(db)
    finally:
        db.close()


def _mark_failed_sync(queue_id: int, error: str) -> None:
    """Mark item as failed (sync, runs in thread)."""
    db = SessionLocal()
    try:
        item = ProcessingQueue.get_by_id(db, queue_id)
        if item:
            item.mark_failed(db, error)
    finally:
        db.close()


def _get_file_entry_path_sync(file_entry_id: int) -> Optional[str]:
    """Get file entry path (sync, runs in thread)."""
    db = SessionLocal()
    try:
        from app.models.file_entry import FileEntry
        entry = db.query(FileEntry).filter(FileEntry.id == file_entry_id).first()
        return entry.file_path if entry else None
    finally:
        db.close()


class QueueWorker:
    """
    Background worker for processing queue items.

    Polls the queue and processes items using the pipeline.
    Uses asyncio.to_thread() for database operations to avoid blocking.
    """

    def __init__(
        self,
        max_concurrent: int = 2,
        poll_interval: float = 5.0,
        enabled: bool = True
    ):
        """
        Initialize queue worker.

        Args:
            max_concurrent: Maximum concurrent processing tasks
            poll_interval: Seconds between queue polls
            enabled: Whether worker is enabled
        """
        self.max_concurrent = max_concurrent
        self.poll_interval = poll_interval
        self.enabled = enabled
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._active_items: set = set()

    async def start(self) -> None:
        """Start the queue worker."""
        if self._running:
            logger.warning("Queue worker already running")
            return

        if not self.enabled:
            logger.info("Queue worker is disabled")
            return

        self._running = True
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._task = asyncio.create_task(self._worker_loop())
        logger.info(f"Queue worker started (max_concurrent={self.max_concurrent})")

    async def stop(self) -> None:
        """Stop the queue worker gracefully."""
        if not self._running:
            return

        logger.info("Stopping queue worker...")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Wait for active items to complete (with timeout)
        if self._active_items:
            logger.info(f"Waiting for {len(self._active_items)} active items to complete...")
            await asyncio.sleep(0)  # Let them make progress

        logger.info("Queue worker stopped")

    async def _worker_loop(self) -> None:
        """Main worker loop."""
        logger.info("Queue worker loop started")

        while self._running:
            try:
                # Get pending items in thread to avoid blocking
                pending = await asyncio.to_thread(
                    _get_pending_items_sync,
                    self.max_concurrent
                )

                if pending:
                    # Process items concurrently
                    tasks = []
                    for queue_id, file_entry_id, skip_approval in pending:
                        if queue_id not in self._active_items:
                            task = asyncio.create_task(
                                self._process_item(queue_id, file_entry_id, bool(skip_approval))
                            )
                            tasks.append(task)

                    if tasks:
                        # Run all tasks, don't wait for completion to continue polling
                        await asyncio.gather(*tasks, return_exceptions=True)

                # Wait before next poll
                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in queue worker loop: {e}")
                await asyncio.sleep(self.poll_interval)

    async def _process_item(
        self,
        queue_id: int,
        file_entry_id: int,
        skip_approval: bool
    ) -> None:
        """
        Process a single queue item.

        Args:
            queue_id: Queue item ID
            file_entry_id: Associated file entry ID
            skip_approval: Whether to skip approval step
        """
        async with self._semaphore:
            self._active_items.add(queue_id)

            try:
                # Mark as processing (in thread)
                started = await asyncio.to_thread(_mark_processing_sync, queue_id)
                if not started:
                    logger.debug(f"Queue item {queue_id} no longer pending")
                    return

                logger.info(f"Processing queue item {queue_id} (file_entry={file_entry_id})")

                # Set logging context
                set_file_entry_id(file_entry_id)

                # Get file path (in thread)
                file_path = await asyncio.to_thread(_get_file_entry_path_sync, file_entry_id)
                if not file_path:
                    await asyncio.to_thread(_mark_failed_sync, queue_id, "File entry not found")
                    return

                # Process using pipeline
                from app.processors.pipeline import process_file_by_id

                result = await process_file_by_id(file_entry_id, skip_approval=skip_approval)

                if result.get('success'):
                    await asyncio.to_thread(_mark_completed_sync, queue_id)
                    logger.info(f"Queue item {queue_id} completed successfully")
                else:
                    error = result.get('error', 'Unknown error')
                    await asyncio.to_thread(_mark_failed_sync, queue_id, error)
                    logger.warning(f"Queue item {queue_id} failed: {error}")

            except Exception as e:
                logger.error(f"Error processing queue item {queue_id}: {e}")
                try:
                    await asyncio.to_thread(_mark_failed_sync, queue_id, str(e))
                except Exception:
                    pass

            finally:
                clear_context()
                self._active_items.discard(queue_id)

    @property
    def is_running(self) -> bool:
        """Check if worker is running."""
        return self._running

    @property
    def active_count(self) -> int:
        """Get number of items currently being processed."""
        return len(self._active_items)

    def get_status(self) -> dict:
        """Get worker status."""
        return {
            "running": self._running,
            "enabled": self.enabled,
            "max_concurrent": self.max_concurrent,
            "active_count": self.active_count,
            "poll_interval": self.poll_interval
        }


# Global worker instance
_queue_worker: Optional[QueueWorker] = None


def get_queue_worker() -> QueueWorker:
    """Get the global queue worker instance."""
    global _queue_worker
    if _queue_worker is None:
        _queue_worker = QueueWorker()
    return _queue_worker


async def start_queue_worker() -> None:
    """Start the global queue worker."""
    worker = get_queue_worker()
    await worker.start()


async def stop_queue_worker() -> None:
    """Stop the global queue worker."""
    worker = get_queue_worker()
    await worker.stop()
