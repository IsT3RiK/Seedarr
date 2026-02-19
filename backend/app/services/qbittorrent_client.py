"""
QBittorrent Client Service for Seedarr v2.5

Dedicated module for all qBittorrent Web API interactions.
Extracted from pipeline.py to provide a clean, reusable interface.

Features:
    - Authentication with session cookie management
    - Torrent injection with configurable save path, category, and tags
    - Tag management for existing torrents
    - Path mapping between Seedarr and qBittorrent mount points
    - Connection testing

API Reference: https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-4.1)
"""

import logging
import os
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)


class QBittorrentError(Exception):
    """Base exception for qBittorrent operations."""
    pass


class QBittorrentAuthError(QBittorrentError):
    """Authentication failed."""
    pass


class QBittorrentClient:
    """
    Client for qBittorrent Web API.

    Handles authentication, torrent injection, tag management, and path mapping
    between Seedarr's filesystem view and qBittorrent's filesystem view.

    Args:
        host: qBittorrent Web UI address (host:port or full URL)
        username: Web UI username
        password: Web UI password
        content_path: Base path as seen by qBittorrent (for Docker path mapping)
        seedarr_root: Base path as seen by Seedarr (for Docker path mapping)
    """

    def __init__(
        self,
        host: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        content_path: Optional[str] = None,
        seedarr_root: Optional[str] = None,
    ):
        # Ensure host has protocol
        if host and not host.startswith('http'):
            host = f"http://{host}"
        self.host = host
        self.username = username or ''
        self.password = password or ''
        self.content_path = (content_path or '').rstrip('/') or None
        self.seedarr_root = (seedarr_root or '/media').rstrip('/')

    def map_path(self, save_path: str) -> str:
        """
        Translate a Seedarr-internal path to qBittorrent's path.

        Handles Docker-to-Docker mapping (e.g., Seedarr /media -> qBit /data)
        and Docker-to-host scenarios.

        Args:
            save_path: Path as seen by Seedarr

        Returns:
            Path as seen by qBittorrent
        """
        if not self.content_path:
            return save_path

        if save_path.startswith(self.seedarr_root):
            relative_path = save_path[len(self.seedarr_root):].lstrip('/')
            mapped = f"{self.content_path}/{relative_path}" if relative_path else self.content_path
            logger.info(f"Path mapping ({self.seedarr_root} -> {self.content_path}): {mapped}")
            return mapped

        return save_path

    async def inject_torrent(
        self,
        torrent_path: str,
        save_path: str,
        category: str = "TP",
        tags: Optional[str] = None,
        skip_checking: bool = True,
        paused: bool = False,
    ) -> Dict[str, Any]:
        """
        Inject a .torrent file into qBittorrent for seeding.

        Args:
            torrent_path: Path to the .torrent file on disk
            save_path: Directory where the content is located (for seeding)
            category: qBittorrent category (default: "TP")
            tags: Comma-separated tags (e.g., "LACALE")
            skip_checking: Skip hash verification (True when file already exists at save_path)
            paused: Start in paused state

        Returns:
            Dict with 'success', 'message', and 'already_exists' keys

        Raises:
            QBittorrentError: On connection or API errors
            QBittorrentAuthError: On authentication failure
        """
        if not self.host:
            raise QBittorrentError("qBittorrent not configured: host is empty")

        if not torrent_path or not os.path.exists(torrent_path):
            raise QBittorrentError(f"Torrent file not found: {torrent_path}")

        # Apply path mapping
        mapped_save_path = self.map_path(save_path)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Authenticate
                cookies = await self._authenticate(client)

                # Read torrent file
                with open(torrent_path, 'rb') as f:
                    torrent_data = f.read()

                logger.info(
                    f"Injecting torrent to qBittorrent: category={category}, "
                    f"save_path={mapped_save_path}, tags={tags}"
                )

                # Build request
                add_data = {
                    "savepath": mapped_save_path,
                    "category": category,
                    "skip_checking": "true" if skip_checking else "false",
                    "paused": "true" if paused else "false",
                    "autoTMM": "false",
                }
                if tags:
                    add_data["tags"] = tags

                # Send torrent
                response = await client.post(
                    f"{self.host}/api/v2/torrents/add",
                    cookies=cookies,
                    files={"torrents": ("torrent.torrent", torrent_data, "application/x-bittorrent")},
                    data=add_data,
                )

                if response.text == "Ok.":
                    tag_info = f" with tag {tags}" if tags else ""
                    logger.info(f"Torrent injected to qBittorrent (category={category}{tag_info})")
                    return {'success': True, 'message': 'Torrent added', 'already_exists': False}

                # Handle "already exists"
                response_lower = response.text.lower()
                if "already" in response_lower or response_lower == "fails.":
                    logger.warning(f"Torrent already exists in qBittorrent: {response.text}")

                    # Try to add tag to existing torrent
                    if tags:
                        await self._add_tag_to_existing(client, cookies, torrent_path, tags)

                    return {'success': True, 'message': 'Torrent already exists (tag updated)', 'already_exists': True}

                raise QBittorrentError(f"Failed to add torrent: {response.text}")

        except httpx.HTTPError as e:
            raise QBittorrentError(f"qBittorrent connection error: {e}") from e

    async def test_connection(self) -> Dict[str, Any]:
        """
        Test connectivity to qBittorrent.

        Returns:
            Dict with 'success', 'message', and optionally 'version'
        """
        if not self.host:
            return {'success': False, 'message': 'qBittorrent host not configured'}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                cookies = await self._authenticate(client)

                # Get version info
                version_response = await client.get(
                    f"{self.host}/api/v2/app/version",
                    cookies=cookies,
                )
                version = version_response.text if version_response.status_code == 200 else 'unknown'

                return {
                    'success': True,
                    'message': f'Connected to qBittorrent {version}',
                    'version': version,
                }

        except QBittorrentAuthError as e:
            return {'success': False, 'message': str(e)}
        except httpx.HTTPError as e:
            return {'success': False, 'message': f'Connection failed: {e}'}
        except Exception as e:
            return {'success': False, 'message': f'Error: {e}'}

    async def _authenticate(self, client: httpx.AsyncClient) -> httpx.Cookies:
        """
        Authenticate with qBittorrent and return session cookies.

        Args:
            client: httpx AsyncClient instance

        Returns:
            Session cookies for subsequent requests

        Raises:
            QBittorrentAuthError: If authentication fails
        """
        logger.debug(f"Authenticating with qBittorrent at {self.host}")

        response = await client.post(
            f"{self.host}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
        )

        if response.text != "Ok.":
            raise QBittorrentAuthError(f"qBittorrent authentication failed: {response.text}")

        logger.debug("Authenticated with qBittorrent")
        return response.cookies

    async def _add_tag_to_existing(
        self,
        client: httpx.AsyncClient,
        cookies: httpx.Cookies,
        torrent_path: str,
        tags: str,
    ) -> None:
        """
        Add tags to an existing torrent (identified by info hash from .torrent file).

        Args:
            client: Authenticated httpx client
            cookies: Session cookies
            torrent_path: Path to .torrent file (used to extract info hash)
            tags: Tags to add
        """
        try:
            import torf
            t = torf.Torrent.read(torrent_path)
            torrent_hash = str(t.infohash).lower()

            await client.post(
                f"{self.host}/api/v2/torrents/addTags",
                cookies=cookies,
                data={"hashes": torrent_hash, "tags": tags},
            )
            logger.info(f"Added tag '{tags}' to existing torrent {torrent_hash[:8]}...")

        except Exception as e:
            logger.warning(f"Could not add tag to existing torrent: {e}")


def get_qbittorrent_client_from_settings(settings) -> Optional[QBittorrentClient]:
    """
    Create a QBittorrentClient from a Settings model instance.

    Args:
        settings: Settings model instance

    Returns:
        QBittorrentClient if qBittorrent is configured, None otherwise
    """
    if not settings or not settings.qbittorrent_host:
        return None

    return QBittorrentClient(
        host=settings.qbittorrent_host,
        username=settings.qbittorrent_username,
        password=settings.qbittorrent_password,
        content_path=settings.qbittorrent_content_path,
        seedarr_root=settings.input_media_path,
    )
