"""
Notification Models

Database models for notification channels and log.

Features:
- Multiple notification channels (Discord, Email)
- Event-based notifications
- Notification history/log
"""

from datetime import datetime
from enum import Enum
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, JSON
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from .base import Base


class NotificationChannel(str, Enum):
    """Notification channel types."""
    DISCORD = "discord"
    EMAIL = "email"


class NotificationEvent(str, Enum):
    """Events that can trigger notifications."""
    UPLOAD_SUCCESS = "upload_success"
    UPLOAD_FAILED = "upload_failed"
    BATCH_COMPLETE = "batch_complete"
    QUEUE_ERROR = "queue_error"
    SYSTEM_ERROR = "system_error"


class NotificationLog(Base):
    """
    Log of sent notifications.

    Tracks notification history for auditing and debugging.
    """

    __tablename__ = 'notification_logs'

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Notification details
    channel = Column(String(50), nullable=False)
    event = Column(String(50), nullable=False)
    recipient = Column(String(500), nullable=True)  # Email or webhook URL (masked in output)
    subject = Column(String(500), nullable=True)
    message = Column(Text, nullable=True)

    # Status
    success = Column(Boolean, nullable=False, default=False)
    error_message = Column(String(1000), nullable=True)

    # Related entities
    file_entry_id = Column(Integer, nullable=True)
    batch_id = Column(Integer, nullable=True)

    # Extra data
    extra_data = Column(JSON, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __init__(self, **kwargs):
        """Initialize notification log entry."""
        super().__init__(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'channel': self.channel,
            'event': self.event,
            'recipient': self._mask_recipient(self.recipient),
            'subject': self.subject,
            'message': self.message[:200] + '...' if self.message and len(self.message) > 200 else self.message,
            'success': self.success,
            'error_message': self.error_message,
            'file_entry_id': self.file_entry_id,
            'batch_id': self.batch_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    @staticmethod
    def _mask_recipient(recipient: Optional[str]) -> Optional[str]:
        """Mask sensitive parts of recipient."""
        if not recipient:
            return None
        if '@' in recipient:
            # Email: mask middle of local part
            local, domain = recipient.split('@', 1)
            if len(local) > 2:
                local = local[:2] + '*' * (len(local) - 2)
            return f"{local}@{domain}"
        if 'discord' in recipient.lower():
            # Discord webhook: mask token
            return recipient[:50] + '***'
        return recipient[:20] + '***' if len(recipient) > 20 else recipient

    # ===========================================================================
    # Query Methods
    # ===========================================================================

    @classmethod
    def get_recent(cls, db: Session, limit: int = 50) -> List['NotificationLog']:
        """Get recent notification logs."""
        return (
            db.query(cls)
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def get_by_event(cls, db: Session, event: str, limit: int = 50) -> List['NotificationLog']:
        """Get logs by event type."""
        return (
            db.query(cls)
            .filter(cls.event == event)
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def get_failures(cls, db: Session, limit: int = 50) -> List['NotificationLog']:
        """Get failed notifications."""
        return (
            db.query(cls)
            .filter(cls.success == False)
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def create_log(
        cls,
        db: Session,
        channel: str,
        event: str,
        success: bool,
        recipient: Optional[str] = None,
        subject: Optional[str] = None,
        message: Optional[str] = None,
        error_message: Optional[str] = None,
        file_entry_id: Optional[int] = None,
        batch_id: Optional[int] = None,
        extra_data: Optional[dict] = None
    ) -> 'NotificationLog':
        """Create a notification log entry."""
        log = cls(
            channel=channel,
            event=event,
            recipient=recipient,
            subject=subject,
            message=message,
            success=success,
            error_message=error_message,
            file_entry_id=file_entry_id,
            batch_id=batch_id,
            extra_data=extra_data
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"<NotificationLog(id={self.id}, channel={self.channel}, "
            f"event={self.event}, success={self.success})>"
        )
