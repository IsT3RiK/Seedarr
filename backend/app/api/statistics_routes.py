"""
Statistics API Routes

Provides endpoints for viewing upload statistics and metrics.

Endpoints:
- GET /statistics: Statistics dashboard page
- GET /api/statistics: Get statistics data
- GET /api/statistics/summary: Get summary metrics
- GET /api/statistics/timeline: Get timeline data
- GET /api/statistics/trackers: Get per-tracker breakdown
"""

import logging
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.statistics_service import get_statistics_service

logger = logging.getLogger(__name__)

router = APIRouter()
# Auto-detect templates path based on working directory
import os
templates_dir = "templates" if os.path.exists("templates") else "backend/templates"
templates = Jinja2Templates(directory=templates_dir)


@router.get("/statistics", response_class=HTMLResponse)
async def statistics_page(request: Request, db: Session = Depends(get_db)):
    """
    Render the statistics dashboard page.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML response with statistics dashboard
    """
    try:
        service = get_statistics_service(db)
        data = service.get_dashboard_data(days=30)

        return templates.TemplateResponse(
            "statistics.html",
            {
                "request": request,
                "summary": data['summary'],
                "timeline": data['timeline'],
                "tracker_breakdown": data['tracker_breakdown'],
                "period_days": data['period_days']
            }
        )
    except Exception as e:
        logger.error(f"Error rendering statistics page: {e}")
        return templates.TemplateResponse(
            "statistics.html",
            {
                "request": request,
                "error": str(e),
                "summary": {
                    'total_uploads': 0,
                    'successful_uploads': 0,
                    'failed_uploads': 0,
                    'success_rate': 0
                },
                "timeline": [],
                "tracker_breakdown": [],
                "period_days": 30
            }
        )


@router.get("/api/statistics")
async def get_statistics(
    days: int = Query(30, ge=1, le=365, description="Number of days"),
    db: Session = Depends(get_db)
):
    """
    Get full statistics data.

    Args:
        days: Number of days to include
        db: Database session

    Returns:
        Statistics data including summary, timeline, and tracker breakdown
    """
    service = get_statistics_service(db)
    return service.get_dashboard_data(days)


@router.get("/api/statistics/summary")
async def get_summary(
    days: int = Query(30, ge=1, le=365, description="Number of days"),
    db: Session = Depends(get_db)
):
    """
    Get summary statistics.

    Args:
        days: Number of days to include
        db: Database session

    Returns:
        Summary metrics
    """
    from app.models.statistics import DailyStatistics
    return DailyStatistics.get_summary(db, days)


@router.get("/api/statistics/timeline")
async def get_timeline(
    days: int = Query(30, ge=1, le=365, description="Number of days"),
    db: Session = Depends(get_db)
):
    """
    Get timeline data for charts.

    Args:
        days: Number of days to include
        db: Database session

    Returns:
        Daily statistics for timeline chart
    """
    from app.models.statistics import DailyStatistics
    stats = DailyStatistics.get_recent(db, days)
    return [s.to_dict() for s in stats]


@router.get("/api/statistics/trackers")
async def get_tracker_statistics(
    days: int = Query(30, ge=1, le=365, description="Number of days"),
    db: Session = Depends(get_db)
):
    """
    Get per-tracker statistics.

    Args:
        days: Number of days to include
        db: Database session

    Returns:
        Statistics breakdown by tracker
    """
    from app.models.statistics import TrackerStatistics
    return TrackerStatistics.get_tracker_summary(db, days)


@router.get("/api/statistics/recent")
async def get_recent_activity(
    limit: int = Query(10, ge=1, le=50, description="Number of entries"),
    db: Session = Depends(get_db)
):
    """
    Get recent upload activity.

    Args:
        limit: Maximum entries to return
        db: Database session

    Returns:
        Recent upload activity
    """
    service = get_statistics_service(db)
    return service.get_recent_activity(limit)


@router.get("/api/statistics/distribution")
async def get_status_distribution(db: Session = Depends(get_db)):
    """
    Get distribution of file entry statuses.

    Args:
        db: Database session

    Returns:
        Status distribution counts
    """
    service = get_statistics_service(db)
    return service.get_status_distribution()
