"""
Batch Processing API Routes

Provides endpoints for batch processing operations.

Endpoints:
- POST /api/batch: Create a new batch
- POST /api/batch/{id}/start: Start batch processing
- GET /api/batch/{id}: Get batch status
- POST /api/batch/{id}/cancel: Cancel batch
- GET /api/batch: List batches
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.batch_service import get_batch_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/batch", tags=["batch"])


class CreateBatchRequest(BaseModel):
    """Request model for creating a batch."""
    file_entry_ids: List[int] = Field(..., min_length=1, description="File entry IDs to process")
    name: Optional[str] = Field(None, max_length=255, description="Optional batch name")
    priority: str = Field("normal", pattern="^(high|normal|low)$", description="Processing priority")
    skip_approval: bool = Field(False, description="Skip approval step")
    max_concurrent: int = Field(2, ge=1, le=10, description="Max concurrent processing")


class BatchResponse(BaseModel):
    """Response model for batch operations."""
    id: int
    name: Optional[str]
    status: str
    total_count: int
    processed_count: int
    success_count: int
    failed_count: int
    progress_percent: float


@router.post("", response_model=BatchResponse)
async def create_batch(
    request: CreateBatchRequest,
    db: Session = Depends(get_db)
):
    """
    Create a new batch job.

    Args:
        request: Batch creation request
        db: Database session

    Returns:
        Created batch job
    """
    try:
        service = get_batch_service(db)
        batch = service.create_batch(
            file_entry_ids=request.file_entry_ids,
            name=request.name,
            priority=request.priority,
            skip_approval=request.skip_approval,
            max_concurrent=request.max_concurrent
        )

        logger.info(f"Created batch {batch.id} with {batch.total_count} files")
        return batch.to_dict()

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating batch: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{batch_id}/start")
async def start_batch(
    batch_id: int,
    sync: bool = False,
    db: Session = Depends(get_db)
):
    """
    Start batch processing.

    Args:
        batch_id: Batch job ID
        sync: If True, process synchronously (blocking)
        db: Database session

    Returns:
        Start result
    """
    try:
        service = get_batch_service(db)

        if sync:
            result = await service.execute_batch_sync(batch_id)
        else:
            result = await service.start_batch(batch_id)

        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error'))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting batch {batch_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{batch_id}")
async def get_batch(
    batch_id: int,
    db: Session = Depends(get_db)
):
    """
    Get batch job status.

    Args:
        batch_id: Batch job ID
        db: Database session

    Returns:
        Batch status
    """
    service = get_batch_service(db)
    batch = service.get_batch_status(batch_id)

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    return batch


@router.post("/{batch_id}/cancel")
async def cancel_batch(
    batch_id: int,
    db: Session = Depends(get_db)
):
    """
    Cancel a batch job.

    Args:
        batch_id: Batch job ID
        db: Database session

    Returns:
        Cancellation result
    """
    try:
        service = get_batch_service(db)
        result = service.cancel_batch(batch_id)

        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('error'))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling batch {batch_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def list_batches(
    active_only: bool = False,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    """
    List batch jobs.

    Args:
        active_only: Only return active batches
        limit: Maximum batches to return
        db: Database session

    Returns:
        List of batch jobs
    """
    service = get_batch_service(db)

    if active_only:
        batches = service.get_active_batches()
    else:
        batches = service.get_recent_batches(limit)

    return {
        "batches": batches,
        "count": len(batches)
    }
