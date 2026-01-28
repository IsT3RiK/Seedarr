"""
C411Adapter - TrackerAdapter Implementation for C411 Tracker

This module implements the TrackerAdapter interface for the C411 private
tracker. Unlike La Cale, C411 uses Bearer token authentication and does
not require Cloudflare bypass.

Architecture:
    TrackerAdapter (interface)
          |
    C411Adapter (concrete implementation)
          |
          └── C411Client (C411 API business logic)

Key Features:
    - Bearer token authentication (no FlareSolverr needed)
    - API key instead of passkey for authentication
    - Subcategory support (instead of tags)
    - Simpler authentication flow

API Specification:
    - Upload: POST /api/torrents with Bearer token
    - Announce URL: /announce/{passkey}

Usage Example:
    adapter = C411Adapter(
        tracker_url="https://c411.org",
        api_key="your_api_key",
        passkey="your_passkey"
    )

    # Authenticate (validates API key)
    authenticated = await adapter.authenticate()

    # Upload torrent
    result = await adapter.upload_torrent(
        torrent_data=torrent_bytes,
        release_name="Movie.2023.1080p.WEB.EAC3.x264-TP",
        category_id="1",
        tag_ids=[],  # Not used, uses subcategory instead
        nfo_data=nfo_bytes,
        subcategory_id="10"
    )
"""

import logging
from typing import Dict, List, Optional, Any

from .tracker_adapter import TrackerAdapter
from ..services.c411_client import C411Client
from ..services.exceptions import (
    TrackerAPIError,
    NetworkRetryableError
)

logger = logging.getLogger(__name__)


class C411Adapter(TrackerAdapter):
    """
    C411 tracker adapter implementing TrackerAdapter interface.

    This adapter handles C411 tracker integration:
    - Bearer token authentication (API key)
    - Torrent uploads with category/subcategory
    - No Cloudflare bypass required

    Attributes:
        tracker_url: C411 tracker base URL
        api_key: API key for Bearer authentication
        passkey: Passkey for announce URL
        client: C411Client instance
        authenticated: Whether authentication succeeded

    Note: C411 uses subcategory_id instead of tag_ids for categorization.
    """

    def __init__(
        self,
        tracker_url: str,
        api_key: str,
        passkey: Optional[str] = None,
        default_category_id: Optional[str] = None,
        default_subcategory_id: Optional[str] = None,
        timeout: int = 60
    ):
        """
        Initialize C411Adapter.

        Args:
            tracker_url: C411 tracker base URL (e.g., https://c411.org)
            api_key: API key for Bearer token authentication
            passkey: Passkey for announce URL
            default_category_id: Default category ID for uploads
            default_subcategory_id: Default subcategory ID for uploads
            timeout: HTTP request timeout in seconds
        """
        self.tracker_url = tracker_url
        self.api_key = api_key
        self.passkey = passkey
        self.default_category_id = default_category_id
        self.default_subcategory_id = default_subcategory_id

        # Initialize C411 API client
        self.client = C411Client(
            tracker_url=tracker_url,
            api_key=api_key,
            passkey=passkey,
            default_category_id=default_category_id,
            default_subcategory_id=default_subcategory_id,
            timeout=timeout
        )

        # Authentication state
        self.authenticated = False

        logger.info(
            f"C411Adapter initialized for tracker: {tracker_url}"
        )

    async def authenticate(self) -> bool:
        """
        Authenticate with C411 tracker.

        This method validates the API key by making a test request.
        No Cloudflare bypass is needed for C411.

        Returns:
            True if authentication successful, False otherwise

        Raises:
            TrackerAPIError: If authentication fails due to invalid API key
            NetworkRetryableError: If network issues occur
        """
        logger.info(f"Authenticating with C411 tracker: {self.tracker_url}")

        try:
            is_valid = await self.client.validate_api_key()

            if not is_valid:
                logger.error("C411 authentication failed: Invalid API key")
                raise TrackerAPIError(
                    "Invalid API key - authentication rejected by C411",
                    status_code=401
                )

            self.authenticated = True
            logger.info("Successfully authenticated with C411 tracker")
            return True

        except TrackerAPIError:
            raise

        except NetworkRetryableError:
            raise

        except Exception as e:
            error_msg = f"Unexpected error during C411 authentication: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    async def upload_torrent(
        self,
        torrent_data: bytes,
        release_name: str,
        category_id: str,
        tag_ids: List[str],
        nfo_data: bytes,
        description: Optional[str] = None,
        tmdb_id: Optional[str] = None,
        tmdb_type: Optional[str] = None,
        cover_url: Optional[str] = None,
        subcategory_id: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        tmdb_data: Optional[Dict[str, Any]] = None,
        rawg_data: Optional[Dict[str, Any]] = None,
        is_exclusive: bool = False,
        uploader_note: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Upload a .torrent file with metadata to C411 tracker.

        Note: C411 uses subcategory_id instead of tag_ids.
        The tag_ids parameter is ignored.

        Args:
            torrent_data: Raw .torrent file bytes
            release_name: Release name/title for the torrent
            category_id: Tracker category ID
            tag_ids: List of tag IDs (IGNORED - C411 uses subcategory)
            nfo_data: NFO file content as bytes
            description: Optional description/plot summary (BBCode or HTML)
            tmdb_id: Optional TMDB ID for metadata (deprecated, use tmdb_data)
            tmdb_type: Optional TMDB type (movie or tv)
            cover_url: Optional cover image URL
            subcategory_id: C411 subcategory ID (required)
            options: C411 options dict with optionTypeId -> optionValueId mappings
                Example: {"1": [4], "2": 25, "7": 121, "6": 96}
            tmdb_data: Full TMDB metadata as dict (for movies/TV shows)
            rawg_data: RAWG metadata as dict (for games)
            is_exclusive: True for exclusive releases
            uploader_note: Note for moderators
            **kwargs: Additional parameters

        Returns:
            Dictionary with upload result

        Raises:
            TrackerAPIError: If upload fails
            NetworkRetryableError: If network issues occur
        """
        logger.info(f"Uploading torrent to C411: {release_name}")

        # Ensure we're authenticated
        if not self.authenticated:
            logger.warning("Not authenticated, authenticating now...")
            await self.authenticate()

        # Use default subcategory if not provided
        subcategory_id = subcategory_id or self.default_subcategory_id

        if not subcategory_id:
            # Try to get from kwargs
            subcategory_id = kwargs.get('subcategory_id') or kwargs.get('default_subcategory_id')

        if not subcategory_id:
            raise TrackerAPIError(
                "subcategory_id is required for C411 uploads"
            )

        try:
            result = await self.client.upload_torrent(
                torrent_data=torrent_data,
                release_name=release_name,
                category_id=category_id,
                subcategory_id=subcategory_id,
                nfo_data=nfo_data,
                description=description,
                options=options,
                tmdb_data=tmdb_data,
                rawg_data=rawg_data,
                is_exclusive=is_exclusive,
                uploader_note=uploader_note
            )

            logger.info(
                f"Successfully uploaded torrent to C411: {release_name} "
                f"(ID: {result.get('torrent_id')})"
            )

            return result

        except (TrackerAPIError, NetworkRetryableError):
            raise

        except Exception as e:
            error_msg = f"Unexpected error during C411 upload: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    async def validate_credentials(self) -> bool:
        """
        Validate C411 credentials (API key).

        Returns:
            True if credentials are valid, False otherwise

        Raises:
            NetworkRetryableError: If network issues occur
        """
        logger.info("Validating C411 credentials")

        # Basic validation: API key must be non-empty
        if not self.api_key or len(self.api_key) < 10:
            logger.warning(
                f"API key validation failed: Invalid format "
                f"(length: {len(self.api_key) if self.api_key else 0})"
            )
            return False

        try:
            return await self.client.validate_api_key()
        except NetworkRetryableError:
            raise
        except Exception as e:
            logger.error(f"Error validating C411 credentials: {e}")
            return False

    async def get_tags(self) -> List[Dict[str, Any]]:
        """
        Fetch available tags from C411 tracker.

        Note: C411 doesn't use tags like La Cale. It uses subcategories instead.
        This method returns an empty list for compatibility.

        Returns:
            Empty list (C411 doesn't use tags)
        """
        logger.info("C411 doesn't use tags, returning empty list")
        return []

    async def get_categories(self) -> List[Dict[str, Any]]:
        """
        Fetch available categories from C411 tracker.

        Returns:
            List of category dictionaries

        Raises:
            NetworkRetryableError: If network issues occur
            TrackerAPIError: If API returns error
        """
        logger.info("Fetching categories from C411 tracker")

        try:
            categories = await self.client.get_categories()
            logger.info(f"Successfully fetched {len(categories)} categories from C411")
            return categories

        except (TrackerAPIError, NetworkRetryableError):
            raise

        except Exception as e:
            error_msg = f"Unexpected error fetching C411 categories: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    async def check_duplicate(
        self,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        release_name: Optional[str] = None,
        quality: Optional[str] = None,
        file_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Check if a release already exists on C411 tracker.

        Uses the Torznab API for searching (same API used by Prowlarr/Sonarr/Radarr).
        Search strategy: TMDB ID -> IMDB ID -> Release name

        Args:
            tmdb_id: TMDB ID to search for
            imdb_id: IMDB ID to search for
            release_name: Release name to search for
            quality: Quality to filter results (optional)
            file_size: File size in bytes for exact match detection (optional)

        Returns:
            Dictionary with is_duplicate, existing_torrents, search_method, message,
            exact_match (bool), and exact_matches (list of exact size matches)
        """
        logger.info(f"Checking for duplicates on C411: tmdb={tmdb_id}, imdb={imdb_id}, name={release_name}, size={file_size}")

        try:
            existing_torrents = []
            search_method = "none"

            # Strategy 1: Search by TMDB ID
            if tmdb_id:
                logger.debug(f"Searching by TMDB ID: {tmdb_id}")
                results = await self.client.search_torrents(
                    query=str(tmdb_id),
                    search_type="tmdb"
                )
                if results:
                    existing_torrents = results
                    search_method = "tmdb"
                    logger.info(f"Found {len(results)} torrents by TMDB ID")

            # Strategy 2: Search by IMDB ID (fallback)
            if not existing_torrents and imdb_id:
                logger.debug(f"Searching by IMDB ID: {imdb_id}")
                results = await self.client.search_torrents(
                    query=str(imdb_id),
                    search_type="imdb"
                )
                if results:
                    existing_torrents = results
                    search_method = "imdb"
                    logger.info(f"Found {len(results)} torrents by IMDB ID")

            # Strategy 3: Search by release name (final fallback)
            if not existing_torrents and release_name:
                import re
                # Extract title from release name - handle both "Title.2019" and "Title (2019)" formats
                title_match = re.match(r'^(.+?)[\.\s\(]+(19|20)\d{2}', release_name)
                if title_match:
                    search_query = title_match.group(1).replace('.', ' ').strip()
                else:
                    # Fallback: just take first few words
                    search_query = ' '.join(release_name.replace('.', ' ').split()[:3])

                search_query = search_query[:50]

                logger.debug(f"Searching by name: {search_query}")
                results = await self.client.search_torrents(
                    query=search_query,
                    search_type="name"
                )
                if results:
                    existing_torrents = results
                    search_method = "name"
                    logger.info(f"Found {len(results)} torrents by name search")

            # Filter by quality if specified
            if existing_torrents and quality:
                quality_lower = quality.lower()
                filtered = [t for t in existing_torrents if quality_lower in t.get('name', '').lower()]
                if filtered:
                    existing_torrents = filtered
                    logger.info(f"Filtered to {len(filtered)} torrents matching quality {quality}")

            # Check for exact matches by file size (with 1% tolerance)
            exact_matches = []
            logger.info(f"Size comparison: local_file_size={file_size}, existing_torrents={len(existing_torrents)}")

            if existing_torrents and file_size and file_size > 0:
                tolerance = 0.01  # 1% tolerance
                min_size = int(file_size * (1 - tolerance))
                max_size = int(file_size * (1 + tolerance))
                logger.info(f"Size range for exact match: {min_size} - {max_size} bytes")

                for torrent in existing_torrents:
                    torrent_size = torrent.get('size', 0)
                    logger.info(f"Comparing: '{torrent.get('name')}' size={torrent_size} vs local={file_size}")
                    if torrent_size and min_size <= torrent_size <= max_size:
                        exact_matches.append(torrent)
                        logger.info(f"EXACT MATCH: {torrent.get('name')} (size: {torrent_size} vs {file_size})")
                    elif torrent_size:
                        size_diff_pct = abs(torrent_size - file_size) / file_size * 100
                        logger.info(f"Size mismatch: {size_diff_pct:.1f}% difference")
            else:
                logger.warning(f"Cannot compare sizes: file_size={file_size}, torrents={len(existing_torrents)}")

            is_duplicate = len(existing_torrents) > 0
            has_exact_match = len(exact_matches) > 0

            if has_exact_match:
                message = f"⚠️ EXACT MATCH: Found {len(exact_matches)} torrent(s) with same size!"
            elif is_duplicate:
                message = f"Found {len(existing_torrents)} similar release(s) (different quality/size)"
            else:
                message = "No duplicates found - safe to upload"

            logger.info(f"Duplicate check result: is_duplicate={is_duplicate}, exact_match={has_exact_match}, method={search_method}")

            return {
                'is_duplicate': is_duplicate,
                'exact_match': has_exact_match,
                'exact_matches': exact_matches,
                'existing_torrents': existing_torrents,
                'search_method': search_method,
                'message': message
            }

        except (TrackerAPIError, NetworkRetryableError):
            raise

        except Exception as e:
            error_msg = f"Unexpected error checking C411 duplicates: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform health check on C411 adapter.

        Returns:
            Dictionary with health check results
        """
        logger.info("Performing health check on C411Adapter")

        health_status = {
            'healthy': True,
            'tracker_reachable': False,
            'credentials_valid': False,
            'flaresolverr_available': True,  # Not needed for C411
            'circuit_breaker_state': 'not_applicable',
            'details': {}
        }

        try:
            credentials_valid = await self.validate_credentials()
            health_status['credentials_valid'] = credentials_valid
            health_status['tracker_reachable'] = True

            if not credentials_valid:
                health_status['healthy'] = False
                health_status['details']['credentials'] = 'Invalid API key'

        except NetworkRetryableError as e:
            health_status['healthy'] = False
            health_status['tracker_reachable'] = False
            health_status['details']['tracker'] = f'Network error: {e}'

        except Exception as e:
            health_status['healthy'] = False
            health_status['details']['tracker'] = f'Error: {e}'

        logger.info(
            f"C411 health check complete: "
            f"healthy={health_status['healthy']}, "
            f"tracker={health_status['tracker_reachable']}, "
            f"credentials={health_status['credentials_valid']}"
        )

        return health_status

    def get_adapter_info(self) -> Dict[str, Any]:
        """
        Get information about this tracker adapter.

        Returns:
            Dictionary with adapter information
        """
        return {
            'name': 'C411 Adapter',
            'tracker_name': 'C411',
            'tracker_url': self.tracker_url,
            'version': '1.0.0',
            'features': [
                'bearer_auth',
                'nfo_upload',
                'subcategories',
                'no_cloudflare'
            ]
        }

    def __repr__(self) -> str:
        """String representation of C411Adapter."""
        return (
            f"<C411Adapter("
            f"tracker_url='{self.tracker_url}', "
            f"api_key='***{self.api_key[-4:] if self.api_key else 'None'}', "
            f"authenticated={self.authenticated}"
            f")>"
        )
