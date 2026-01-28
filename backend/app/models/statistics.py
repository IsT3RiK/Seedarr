"""
Statistics Models

Database models for tracking upload statistics and metrics.

Features:
- Daily aggregated statistics
- Per-tracker breakdown
- Success/failure tracking
- Processing time metrics
"""

from datetime import datetime, date
from typing import List, Dict, Any, Optional
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, func
from sqlalchemy.orm import Session

from app.models.base import Base


class DailyStatistics(Base):
    """
    Daily aggregated statistics model.

    Tracks daily upload metrics including:
    - Total uploads attempted
    - Successful uploads
    - Failed uploads
    - Average processing time
    - Breakdown by tracker
    """

    __tablename__ = 'daily_statistics'

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, unique=True, index=True)

    # Upload counts
    total_uploads = Column(Integer, nullable=False, default=0)
    successful_uploads = Column(Integer, nullable=False, default=0)
    failed_uploads = Column(Integer, nullable=False, default=0)

    # Processing metrics
    avg_processing_time_seconds = Column(Float, nullable=True)
    total_processing_time_seconds = Column(Float, nullable=False, default=0)

    # File size metrics
    total_bytes_processed = Column(Integer, nullable=False, default=0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'date': self.date.isoformat() if self.date else None,
            'total_uploads': self.total_uploads,
            'successful_uploads': self.successful_uploads,
            'failed_uploads': self.failed_uploads,
            'success_rate': round(
                (self.successful_uploads / self.total_uploads * 100) if self.total_uploads > 0 else 0, 1
            ),
            'avg_processing_time_seconds': round(self.avg_processing_time_seconds, 2) if self.avg_processing_time_seconds else None,
            'total_bytes_processed': self.total_bytes_processed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    @classmethod
    def get_or_create_for_date(cls, db: Session, target_date: date = None) -> 'DailyStatistics':
        """Get or create statistics record for a date."""
        if target_date is None:
            target_date = date.today()

        stats = db.query(cls).filter(cls.date == target_date).first()
        if not stats:
            stats = cls(date=target_date)
            db.add(stats)
            db.commit()
            db.refresh(stats)

        return stats

    @classmethod
    def record_upload(
        cls,
        db: Session,
        success: bool,
        processing_time_seconds: float = None,
        bytes_processed: int = 0
    ):
        """
        Record an upload result.

        Args:
            db: Database session
            success: Whether the upload succeeded
            processing_time_seconds: Time taken to process
            bytes_processed: Size of file processed
        """
        stats = cls.get_or_create_for_date(db)

        stats.total_uploads += 1
        if success:
            stats.successful_uploads += 1
        else:
            stats.failed_uploads += 1

        if processing_time_seconds:
            stats.total_processing_time_seconds += processing_time_seconds
            # Recalculate average
            stats.avg_processing_time_seconds = (
                stats.total_processing_time_seconds / stats.total_uploads
            )

        stats.total_bytes_processed += bytes_processed
        stats.updated_at = datetime.utcnow()

        db.commit()
        return stats

    @classmethod
    def get_range(cls, db: Session, start_date: date, end_date: date) -> List['DailyStatistics']:
        """Get statistics for a date range."""
        return db.query(cls).filter(
            cls.date >= start_date,
            cls.date <= end_date
        ).order_by(cls.date).all()

    @classmethod
    def get_recent(cls, db: Session, days: int = 30) -> List['DailyStatistics']:
        """Get statistics for the last N days."""
        from datetime import timedelta
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        return cls.get_range(db, start_date, end_date)

    @classmethod
    def get_summary(cls, db: Session, days: int = 30) -> Dict[str, Any]:
        """Get summary statistics for the last N days."""
        from datetime import timedelta
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        result = db.query(
            func.sum(cls.total_uploads).label('total'),
            func.sum(cls.successful_uploads).label('successful'),
            func.sum(cls.failed_uploads).label('failed'),
            func.avg(cls.avg_processing_time_seconds).label('avg_time'),
            func.sum(cls.total_bytes_processed).label('total_bytes')
        ).filter(
            cls.date >= start_date,
            cls.date <= end_date
        ).first()

        total = result.total or 0
        successful = result.successful or 0
        failed = result.failed or 0

        return {
            'period_days': days,
            'total_uploads': total,
            'successful_uploads': successful,
            'failed_uploads': failed,
            'success_rate': round((successful / total * 100) if total > 0 else 0, 1),
            'avg_processing_time': round(result.avg_time, 2) if result.avg_time else None,
            'total_bytes_processed': result.total_bytes or 0
        }


class TrackerStatistics(Base):
    """
    Per-tracker statistics model.

    Tracks upload metrics broken down by tracker.
    """

    __tablename__ = 'tracker_statistics'

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    tracker_name = Column(String(100), nullable=False, index=True)

    # Upload counts
    total_uploads = Column(Integer, nullable=False, default=0)
    successful_uploads = Column(Integer, nullable=False, default=0)
    failed_uploads = Column(Integer, nullable=False, default=0)

    # Processing metrics
    avg_processing_time_seconds = Column(Float, nullable=True)
    total_processing_time_seconds = Column(Float, nullable=False, default=0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'date': self.date.isoformat() if self.date else None,
            'tracker_name': self.tracker_name,
            'total_uploads': self.total_uploads,
            'successful_uploads': self.successful_uploads,
            'failed_uploads': self.failed_uploads,
            'success_rate': round(
                (self.successful_uploads / self.total_uploads * 100) if self.total_uploads > 0 else 0, 1
            ),
            'avg_processing_time_seconds': round(self.avg_processing_time_seconds, 2) if self.avg_processing_time_seconds else None
        }

    @classmethod
    def get_or_create(cls, db: Session, tracker_name: str, target_date: date = None) -> 'TrackerStatistics':
        """Get or create tracker statistics record."""
        if target_date is None:
            target_date = date.today()

        stats = db.query(cls).filter(
            cls.date == target_date,
            cls.tracker_name == tracker_name
        ).first()

        if not stats:
            stats = cls(date=target_date, tracker_name=tracker_name)
            db.add(stats)
            db.commit()
            db.refresh(stats)

        return stats

    @classmethod
    def record_upload(
        cls,
        db: Session,
        tracker_name: str,
        success: bool,
        processing_time_seconds: float = None
    ):
        """Record an upload for a specific tracker."""
        stats = cls.get_or_create(db, tracker_name)

        stats.total_uploads += 1
        if success:
            stats.successful_uploads += 1
        else:
            stats.failed_uploads += 1

        if processing_time_seconds:
            stats.total_processing_time_seconds += processing_time_seconds
            stats.avg_processing_time_seconds = (
                stats.total_processing_time_seconds / stats.total_uploads
            )

        stats.updated_at = datetime.utcnow()
        db.commit()
        return stats

    @classmethod
    def get_tracker_summary(cls, db: Session, days: int = 30) -> List[Dict[str, Any]]:
        """Get summary by tracker for the last N days."""
        from datetime import timedelta
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        results = db.query(
            cls.tracker_name,
            func.sum(cls.total_uploads).label('total'),
            func.sum(cls.successful_uploads).label('successful'),
            func.sum(cls.failed_uploads).label('failed'),
            func.avg(cls.avg_processing_time_seconds).label('avg_time')
        ).filter(
            cls.date >= start_date,
            cls.date <= end_date
        ).group_by(cls.tracker_name).all()

        return [
            {
                'tracker_name': r.tracker_name,
                'total_uploads': r.total or 0,
                'successful_uploads': r.successful or 0,
                'failed_uploads': r.failed or 0,
                'success_rate': round((r.successful / r.total * 100) if r.total else 0, 1),
                'avg_processing_time': round(r.avg_time, 2) if r.avg_time else None
            }
            for r in results
        ]
