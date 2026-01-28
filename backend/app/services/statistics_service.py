"""
Statistics Service

Service for tracking and retrieving upload statistics.

Features:
- Record upload results
- Get daily/weekly/monthly statistics
- Tracker breakdown
- Dashboard metrics
"""

import logging
from datetime import date, timedelta
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.statistics import DailyStatistics, TrackerStatistics
from app.models.file_entry import FileEntry

logger = logging.getLogger(__name__)


class StatisticsService:
    """
    Service for managing upload statistics.

    Provides methods for:
    - Recording upload results
    - Retrieving statistics
    - Generating dashboard data
    """

    def __init__(self, db: Session):
        """Initialize statistics service."""
        self.db = db

    def record_upload(
        self,
        success: bool,
        tracker_name: str = None,
        processing_time_seconds: float = None,
        bytes_processed: int = 0
    ):
        """
        Record an upload result.

        Args:
            success: Whether upload succeeded
            tracker_name: Name of the tracker
            processing_time_seconds: Processing time
            bytes_processed: Size of processed file
        """
        try:
            # Record daily statistics
            DailyStatistics.record_upload(
                self.db,
                success=success,
                processing_time_seconds=processing_time_seconds,
                bytes_processed=bytes_processed
            )

            # Record tracker statistics if tracker specified
            if tracker_name:
                TrackerStatistics.record_upload(
                    self.db,
                    tracker_name=tracker_name,
                    success=success,
                    processing_time_seconds=processing_time_seconds
                )

            logger.debug(f"Recorded upload statistics: success={success}, tracker={tracker_name}")

        except Exception as e:
            logger.error(f"Error recording statistics: {e}")

    def get_dashboard_data(self, days: int = 30) -> Dict[str, Any]:
        """
        Get data for statistics dashboard.

        Args:
            days: Number of days to include

        Returns:
            Dashboard data including summary, timeline, and tracker breakdown
        """
        # Get summary
        summary = DailyStatistics.get_summary(self.db, days)

        # Get daily timeline data
        daily_stats = DailyStatistics.get_recent(self.db, days)
        timeline = [s.to_dict() for s in daily_stats]

        # Get tracker breakdown
        tracker_breakdown = TrackerStatistics.get_tracker_summary(self.db, days)

        # Calculate additional metrics from FileEntry if no recorded stats
        if summary['total_uploads'] == 0:
            # Try to calculate from file_entry table
            historical = self._calculate_historical_stats(days)
            if historical['total_uploads'] > 0:
                summary = historical

        # If no tracker breakdown stats, calculate from FileEntry.tracker_statuses
        if not tracker_breakdown:
            tracker_breakdown = self._calculate_tracker_breakdown_from_entries(days)

        return {
            'summary': summary,
            'timeline': timeline,
            'tracker_breakdown': tracker_breakdown,
            'period_days': days
        }

    def _calculate_historical_stats(self, days: int) -> Dict[str, Any]:
        """Calculate historical stats from FileEntry table."""
        try:
            end_date = date.today()
            start_date = end_date - timedelta(days=days)

            # Count by status
            from app.models.file_entry import Status

            total = self.db.query(func.count(FileEntry.id)).filter(
                FileEntry.created_at >= start_date
            ).scalar() or 0

            successful = self.db.query(func.count(FileEntry.id)).filter(
                FileEntry.created_at >= start_date,
                FileEntry.status == Status.UPLOADED
            ).scalar() or 0

            failed = self.db.query(func.count(FileEntry.id)).filter(
                FileEntry.created_at >= start_date,
                FileEntry.status == Status.FAILED
            ).scalar() or 0

            return {
                'period_days': days,
                'total_uploads': total,
                'successful_uploads': successful,
                'failed_uploads': failed,
                'success_rate': round((successful / total * 100) if total > 0 else 0, 1),
                'avg_processing_time': None,
                'total_bytes_processed': 0,
                'source': 'historical'
            }

        except Exception as e:
            logger.warning(f"Could not calculate historical stats: {e}")
            return {
                'period_days': days,
                'total_uploads': 0,
                'successful_uploads': 0,
                'failed_uploads': 0,
                'success_rate': 0,
                'avg_processing_time': None,
                'total_bytes_processed': 0
            }

    def _calculate_tracker_breakdown_from_entries(self, days: int) -> List[Dict[str, Any]]:
        """Calculate tracker breakdown from FileEntry.tracker_statuses field."""
        try:
            end_date = date.today()
            start_date = end_date - timedelta(days=days)

            # Get all entries with tracker_statuses in the period
            entries = self.db.query(FileEntry).filter(
                FileEntry.created_at >= start_date,
                FileEntry.tracker_statuses.isnot(None)
            ).all()

            # Aggregate by tracker
            tracker_stats = {}
            for entry in entries:
                statuses = entry.tracker_statuses or {}
                for tracker_slug, data in statuses.items():
                    if tracker_slug not in tracker_stats:
                        tracker_stats[tracker_slug] = {
                            'tracker_name': tracker_slug,
                            'total_uploads': 0,
                            'successful_uploads': 0,
                            'failed_uploads': 0
                        }

                    tracker_stats[tracker_slug]['total_uploads'] += 1
                    status = data.get('status', '')
                    if status == 'success':
                        tracker_stats[tracker_slug]['successful_uploads'] += 1
                    elif status in ('failed', 'error'):
                        tracker_stats[tracker_slug]['failed_uploads'] += 1

            # Calculate success rates and return as list
            result = []
            for slug, stats in tracker_stats.items():
                total = stats['total_uploads']
                successful = stats['successful_uploads']
                stats['success_rate'] = round((successful / total * 100) if total > 0 else 0, 1)
                stats['avg_processing_time'] = None
                result.append(stats)

            return result

        except Exception as e:
            logger.warning(f"Could not calculate tracker breakdown from entries: {e}")
            return []

    def get_recent_activity(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent upload activity."""
        from app.models.file_entry import FileEntry, Status

        recent = self.db.query(FileEntry).filter(
            FileEntry.status.in_([Status.UPLOADED, Status.FAILED])
        ).order_by(FileEntry.updated_at.desc()).limit(limit).all()

        return [
            {
                'id': entry.id,
                'file_name': entry.file_path.split('/')[-1] if entry.file_path else 'Unknown',
                'status': entry.status.value if hasattr(entry.status, 'value') else str(entry.status),
                'updated_at': entry.updated_at.isoformat() if entry.updated_at else None
            }
            for entry in recent
        ]

    def get_status_distribution(self) -> Dict[str, int]:
        """Get distribution of file entry statuses."""
        from app.models.file_entry import FileEntry, Status

        results = self.db.query(
            FileEntry.status,
            func.count(FileEntry.id)
        ).group_by(FileEntry.status).all()

        return {
            str(status.value if hasattr(status, 'value') else status): count
            for status, count in results
        }


def get_statistics_service(db: Session) -> StatisticsService:
    """Get a statistics service instance."""
    return StatisticsService(db)
