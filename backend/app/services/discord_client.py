"""
Discord Webhook Client

Client for sending notifications via Discord webhooks.

Features:
- Rich embed support
- Color-coded messages by type
- Rate limiting
- Error handling
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


class DiscordClient:
    """
    Client for sending Discord webhook notifications.

    Supports rich embeds with color-coding based on notification type.
    """

    # Discord embed colors
    COLORS = {
        'success': 0x00FF00,  # Green
        'error': 0xFF0000,    # Red
        'warning': 0xFFA500,  # Orange
        'info': 0x0099FF,     # Blue
    }

    def __init__(self, webhook_url: str, timeout: float = 10.0):
        """
        Initialize Discord client.

        Args:
            webhook_url: Discord webhook URL
            timeout: Request timeout in seconds
        """
        self.webhook_url = webhook_url
        self.timeout = timeout

    async def send_message(
        self,
        content: Optional[str] = None,
        embed: Optional[Dict[str, Any]] = None,
        username: str = "Seedarr",
        avatar_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a message via Discord webhook.

        Args:
            content: Plain text content (optional if embed provided)
            embed: Discord embed object (optional)
            username: Bot username to display
            avatar_url: Bot avatar URL

        Returns:
            Dict with success status and any error message
        """
        if not self.webhook_url:
            return {'success': False, 'error': 'Webhook URL not configured'}

        payload = {
            'username': username,
        }

        if avatar_url:
            payload['avatar_url'] = avatar_url

        if content:
            payload['content'] = content

        if embed:
            payload['embeds'] = [embed]

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload
                )

                if response.status_code == 204:
                    logger.info("Discord notification sent successfully")
                    return {'success': True}
                elif response.status_code == 429:
                    # Rate limited
                    retry_after = response.json().get('retry_after', 1)
                    logger.warning(f"Discord rate limited. Retry after {retry_after}s")
                    return {
                        'success': False,
                        'error': f'Rate limited. Retry after {retry_after}s'
                    }
                else:
                    error_msg = f"Discord webhook failed: HTTP {response.status_code}"
                    logger.error(error_msg)
                    return {'success': False, 'error': error_msg}

        except httpx.TimeoutException:
            logger.error("Discord webhook timeout")
            return {'success': False, 'error': 'Request timeout'}
        except httpx.ConnectError as e:
            logger.error(f"Discord webhook connection error: {e}")
            return {'success': False, 'error': f'Connection error: {str(e)}'}
        except Exception as e:
            logger.error(f"Discord webhook error: {e}")
            return {'success': False, 'error': str(e)}

    def create_embed(
        self,
        title: str,
        description: Optional[str] = None,
        color_type: str = 'info',
        fields: Optional[List[Dict[str, Any]]] = None,
        footer: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a Discord embed object.

        Args:
            title: Embed title
            description: Embed description
            color_type: Color type (success, error, warning, info)
            fields: List of field dicts with name, value, inline keys
            footer: Footer text
            thumbnail_url: Thumbnail image URL
            url: URL to link title to

        Returns:
            Discord embed dictionary
        """
        embed = {
            'title': title,
            'color': self.COLORS.get(color_type, self.COLORS['info']),
            'timestamp': datetime.utcnow().isoformat()
        }

        if description:
            embed['description'] = description

        if url:
            embed['url'] = url

        if fields:
            embed['fields'] = fields

        if footer:
            embed['footer'] = {'text': footer}

        if thumbnail_url:
            embed['thumbnail'] = {'url': thumbnail_url}

        return embed

    async def send_upload_success(
        self,
        release_name: str,
        tracker_name: str,
        torrent_url: Optional[str] = None,
        cover_url: Optional[str] = None,
        file_size: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send upload success notification.

        Args:
            release_name: Name of the uploaded release
            tracker_name: Tracker the release was uploaded to
            torrent_url: URL to the torrent on tracker
            cover_url: Cover image URL
            file_size: File size string

        Returns:
            Send result
        """
        fields = [
            {'name': 'Release', 'value': release_name, 'inline': False},
            {'name': 'Tracker', 'value': tracker_name, 'inline': True},
        ]

        if file_size:
            fields.append({'name': 'Size', 'value': file_size, 'inline': True})

        embed = self.create_embed(
            title='Upload Successful',
            description=f'Successfully uploaded to {tracker_name}',
            color_type='success',
            fields=fields,
            footer='Seedarr v2.0',
            thumbnail_url=cover_url,
            url=torrent_url
        )

        return await self.send_message(embed=embed)

    async def send_upload_failed(
        self,
        release_name: str,
        tracker_name: str,
        error_message: str
    ) -> Dict[str, Any]:
        """
        Send upload failure notification.

        Args:
            release_name: Name of the release
            tracker_name: Tracker where upload failed
            error_message: Error description

        Returns:
            Send result
        """
        fields = [
            {'name': 'Release', 'value': release_name, 'inline': False},
            {'name': 'Tracker', 'value': tracker_name, 'inline': True},
            {'name': 'Error', 'value': error_message[:500], 'inline': False},
        ]

        embed = self.create_embed(
            title='Upload Failed',
            description=f'Failed to upload to {tracker_name}',
            color_type='error',
            fields=fields,
            footer='Seedarr v2.0'
        )

        return await self.send_message(embed=embed)

    async def send_batch_complete(
        self,
        total: int,
        successful: int,
        failed: int,
        batch_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send batch completion notification.

        Args:
            total: Total files in batch
            successful: Number of successful uploads
            failed: Number of failed uploads
            batch_id: Optional batch ID

        Returns:
            Send result
        """
        color_type = 'success' if failed == 0 else ('warning' if successful > 0 else 'error')

        fields = [
            {'name': 'Total', 'value': str(total), 'inline': True},
            {'name': 'Successful', 'value': str(successful), 'inline': True},
            {'name': 'Failed', 'value': str(failed), 'inline': True},
        ]

        description = f"Batch processing completed with {successful}/{total} successful uploads"
        if batch_id:
            description += f" (Batch #{batch_id})"

        embed = self.create_embed(
            title='Batch Processing Complete',
            description=description,
            color_type=color_type,
            fields=fields,
            footer='Seedarr v2.0'
        )

        return await self.send_message(embed=embed)

    async def test_webhook(self) -> Dict[str, Any]:
        """
        Test webhook connectivity.

        Returns:
            Test result with success status
        """
        embed = self.create_embed(
            title='Webhook Test',
            description='This is a test message from Seedarr.',
            color_type='info',
            footer='Webhook is working correctly!'
        )

        return await self.send_message(
            content='Webhook test successful!',
            embed=embed
        )
