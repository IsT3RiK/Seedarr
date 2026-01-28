"""
Notification Service

Centralized service for sending notifications across channels.

Features:
- Multi-channel support (Discord, Email)
- Event-based notification routing
- Logging of all notifications
- Settings-based configuration
"""

import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from app.models.notification import NotificationLog, NotificationChannel, NotificationEvent
from app.models.settings import Settings
from app.services.discord_client import DiscordClient
from app.services.email_client import EmailClient

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Service for sending notifications across configured channels.

    Automatically routes notifications based on event type and configuration.
    """

    def __init__(self, db: Session):
        """
        Initialize notification service.

        Args:
            db: Database session
        """
        self.db = db
        self._discord_client: Optional[DiscordClient] = None
        self._email_client: Optional[EmailClient] = None
        self._settings: Optional[Settings] = None

    def _get_settings(self) -> Settings:
        """Get and cache settings."""
        if not self._settings:
            self._settings = Settings.get_settings(self.db)
        return self._settings

    def _get_discord_client(self) -> Optional[DiscordClient]:
        """Get Discord client if configured."""
        if self._discord_client is not None:
            return self._discord_client

        settings = self._get_settings()
        webhook_url = getattr(settings, 'discord_webhook_url', None)

        if webhook_url:
            self._discord_client = DiscordClient(webhook_url)
            return self._discord_client

        return None

    def _get_email_client(self) -> Optional[EmailClient]:
        """Get Email client if configured."""
        if self._email_client is not None:
            return self._email_client

        settings = self._get_settings()
        smtp_host = getattr(settings, 'smtp_host', None)

        if smtp_host:
            self._email_client = EmailClient(
                smtp_host=smtp_host,
                smtp_port=getattr(settings, 'smtp_port', 587),
                smtp_username=getattr(settings, 'smtp_username', None),
                smtp_password=getattr(settings, 'smtp_password', None),
                smtp_from=getattr(settings, 'smtp_from', None),
                use_tls=getattr(settings, 'smtp_use_tls', True)
            )
            return self._email_client

        return None

    def _log_notification(
        self,
        channel: str,
        event: str,
        success: bool,
        recipient: Optional[str] = None,
        subject: Optional[str] = None,
        message: Optional[str] = None,
        error_message: Optional[str] = None,
        file_entry_id: Optional[int] = None,
        batch_id: Optional[int] = None
    ) -> NotificationLog:
        """Log a notification attempt."""
        return NotificationLog.create_log(
            db=self.db,
            channel=channel,
            event=event,
            success=success,
            recipient=recipient,
            subject=subject,
            message=message,
            error_message=error_message,
            file_entry_id=file_entry_id,
            batch_id=batch_id
        )

    async def notify_upload_success(
        self,
        release_name: str,
        tracker_name: str,
        torrent_url: Optional[str] = None,
        cover_url: Optional[str] = None,
        file_size: Optional[str] = None,
        file_entry_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send upload success notifications.

        Args:
            release_name: Name of the uploaded release
            tracker_name: Tracker name
            torrent_url: URL to the torrent
            cover_url: Cover image URL
            file_size: File size string
            file_entry_id: Related file entry ID

        Returns:
            Results from all channels
        """
        results = {'discord': None, 'email': None}
        settings = self._get_settings()

        # Discord notification
        discord = self._get_discord_client()
        if discord:
            try:
                result = await discord.send_upload_success(
                    release_name=release_name,
                    tracker_name=tracker_name,
                    torrent_url=torrent_url,
                    cover_url=cover_url,
                    file_size=file_size
                )
                results['discord'] = result

                self._log_notification(
                    channel=NotificationChannel.DISCORD.value,
                    event=NotificationEvent.UPLOAD_SUCCESS.value,
                    success=result.get('success', False),
                    recipient=discord.webhook_url,
                    subject=f"Upload Success: {release_name}",
                    error_message=result.get('error'),
                    file_entry_id=file_entry_id
                )
            except Exception as e:
                logger.error(f"Discord notification failed: {e}")
                results['discord'] = {'success': False, 'error': str(e)}

        # Email notification
        email = self._get_email_client()
        notification_email = getattr(settings, 'notification_email', None)
        if email and notification_email:
            try:
                result = email.send_upload_success(
                    to=notification_email,
                    release_name=release_name,
                    tracker_name=tracker_name,
                    torrent_url=torrent_url
                )
                results['email'] = result

                self._log_notification(
                    channel=NotificationChannel.EMAIL.value,
                    event=NotificationEvent.UPLOAD_SUCCESS.value,
                    success=result.get('success', False),
                    recipient=notification_email,
                    subject=f"Upload Success: {release_name}",
                    error_message=result.get('error'),
                    file_entry_id=file_entry_id
                )
            except Exception as e:
                logger.error(f"Email notification failed: {e}")
                results['email'] = {'success': False, 'error': str(e)}

        return results

    async def notify_upload_failed(
        self,
        release_name: str,
        tracker_name: str,
        error_message: str,
        file_entry_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send upload failure notifications.

        Args:
            release_name: Name of the release
            tracker_name: Tracker name
            error_message: Error description
            file_entry_id: Related file entry ID

        Returns:
            Results from all channels
        """
        results = {'discord': None, 'email': None}
        settings = self._get_settings()

        # Discord notification
        discord = self._get_discord_client()
        if discord:
            try:
                result = await discord.send_upload_failed(
                    release_name=release_name,
                    tracker_name=tracker_name,
                    error_message=error_message
                )
                results['discord'] = result

                self._log_notification(
                    channel=NotificationChannel.DISCORD.value,
                    event=NotificationEvent.UPLOAD_FAILED.value,
                    success=result.get('success', False),
                    recipient=discord.webhook_url,
                    subject=f"Upload Failed: {release_name}",
                    message=error_message,
                    error_message=result.get('error'),
                    file_entry_id=file_entry_id
                )
            except Exception as e:
                logger.error(f"Discord notification failed: {e}")
                results['discord'] = {'success': False, 'error': str(e)}

        # Email notification
        email = self._get_email_client()
        notification_email = getattr(settings, 'notification_email', None)
        if email and notification_email:
            try:
                result = email.send_upload_failed(
                    to=notification_email,
                    release_name=release_name,
                    tracker_name=tracker_name,
                    error_message=error_message
                )
                results['email'] = result

                self._log_notification(
                    channel=NotificationChannel.EMAIL.value,
                    event=NotificationEvent.UPLOAD_FAILED.value,
                    success=result.get('success', False),
                    recipient=notification_email,
                    subject=f"Upload Failed: {release_name}",
                    message=error_message,
                    error_message=result.get('error'),
                    file_entry_id=file_entry_id
                )
            except Exception as e:
                logger.error(f"Email notification failed: {e}")
                results['email'] = {'success': False, 'error': str(e)}

        return results

    async def notify_batch_complete(
        self,
        total: int,
        successful: int,
        failed: int,
        batch_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send batch completion notifications.

        Args:
            total: Total files in batch
            successful: Successful uploads
            failed: Failed uploads
            batch_id: Batch ID

        Returns:
            Results from all channels
        """
        results = {'discord': None, 'email': None}
        settings = self._get_settings()

        # Discord notification
        discord = self._get_discord_client()
        if discord:
            try:
                result = await discord.send_batch_complete(
                    total=total,
                    successful=successful,
                    failed=failed,
                    batch_id=batch_id
                )
                results['discord'] = result

                self._log_notification(
                    channel=NotificationChannel.DISCORD.value,
                    event=NotificationEvent.BATCH_COMPLETE.value,
                    success=result.get('success', False),
                    recipient=discord.webhook_url,
                    subject=f"Batch Complete: {successful}/{total}",
                    error_message=result.get('error'),
                    batch_id=batch_id
                )
            except Exception as e:
                logger.error(f"Discord notification failed: {e}")
                results['discord'] = {'success': False, 'error': str(e)}

        # Email notification
        email = self._get_email_client()
        notification_email = getattr(settings, 'notification_email', None)
        if email and notification_email:
            try:
                result = email.send_batch_complete(
                    to=notification_email,
                    total=total,
                    successful=successful,
                    failed=failed
                )
                results['email'] = result

                self._log_notification(
                    channel=NotificationChannel.EMAIL.value,
                    event=NotificationEvent.BATCH_COMPLETE.value,
                    success=result.get('success', False),
                    recipient=notification_email,
                    subject=f"Batch Complete: {successful}/{total}",
                    error_message=result.get('error'),
                    batch_id=batch_id
                )
            except Exception as e:
                logger.error(f"Email notification failed: {e}")
                results['email'] = {'success': False, 'error': str(e)}

        return results

    async def test_discord(self) -> Dict[str, Any]:
        """Test Discord webhook configuration."""
        discord = self._get_discord_client()
        if not discord:
            return {'success': False, 'error': 'Discord webhook not configured'}

        return await discord.test_webhook()

    def test_email(self) -> Dict[str, Any]:
        """Test email configuration."""
        email = self._get_email_client()
        if not email:
            return {'success': False, 'error': 'SMTP not configured'}

        return email.test_connection()

    def get_notification_logs(
        self,
        limit: int = 50,
        event: Optional[str] = None,
        failures_only: bool = False
    ) -> List[NotificationLog]:
        """
        Get notification logs.

        Args:
            limit: Maximum logs to return
            event: Filter by event type
            failures_only: Only return failed notifications

        Returns:
            List of notification logs
        """
        if failures_only:
            return NotificationLog.get_failures(self.db, limit)
        elif event:
            return NotificationLog.get_by_event(self.db, event, limit)
        else:
            return NotificationLog.get_recent(self.db, limit)


def get_notification_service(db: Session) -> NotificationService:
    """Get a notification service instance."""
    return NotificationService(db)
