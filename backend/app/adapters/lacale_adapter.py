"""
LaCaleAdapter - TrackerAdapter Implementation for "La Cale" Tracker

This module implements the TrackerAdapter interface for the "La Cale" French private
tracker. It composes CloudflareSessionManager and LaCaleClient to provide a complete
tracker integration following the adapter pattern.

Architecture:
    TrackerAdapter (interface)
          ↑
          |
    LaCaleAdapter (concrete implementation)
          |
          ├── CloudflareSessionManager (Cloudflare bypass, session management)
          └── LaCaleClient (La Cale API business logic)

Key Features:
    - Cloudflare bypass using FlareSolverr
    - Multipart upload with CRITICAL repeated tags fields pattern
    - Passkey authentication
    - Circuit breaker for FlareSolverr failures
    - Typed exception handling with retry logic
    - Tag and category fetching

CRITICAL Implementation Notes:
    - Tags MUST be sent as repeated form fields, NOT JSON arrays
    - Torrent files MUST include source="lacale" flag
    - FlareSolverr is mandatory for Cloudflare bypass
    - NFO content is required for uploads

Usage Example:
    adapter = LaCaleAdapter(
        flaresolverr_url="http://localhost:8191",
        tracker_url="https://lacale.example.com",
        passkey="your_passkey_here"
    )

    # Authenticate with tracker
    authenticated = await adapter.authenticate()

    # Upload torrent
    result = await adapter.upload_torrent(
        torrent_data=torrent_bytes,
        release_name="Movie.2023.1080p.BluRay.x264",
        category_id="1",
        tag_ids=["10", "15", "20"],
        nfo_content="NFO content here"
    )
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from requests import Session

from .tracker_adapter import TrackerAdapter
from ..services.cloudflare_session_manager import CloudflareSessionManager
from ..services.lacale_client import LaCaleClient
from ..services.exceptions import (
    TrackerAPIError,
    CloudflareBypassError,
    NetworkRetryableError
)

logger = logging.getLogger(__name__)


class LaCaleAdapter(TrackerAdapter):
    """
    La Cale tracker adapter implementing TrackerAdapter interface.

    This adapter composes CloudflareSessionManager and LaCaleClient to provide
    complete La Cale tracker integration. It handles:
        - Cloudflare bypass authentication via FlareSolverr
        - Session cookie management and persistence
        - Torrent uploads with La Cale-specific format requirements
        - Tag and category fetching
        - Credential validation
        - Circuit breaker for FlareSolverr failures

    Attributes:
        flaresolverr_url: FlareSolverr service URL
        tracker_url: La Cale tracker base URL
        passkey: User's tracker passkey for authentication
        session_manager: CloudflareSessionManager instance
        client: LaCaleClient instance
        authenticated_session: Cached authenticated requests.Session
    """

    def __init__(
        self,
        flaresolverr_url: str,
        tracker_url: str,
        passkey: str,
        flaresolverr_timeout: int = 60000
    ):
        """
        Initialize LaCaleAdapter.

        Args:
            flaresolverr_url: FlareSolverr service URL (e.g., http://localhost:8191)
            tracker_url: La Cale tracker base URL (e.g., https://lacale.example.com)
            passkey: User's tracker passkey for authentication
            flaresolverr_timeout: FlareSolverr request timeout in milliseconds (default: 60000)
        """
        self.flaresolverr_url = flaresolverr_url
        self.tracker_url = tracker_url
        self.passkey = passkey

        # Initialize session manager for Cloudflare bypass
        self.session_manager = CloudflareSessionManager(
            flaresolverr_url=flaresolverr_url,
            max_timeout=flaresolverr_timeout
        )

        # Initialize La Cale API client
        self.client = LaCaleClient(
            tracker_url=tracker_url,
            passkey=passkey
        )

        # Cached authenticated session
        self.authenticated_session: Optional[Session] = None

        logger.info(
            f"LaCaleAdapter initialized for tracker: {tracker_url} "
            f"with FlareSolverr: {flaresolverr_url}"
        )

    async def authenticate(self) -> bool:
        """
        Authenticate with La Cale tracker and establish a session.

        This method:
            1. Uses FlareSolverr to bypass Cloudflare challenge
            2. Extracts authentication cookies from FlareSolverr response
            3. Creates and caches an authenticated requests.Session
            4. Verifies authentication succeeded

        Returns:
            True if authentication successful, False otherwise

        Raises:
            CloudflareBypassError: If Cloudflare bypass fails (retryable)
            TrackerAPIError: If authentication fails due to invalid credentials (non-retryable)
            NetworkRetryableError: If network/connectivity issues occur (retryable)

        Example:
            adapter = LaCaleAdapter(...)
            authenticated = await adapter.authenticate()
            if authenticated:
                # Proceed with uploads
                pass
        """
        logger.info(f"Authenticating with La Cale tracker: {self.tracker_url}")

        try:
            # Get authenticated session from CloudflareSessionManager
            # This handles FlareSolverr communication and cookie extraction
            self.authenticated_session = await self.session_manager.get_session(
                tracker_url=self.tracker_url
            )

            # Verify authentication by validating passkey
            is_valid = await self.client.validate_passkey(self.authenticated_session)

            if not is_valid:
                logger.error("Authentication failed: Invalid passkey")
                raise TrackerAPIError(
                    "Invalid passkey - authentication rejected by tracker",
                    status_code=403
                )

            logger.info("Successfully authenticated with La Cale tracker")
            return True

        except CloudflareBypassError as e:
            logger.error(f"Cloudflare bypass failed during authentication: {e}")
            raise

        except TrackerAPIError as e:
            logger.error(f"Tracker authentication failed: {e}")
            raise

        except NetworkRetryableError as e:
            logger.error(f"Network error during authentication: {e}")
            raise

        except Exception as e:
            error_msg = f"Unexpected error during authentication: {type(e).__name__}: {e}"
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
        **kwargs
    ) -> Dict[str, Any]:
        """
        Upload a .torrent file with metadata to La Cale tracker.

        This method prepares multipart form data according to La Cale's API format
        (including CRITICAL repeated tags fields) and executes the upload using the
        authenticated session.

        CRITICAL Implementation Notes:
            - Tags MUST be sent as REPEATED form fields, not JSON arrays
            - NFO content is required by La Cale tracker
            - Authenticated session must be established first (call authenticate())

        Args:
            torrent_data: Raw .torrent file bytes
            release_name: Release name/title for the torrent
            category_id: Tracker category ID (e.g., "1" for Movies)
            tag_ids: List of tracker tag IDs to apply (e.g., ["10", "15", "20"])
            nfo_data: NFO file content as bytes (required, min 50 chars)
            description: Optional description/plot summary
            tmdb_id: Optional TMDB ID for metadata
            tmdb_type: Optional TMDB type (movie or tv)
            cover_url: Optional cover image URL
            **kwargs: Additional tracker-specific parameters

        Returns:
            Dictionary with upload result containing:
                {
                    'success': bool,
                    'torrent_id': str,  # Tracker's torrent ID
                    'torrent_url': str,  # URL to view torrent on tracker
                    'message': str,  # Success/error message
                    'response_data': dict  # Full tracker response for debugging
                }

        Raises:
            TrackerAPIError: If upload fails due to invalid data (non-retryable)
            NetworkRetryableError: If network/connectivity issues occur (retryable)

        Example:
            result = await adapter.upload_torrent(
                torrent_data=torrent_bytes,
                release_name="Movie.2023.1080p.BluRay.x264",
                category_id="1",
                tag_ids=["10", "15", "20"],
                nfo_data=nfo_bytes
            )

            if result['success']:
                print(f"Uploaded successfully: {result['torrent_url']}")
        """
        logger.info(f"Uploading torrent to La Cale: {release_name}")

        # Ensure we have an authenticated session
        if not self.authenticated_session:
            logger.warning("No authenticated session found, authenticating now...")
            await self.authenticate()

        # Validate required session
        if not self.authenticated_session:
            raise TrackerAPIError(
                "Authentication required before upload - call authenticate() first"
            )

        # Delegate upload to LaCaleClient
        # Client handles multipart preparation with repeated tags fields
        try:
            result = await self.client.upload_torrent(
                session=self.authenticated_session,
                torrent_data=torrent_data,
                release_name=release_name,
                category_id=category_id,
                tag_ids=tag_ids,
                nfo_data=nfo_data,
                description=description,
                tmdb_id=tmdb_id,
                tmdb_type=tmdb_type,
                cover_url=cover_url
            )

            logger.info(
                f"Successfully uploaded torrent: {release_name} "
                f"(ID: {result.get('torrent_id')})"
            )

            return result

        except (TrackerAPIError, NetworkRetryableError):
            # Re-raise typed exceptions
            raise

        except Exception as e:
            error_msg = f"Unexpected error during torrent upload: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    async def validate_credentials(self) -> bool:
        """
        Validate tracker credentials (passkey) without full authentication.

        This method performs a lightweight check of credentials by:
            1. Verifying passkey is in valid format (non-empty string)
            2. Optionally pinging tracker API to verify passkey works

        Useful for:
            - Settings validation in admin UI
            - Health checks
            - Startup verification

        Returns:
            True if credentials are valid, False otherwise

        Raises:
            NetworkRetryableError: If network issues prevent validation (retryable)

        Example:
            adapter = LaCaleAdapter(passkey="abc123", ...)
            if await adapter.validate_credentials():
                print("Credentials are valid")
            else:
                print("Invalid credentials")
        """
        logger.info("Validating La Cale credentials")

        # Basic validation: passkey must be non-empty
        if not self.passkey or len(self.passkey) < 10:
            logger.warning(
                f"Passkey validation failed: Invalid format "
                f"(length: {len(self.passkey) if self.passkey else 0})"
            )
            return False

        try:
            # Try to authenticate to verify credentials actually work
            # This is more thorough than just format validation
            await self.authenticate()
            logger.info("Credentials validated successfully")
            return True

        except TrackerAPIError as e:
            # Authentication errors indicate invalid credentials
            if e.status_code in (401, 403):
                logger.warning(f"Credentials validation failed: {e}")
                return False
            # Other errors might be temporary, consider valid
            logger.warning(f"Unable to fully validate credentials due to: {e}")
            return True  # Assume valid if format is ok but service is down

        except (CloudflareBypassError, NetworkRetryableError) as e:
            # Network/service issues - can't validate but credentials might be ok
            logger.warning(f"Unable to validate credentials due to connectivity: {e}")
            # Re-raise to signal retryable error
            raise

        except Exception as e:
            logger.error(f"Unexpected error during credential validation: {e}")
            return False

    async def get_tags(self) -> List[Dict[str, Any]]:
        """
        Fetch available tag IDs and labels from La Cale tracker.

        This method retrieves the current list of tags from the tracker API.
        Tags are used to categorize torrents (e.g., "BluRay", "1080p", "French Audio").

        Returns:
            List of tag dictionaries:
                [
                    {
                        'tag_id': str,       # Tag ID to use in uploads
                        'label': str,        # Human-readable label
                        'category': str,     # Tag category/group
                        'description': str   # Tag description
                    },
                    ...
                ]

        Raises:
            NetworkRetryableError: If network issues occur (retryable)
            TrackerAPIError: If API returns error (non-retryable)

        Example:
            tags = await adapter.get_tags()
            for tag in tags:
                print(f"{tag['label']}: {tag['tag_id']}")
        """
        logger.info("Fetching tags from La Cale tracker")

        # Ensure we have an authenticated session
        if not self.authenticated_session:
            logger.warning("No authenticated session found, authenticating now...")
            await self.authenticate()

        if not self.authenticated_session:
            raise TrackerAPIError("Authentication required to fetch tags")

        # Delegate to LaCaleClient
        try:
            tags = await self.client.get_tags(self.authenticated_session)
            logger.info(f"Successfully fetched {len(tags)} tags from La Cale")
            return tags

        except (TrackerAPIError, NetworkRetryableError):
            # Re-raise typed exceptions
            raise

        except Exception as e:
            error_msg = f"Unexpected error fetching tags: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    async def get_categories(self) -> List[Dict[str, Any]]:
        """
        Fetch available categories from La Cale tracker.

        Categories represent the main content types (e.g., Movies, TV Shows, Music).

        Returns:
            List of category dictionaries:
                [
                    {
                        'category_id': str,  # Category ID to use in uploads
                        'name': str,         # Category name
                        'description': str   # Category description
                    },
                    ...
                ]

        Raises:
            NetworkRetryableError: If network issues occur (retryable)
            TrackerAPIError: If API returns error (non-retryable)

        Example:
            categories = await adapter.get_categories()
            for cat in categories:
                print(f"{cat['name']}: {cat['category_id']}")
        """
        logger.info("Fetching categories from La Cale tracker")

        # Ensure we have an authenticated session
        if not self.authenticated_session:
            logger.warning("No authenticated session found, authenticating now...")
            await self.authenticate()

        if not self.authenticated_session:
            raise TrackerAPIError("Authentication required to fetch categories")

        # Delegate to LaCaleClient
        try:
            categories = await self.client.get_categories(self.authenticated_session)
            logger.info(f"Successfully fetched {len(categories)} categories from La Cale")
            return categories

        except (TrackerAPIError, NetworkRetryableError):
            # Re-raise typed exceptions
            raise

        except Exception as e:
            error_msg = f"Unexpected error fetching categories: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    async def check_duplicate(
        self,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        release_name: Optional[str] = None,
        quality: Optional[str] = None,
        file_size: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Check if a release already exists on La Cale tracker.

        Uses cascade search strategy: TMDB ID -> Release name
        Note: IMDB search is not directly supported by La Cale External API.

        Args:
            tmdb_id: TMDB ID to search for
            imdb_id: IMDB ID (ignored - not supported by API, kept for interface compatibility)
            release_name: Release name to search for
            quality: Quality to filter results (optional)
            file_size: File size in bytes for exact match detection (optional)
            **kwargs: Additional arguments (ignored for compatibility)

        Returns:
            Dictionary with is_duplicate, exact_match, exact_matches, existing_torrents, search_method, message
        """
        logger.info(f"Checking for duplicates on La Cale: tmdb={tmdb_id}, name={release_name}")
        if imdb_id:
            logger.debug(f"IMDB ID provided ({imdb_id}) but not supported by La Cale API - using TMDB/name search")

        # Ensure we have an authenticated session
        if not self.authenticated_session:
            logger.warning("No authenticated session found, authenticating now...")
            await self.authenticate()

        if not self.authenticated_session:
            raise TrackerAPIError("Authentication required to check duplicates")

        try:
            existing_torrents = []
            search_method = "none"

            # Strategy 1: Search by TMDB ID (most reliable)
            if tmdb_id:
                logger.info(f"🔍 Duplicate check: Searching by TMDB ID: {tmdb_id}")
                results = await self.client.search_torrents(
                    self.authenticated_session,
                    query=str(tmdb_id),  # Ensure string
                    search_type="tmdb"
                )
                logger.info(f"🔍 TMDB search returned {len(results) if results else 0} results")
                if results:
                    existing_torrents = results
                    search_method = "tmdb"
                    logger.info(f"Found {len(results)} torrents by TMDB ID")
                    for i, t in enumerate(results[:5]):  # Log first 5 results
                        logger.debug(f"  Result {i+1}: {t.get('name', 'N/A')} (size={t.get('size', 0)}, hash={t.get('info_hash', 'N/A')[:16] if t.get('info_hash') else 'N/A'})")

            # Strategy 2: Search by release name (fallback)
            # Note: IMDB search is no longer supported by La Cale External API
            if not existing_torrents and release_name:
                # Extract title from release name (remove year, quality, etc.)
                import re
                # Simple extraction: take everything before the year
                title_match = re.match(r'^(.+?)[\.\s]+(19|20)\d{2}', release_name)
                search_query = title_match.group(1).replace('.', ' ') if title_match else release_name
                search_query = search_query[:200]  # API limit is 200 chars

                logger.info(f"🔍 Duplicate check: Searching by name: '{search_query}' (from release: {release_name[:50]})")
                results = await self.client.search_torrents(
                    self.authenticated_session,
                    query=search_query,
                    search_type="name"
                )
                logger.info(f"🔍 Name search returned {len(results) if results else 0} results")
                if results:
                    existing_torrents = results
                    search_method = "name"
                    logger.info(f"Found {len(results)} torrents by name search")
                    for i, t in enumerate(results[:5]):  # Log first 5 results
                        logger.debug(f"  Result {i+1}: {t.get('name', 'N/A')} (size={t.get('size', 0)})")

            # Filter by quality if specified
            if existing_torrents and quality:
                quality_lower = quality.lower()
                filtered = [t for t in existing_torrents if quality_lower in t.get('name', '').lower()]
                if filtered:
                    existing_torrents = filtered
                    logger.info(f"Filtered to {len(filtered)} torrents matching quality {quality}")

            # Check for exact matches by file size
            exact_matches = []
            if existing_torrents and file_size:
                # Allow 1% tolerance for size comparison
                tolerance = file_size * 0.01
                for t in existing_torrents:
                    torrent_size = t.get('size', 0)
                    if abs(torrent_size - file_size) <= tolerance:
                        exact_matches.append(t)
                if exact_matches:
                    logger.info(f"Found {len(exact_matches)} exact size matches")

            is_duplicate = len(existing_torrents) > 0
            exact_match = len(exact_matches) > 0

            if exact_match:
                message = f"EXACT MATCH: Found {len(exact_matches)} torrent(s) with same size"
            elif is_duplicate:
                message = f"Found {len(existing_torrents)} existing release(s) via {search_method} search"
            else:
                message = "No duplicates found - safe to upload"

            logger.info(f"Duplicate check result: is_duplicate={is_duplicate}, exact_match={exact_match}, method={search_method}")

            return {
                'is_duplicate': is_duplicate,
                'exact_match': exact_match,
                'exact_matches': exact_matches,
                'existing_torrents': existing_torrents,
                'search_method': search_method,
                'message': message
            }

        except (TrackerAPIError, NetworkRetryableError):
            raise

        except Exception as e:
            error_msg = f"Unexpected error checking duplicates: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    def get_flaresolverr_status(self) -> Dict[str, Any]:
        """
        Get current FlareSolverr circuit breaker status.

        Returns:
            Dictionary with circuit breaker state, failure count, and timing info

        Example:
            status = adapter.get_flaresolverr_status()
            print(f"Circuit state: {status['state']}")
            print(f"Failures: {status['failure_count']}")
        """
        return self.session_manager.get_status()

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform comprehensive health check on adapter and dependencies.

        Checks:
            - FlareSolverr service availability
            - Tracker API connectivity
            - Credential validity
            - Circuit breaker state

        Returns:
            Dictionary with health check results:
                {
                    'healthy': bool,
                    'flaresolverr_available': bool,
                    'tracker_reachable': bool,
                    'credentials_valid': bool,
                    'circuit_breaker_state': str,
                    'details': dict
                }

        Example:
            health = await adapter.health_check()
            if health['healthy']:
                print("Adapter is healthy and ready")
            else:
                print(f"Health issues: {health['details']}")
        """
        logger.info("Performing health check on LaCaleAdapter")

        health_status = {
            'healthy': True,
            'flaresolverr_available': False,
            'tracker_reachable': False,
            'credentials_valid': False,
            'circuit_breaker_state': self.session_manager.circuit_state.value,
            'details': {}
        }

        # Check FlareSolverr availability
        try:
            flaresolverr_healthy = await self.session_manager.health_check()
            health_status['flaresolverr_available'] = flaresolverr_healthy
            if not flaresolverr_healthy:
                health_status['healthy'] = False
                health_status['details']['flaresolverr'] = 'Service unavailable'
        except Exception as e:
            health_status['healthy'] = False
            health_status['details']['flaresolverr'] = f'Error: {e}'

        # Check tracker connectivity and credentials
        try:
            credentials_valid = await self.validate_credentials()
            health_status['credentials_valid'] = credentials_valid
            health_status['tracker_reachable'] = True

            if not credentials_valid:
                health_status['healthy'] = False
                health_status['details']['credentials'] = 'Invalid passkey'

        except NetworkRetryableError as e:
            health_status['healthy'] = False
            health_status['tracker_reachable'] = False
            health_status['details']['tracker'] = f'Network error: {e}'

        except Exception as e:
            health_status['healthy'] = False
            health_status['details']['tracker'] = f'Error: {e}'

        logger.info(
            f"Health check complete: "
            f"healthy={health_status['healthy']}, "
            f"FlareSolverr={health_status['flaresolverr_available']}, "
            f"tracker={health_status['tracker_reachable']}, "
            f"credentials={health_status['credentials_valid']}"
        )

        return health_status

    def get_adapter_info(self) -> Dict[str, Any]:
        """
        Get information about this tracker adapter.

        Returns static information about the La Cale adapter implementation
        for display in settings UI and logging.

        Returns:
            Dictionary with adapter information:
                {
                    'name': str,  # Adapter name
                    'tracker_name': str,  # Tracker name
                    'tracker_url': str,  # Tracker base URL
                    'version': str,  # Adapter version
                    'features': List[str]  # Supported features
                }

        Example:
            info = adapter.get_adapter_info()
            print(f"Using {info['name']} v{info['version']} for {info['tracker_name']}")
        """
        return {
            'name': 'La Cale Adapter',
            'tracker_name': 'La Cale',
            'tracker_url': self.tracker_url,
            'version': '2.0.0',
            'features': [
                'cloudflare_bypass',
                'nfo_upload',
                'mediainfo',
                'tmdb_metadata',
                'cover_images',
                'tags',
                'categories'
            ]
        }

    def __repr__(self) -> str:
        """String representation of LaCaleAdapter."""
        return (
            f"<LaCaleAdapter("
            f"tracker_url='{self.tracker_url}', "
            f"flaresolverr_url='{self.flaresolverr_url}', "
            f"passkey='***{self.passkey[-4:] if self.passkey else 'None'}', "
            f"authenticated={self.authenticated_session is not None}"
            f")>"
        )
