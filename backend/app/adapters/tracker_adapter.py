"""
TrackerAdapter Abstract Base Class for Seedarr v2.0

This module defines the TrackerAdapter abstract base class (ABC) that establishes
the contract interface for all tracker implementations. This abstraction allows
the pipeline to work with any tracker without knowing tracker-specific details.

Architecture Pattern:
    - Pipeline depends only on TrackerAdapter interface
    - Concrete adapters (LaCaleAdapter, etc.) implement the interface
    - Tracker selection configurable via database setting
    - Easy to add new tracker support by implementing this interface

Contract Methods:
    - authenticate(): Establish session with tracker (includes Cloudflare bypass if needed)
    - upload_torrent(): Upload .torrent file with metadata to tracker
    - validate_credentials(): Verify passkey/credentials are valid
    - get_tags(): Fetch available tag IDs from tracker
    - get_categories(): Fetch available categories from tracker

Benefits:
    - Tracker-agnostic pipeline code
    - Clear separation of concerns
    - Easy testing via mocks
    - Simple to add new tracker support
    - Configuration-driven tracker selection

Usage Example:
    # Pipeline code works with interface, not concrete implementation
    adapter: TrackerAdapter = get_tracker_adapter()  # Via dependency injection

    # Authenticate with tracker
    await adapter.authenticate()

    # Upload torrent
    result = await adapter.upload_torrent(
        torrent_data=torrent_bytes,
        release_name="Movie.2023.1080p.BluRay.x264",
        category_id="1",
        tag_ids=["10", "15", "20"],
        nfo_content="NFO content here"
    )
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from pathlib import Path


class TrackerAdapter(ABC):
    """
    Abstract base class defining the contract for tracker adapters.

    All tracker implementations must inherit from this class and implement
    all abstract methods. This ensures consistent interface across different
    tracker backends.

    The adapter handles all tracker-specific logic:
        - Authentication (including Cloudflare bypass if needed)
        - API request formatting
        - Multipart form data preparation
        - Tag and category management
        - Error handling and retry logic

    Implementations should:
        - Use the typed exception hierarchy (TrackerAPIError, etc.)
        - Apply retry decorators for retryable operations
        - Log all API interactions at DEBUG level
        - Log authentication and uploads at INFO level
        - Handle tracker-specific quirks (e.g., repeated fields for tags)
    """

    @abstractmethod
    async def authenticate(self) -> bool:
        """
        Authenticate with the tracker and establish a session.

        This method should:
            1. Handle any Cloudflare bypass if required
            2. Obtain and store session cookies/tokens
            3. Verify authentication succeeded
            4. Set up session for subsequent requests

        For trackers behind Cloudflare:
            - Use FlareSolverr to bypass challenge
            - Extract cookies from FlareSolverr response
            - Apply cookies to session

        Returns:
            True if authentication successful, False otherwise

        Raises:
            CloudflareBypassError: If Cloudflare bypass fails (retryable)
            TrackerAPIError: If authentication fails due to invalid credentials (non-retryable)
            NetworkRetryableError: If network/connectivity issues occur (retryable)

        Example:
            adapter = LaCaleAdapter(passkey="abc123")
            authenticated = await adapter.authenticate()
            if authenticated:
                # Proceed with uploads
                pass
        """
        pass

    @abstractmethod
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
        Upload a .torrent file with metadata to the tracker.

        This method should:
            1. Prepare multipart form data according to tracker API format
            2. Handle tracker-specific field formatting (e.g., repeated tag fields)
            3. Upload .torrent file and metadata
            4. Parse and return tracker response (torrent ID, URL, etc.)

        CRITICAL Implementation Notes:
            - Some trackers require tags as REPEATED form fields, not JSON arrays
            - Always include NFO content if required by tracker
            - Validate all required fields before upload
            - Log full request details at DEBUG level for troubleshooting

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
        pass

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """
        Validate tracker credentials (passkey, API key, etc.) without full authentication.

        This method should:
            1. Perform a lightweight check of credentials
            2. Verify credentials are in valid format
            3. Optionally ping tracker API to verify credentials work
            4. Return validation result without side effects

        Useful for:
            - Settings validation in admin UI
            - Health checks
            - Startup verification

        Returns:
            True if credentials are valid, False otherwise

        Raises:
            NetworkRetryableError: If network issues prevent validation (retryable)

        Example:
            adapter = LaCaleAdapter(passkey="abc123")
            if await adapter.validate_credentials():
                print("Credentials are valid")
            else:
                print("Invalid credentials")
        """
        pass

    @abstractmethod
    async def get_tags(self) -> List[Dict[str, Any]]:
        """
        Fetch available tags from tracker API.

        This method should:
            1. Query tracker API for current tag list
            2. Parse response into standardized format
            3. Return tag information (ID, label, category, description)
            4. Handle pagination if tracker API uses it

        Used for:
            - Dynamic tag ID loading at application startup
            - Populating Tags database table
            - Settings UI to show available tags

        Returns:
            List of tag dictionaries with structure:
                [
                    {
                        'tag_id': str,  # Tracker's tag ID
                        'label': str,  # Human-readable label
                        'category': str,  # Optional category (e.g., "Type", "Quality")
                        'description': str  # Optional description
                    },
                    ...
                ]

        Raises:
            NetworkRetryableError: If network issues occur (retryable)
            TrackerAPIError: If API returns error (non-retryable)

        Example:
            tags = await adapter.get_tags()
            for tag in tags:
                print(f"Tag: {tag['label']} (ID: {tag['tag_id']})")
        """
        pass

    @abstractmethod
    async def get_categories(self) -> List[Dict[str, Any]]:
        """
        Fetch available categories from tracker API.

        This method should:
            1. Query tracker API for current category list
            2. Parse response into standardized format
            3. Return category information (ID, name, description)

        Used for:
            - Settings UI to show available categories
            - Validation of category_id before upload
            - Admin dashboard

        Returns:
            List of category dictionaries with structure:
                [
                    {
                        'category_id': str,  # Tracker's category ID
                        'name': str,  # Category name (e.g., "Movies", "TV Shows")
                        'description': str  # Optional description
                    },
                    ...
                ]

        Raises:
            NetworkRetryableError: If network issues occur (retryable)
            TrackerAPIError: If API returns error (non-retryable)

        Example:
            categories = await adapter.get_categories()
            for cat in categories:
                print(f"Category: {cat['name']} (ID: {cat['category_id']})")
        """
        pass

    @abstractmethod
    async def check_duplicate(
        self,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        release_name: Optional[str] = None,
        quality: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Check if a release already exists on the tracker.

        This method performs duplicate detection using a cascade search strategy:
        1. Search by TMDB ID (most reliable if available)
        2. Search by IMDB ID (fallback if TMDB not found)
        3. Search by release name (final fallback)

        Args:
            tmdb_id: TMDB ID to search for (e.g., "12345")
            imdb_id: IMDB ID to search for (e.g., "tt1234567")
            release_name: Release name to search for
            quality: Quality/resolution to filter duplicates (e.g., "1080p", "4K")

        Returns:
            Dictionary with duplicate check results:
                {
                    'is_duplicate': bool,  # True if release exists
                    'existing_torrents': List[Dict],  # List of matching torrents found
                    'search_method': str,  # Which method found the match ("tmdb", "imdb", "name", "none")
                    'message': str  # Human-readable result message
                }

            Each torrent in existing_torrents contains:
                {
                    'torrent_id': str,
                    'name': str,
                    'url': str,
                    'quality': str,  # If available
                    'uploaded_at': str  # If available
                }

        Raises:
            NetworkRetryableError: If network issues occur (retryable)
            TrackerAPIError: If API returns error (non-retryable)

        Example:
            result = await adapter.check_duplicate(
                tmdb_id="550",  # Fight Club
                quality="1080p"
            )

            if result['is_duplicate']:
                print(f"Found {len(result['existing_torrents'])} existing releases")
                print(f"Detected via: {result['search_method']}")
            else:
                print("No duplicates found - safe to upload")
        """
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """
        Perform health check on tracker and dependencies.

        This method should:
            1. Check tracker API availability
            2. Verify authentication status
            3. Check FlareSolverr status (if applicable)
            4. Return comprehensive health status

        Used for:
            - Admin dashboard health indicators
            - Circuit breaker status
            - Monitoring and alerting

        Returns:
            Dictionary with health status:
                {
                    'tracker_api': bool,  # Tracker API reachable
                    'authenticated': bool,  # Session authenticated
                    'flaresolverr': bool,  # FlareSolverr available (if used)
                    'circuit_breaker': str,  # Circuit breaker state ("open", "closed", "half-open")
                    'last_error': str,  # Last error message if any
                    'last_success': datetime  # Last successful operation timestamp
                }

        Example:
            health = await adapter.health_check()
            if health['tracker_api'] and health['authenticated']:
                print("Tracker is healthy and ready")
            else:
                print(f"Tracker issues detected: {health['last_error']}")
        """
        pass

    @abstractmethod
    def get_adapter_info(self) -> Dict[str, str]:
        """
        Get information about this tracker adapter.

        Returns static information about the adapter implementation
        for display in settings UI and logging.

        Returns:
            Dictionary with adapter information:
                {
                    'name': str,  # Adapter name (e.g., "La Cale Adapter")
                    'tracker_name': str,  # Tracker name (e.g., "La Cale")
                    'tracker_url': str,  # Tracker base URL
                    'version': str,  # Adapter version
                    'features': List[str]  # Supported features (e.g., ["nfo", "mediainfo", "screenshots"])
                }

        Example:
            info = adapter.get_adapter_info()
            print(f"Using {info['name']} v{info['version']} for {info['tracker_name']}")
        """
        pass
