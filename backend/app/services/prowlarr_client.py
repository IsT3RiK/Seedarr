"""
ProwlarrClient - Integration with Prowlarr indexer manager

This module provides integration with Prowlarr to:
1. Fetch available indexers (trackers) and their configuration
2. Search for duplicates using Torznab API
3. Sync tracker list with Prowlarr

Prowlarr API Documentation:
    https://prowlarr.com/docs/api

Note: Prowlarr is an indexer (search) tool, NOT an upload tool.
Upload functionality still requires tracker-specific adapters.

Usage Example:
    client = ProwlarrClient(
        base_url="http://localhost:9696",
        api_key="your-api-key"
    )

    # Get all indexers
    indexers = await client.get_indexers()

    # Search for duplicates
    results = await client.search(indexer_id=1, query="Movie.2024.1080p")
"""

import logging
from typing import Dict, List, Any, Optional
from urllib.parse import urljoin

import httpx

from .exceptions import NetworkRetryableError, retry_on_network_error

logger = logging.getLogger(__name__)


class ProwlarrError(Exception):
    """Exception raised when Prowlarr API call fails."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class ProwlarrClient:
    """
    Client for Prowlarr API integration.

    Prowlarr is an indexer manager that aggregates multiple torrent trackers
    and provides a unified search API (Torznab). This client allows:

    - Fetching configured indexers (trackers)
    - Searching across indexers for duplicate detection
    - Getting indexer status and capabilities

    Attributes:
        base_url: Prowlarr instance URL (e.g., http://localhost:9696)
        api_key: Prowlarr API key (from Settings -> General -> Security)
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 30
    ):
        """
        Initialize ProwlarrClient.

        Args:
            base_url: Prowlarr instance URL
            api_key: API key from Prowlarr settings
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout

        logger.info(f"ProwlarrClient initialized for: {self.base_url}")

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        return {
            'X-Api-Key': self.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    @retry_on_network_error(max_retries=3)
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None
    ) -> Any:
        """
        Make an API request to Prowlarr with automatic retry on network errors.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., /api/v1/indexer)
            params: Query parameters
            json_data: JSON body data

        Returns:
            Parsed JSON response

        Raises:
            ProwlarrError: If request fails (non-retryable)
            NetworkRetryableError: If network issues occur (retryable)
        """
        url = urljoin(self.base_url, endpoint)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=self._get_headers(),
                    params=params,
                    json=json_data
                )

                # 5xx errors are retryable
                if response.status_code in (502, 503, 504):
                    raise NetworkRetryableError(
                        f"Prowlarr service temporarily unavailable (HTTP {response.status_code})"
                    )

                if response.status_code >= 400:
                    error_data = None
                    try:
                        error_data = response.json()
                    except Exception:
                        pass

                    raise ProwlarrError(
                        f"Prowlarr API error: {response.status_code}",
                        status_code=response.status_code,
                        response_data=error_data
                    )

                return response.json()

        except httpx.TimeoutException as e:
            raise NetworkRetryableError(f"Request timeout to {url}") from e
        except httpx.ConnectError as e:
            raise NetworkRetryableError(f"Connection error to {url}") from e
        except httpx.HTTPError as e:
            raise ProwlarrError(f"HTTP error: {e}")

    # =========================================================================
    # System Endpoints
    # =========================================================================

    async def get_status(self) -> Dict[str, Any]:
        """
        Get Prowlarr system status.

        Returns:
            System status including version, build date, etc.
        """
        return await self._request('GET', '/api/v1/system/status')

    async def health_check(self) -> Dict[str, Any]:
        """
        Check Prowlarr health/connectivity.

        Returns:
            Dict with 'healthy' boolean and 'version' string
        """
        try:
            status = await self.get_status()
            return {
                'healthy': True,
                'version': status.get('version', 'unknown'),
                'app_name': status.get('appName', 'Prowlarr'),
                'url': self.base_url
            }
        except ProwlarrError as e:
            return {
                'healthy': False,
                'error': str(e),
                'url': self.base_url
            }

    # =========================================================================
    # Indexer Endpoints
    # =========================================================================

    async def get_indexers(self) -> List[Dict[str, Any]]:
        """
        Get all configured indexers.

        Returns:
            List of indexer configurations

        Example response:
            [
                {
                    "id": 1,
                    "name": "YGGTorrent",
                    "protocol": "torrent",
                    "privacy": "private",
                    "enable": true,
                    "appProfileId": 1,
                    "definitionName": "yggtorrent",
                    "description": "French tracker",
                    "language": "fr-FR",
                    "indexerUrls": ["https://www.yggtorrent.si"],
                    "capabilities": {...}
                }
            ]
        """
        return await self._request('GET', '/api/v1/indexer')

    async def get_indexer(self, indexer_id: int) -> Dict[str, Any]:
        """
        Get a specific indexer by ID.

        Args:
            indexer_id: Prowlarr indexer ID

        Returns:
            Indexer configuration
        """
        return await self._request('GET', f'/api/v1/indexer/{indexer_id}')

    async def get_enabled_indexers(self) -> List[Dict[str, Any]]:
        """
        Get only enabled indexers.

        Returns:
            List of enabled indexer configurations
        """
        indexers = await self.get_indexers()
        return [idx for idx in indexers if idx.get('enable', False)]

    async def get_indexer_stats(self) -> List[Dict[str, Any]]:
        """
        Get indexer statistics.

        Returns:
            List of indexer stats (queries, grabs, failures)
        """
        return await self._request('GET', '/api/v1/indexerstats')

    # =========================================================================
    # Search Endpoints (Torznab)
    # =========================================================================

    async def search(
        self,
        query: str,
        indexer_ids: Optional[List[int]] = None,
        categories: Optional[List[int]] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Search across indexers using Prowlarr's internal search.

        Args:
            query: Search query string
            indexer_ids: List of indexer IDs to search (None = all enabled)
            categories: Category IDs to filter (Newznab categories)
            limit: Maximum results per indexer

        Returns:
            List of search results

        Example response:
            [
                {
                    "guid": "...",
                    "indexerId": 1,
                    "indexer": "YGGTorrent",
                    "title": "Movie.2024.1080p.BluRay",
                    "size": 8589934592,
                    "publishDate": "2024-01-15T10:00:00Z",
                    "downloadUrl": "...",
                    "infoUrl": "..."
                }
            ]
        """
        params = {
            'query': query,
            'limit': limit,
            'type': 'search'
        }

        if indexer_ids:
            params['indexerIds'] = indexer_ids

        if categories:
            params['categories'] = categories

        return await self._request('GET', '/api/v1/search', params=params)

    async def search_by_tmdb(
        self,
        tmdb_id: str,
        indexer_ids: Optional[List[int]] = None,
        media_type: str = 'movie'
    ) -> List[Dict[str, Any]]:
        """
        Search by TMDB ID.

        Args:
            tmdb_id: TMDB ID
            indexer_ids: Specific indexers to search
            media_type: 'movie' or 'tv'

        Returns:
            List of search results
        """
        params = {
            'type': 'tvsearch' if media_type == 'tv' else 'movie',
            'tmdbId': tmdb_id
        }

        if indexer_ids:
            params['indexerIds'] = indexer_ids

        return await self._request('GET', '/api/v1/search', params=params)

    async def search_by_imdb(
        self,
        imdb_id: str,
        indexer_ids: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Search by IMDB ID.

        Args:
            imdb_id: IMDB ID (with or without 'tt' prefix)
            indexer_ids: Specific indexers to search

        Returns:
            List of search results
        """
        # Ensure tt prefix
        if not imdb_id.startswith('tt'):
            imdb_id = f'tt{imdb_id}'

        params = {
            'type': 'movie',
            'imdbId': imdb_id
        }

        if indexer_ids:
            params['indexerIds'] = indexer_ids

        return await self._request('GET', '/api/v1/search', params=params)

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def indexer_to_tracker_dict(self, indexer: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert Prowlarr indexer to tracker configuration dict.

        This maps Prowlarr's indexer structure to our Tracker model format
        for easy import.

        Args:
            indexer: Prowlarr indexer dict

        Returns:
            Dict compatible with Tracker.create_or_update()
        """
        # Extract base URL from indexerUrls
        urls = indexer.get('indexerUrls', [])
        base_url = urls[0] if urls else ''

        # Generate slug from definition name or name
        definition = indexer.get('definitionName', '')
        name = indexer.get('name', 'Unknown')
        slug = definition or name.lower().replace(' ', '-').replace('.', '')

        # Determine adapter type (we'll need to map known trackers)
        adapter_type = self._determine_adapter_type(definition)

        # Extract capabilities
        capabilities = indexer.get('capabilities', {})
        categories_caps = capabilities.get('categories', [])

        return {
            'name': name,
            'slug': slug,
            'tracker_url': base_url,
            'adapter_type': adapter_type,
            'requires_cloudflare': indexer.get('privacy') == 'private',
            'enabled': indexer.get('enable', False),
            'upload_enabled': False,  # Default to false, user must enable
            'priority': 100,  # Default priority
            # Store Prowlarr ID for reference
            'extra_config': {
                'prowlarr_id': indexer.get('id'),
                'prowlarr_definition': definition,
                'prowlarr_language': indexer.get('language'),
                'prowlarr_protocol': indexer.get('protocol'),
                'prowlarr_privacy': indexer.get('privacy'),
                'prowlarr_categories': categories_caps
            }
        }

    def _determine_adapter_type(self, definition_name: str) -> str:
        """
        Determine adapter type from Prowlarr definition name.

        Maps known Prowlarr definitions to our adapter types.

        Args:
            definition_name: Prowlarr definition identifier

        Returns:
            Adapter type string ('lacale', 'c411', 'generic')
        """
        # Map known definitions to adapter types
        ADAPTER_MAP = {
            'lacale': 'lacale',
            'cinema411': 'c411',
            'c411': 'c411',
            # Add more mappings as needed
        }

        definition_lower = definition_name.lower()

        for key, adapter in ADAPTER_MAP.items():
            if key in definition_lower:
                return adapter

        return 'generic'

    async def check_duplicate_on_indexer(
        self,
        indexer_id: int,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        release_name: Optional[str] = None,
        quality: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Check for duplicates on a specific indexer.

        Uses cascade search strategy: TMDB ID -> IMDB ID -> release name

        Args:
            indexer_id: Prowlarr indexer ID
            tmdb_id: TMDB ID to search
            imdb_id: IMDB ID to search
            release_name: Release name to search
            quality: Quality filter (e.g., '1080p')

        Returns:
            Dict with is_duplicate, existing_torrents, search_method
        """
        results = []
        search_method = None

        # Strategy 1: Search by TMDB ID
        if tmdb_id:
            try:
                results = await self.search_by_tmdb(tmdb_id, indexer_ids=[indexer_id])
                if results:
                    search_method = 'tmdb_id'
            except Exception as e:
                logger.warning(f"TMDB search failed: {e}")

        # Strategy 2: Search by IMDB ID
        if not results and imdb_id:
            try:
                results = await self.search_by_imdb(imdb_id, indexer_ids=[indexer_id])
                if results:
                    search_method = 'imdb_id'
            except Exception as e:
                logger.warning(f"IMDB search failed: {e}")

        # Strategy 3: Search by release name
        if not results and release_name:
            try:
                # Extract title and year from release name for better search
                search_query = release_name.replace('.', ' ').replace('-', ' ')
                results = await self.search(search_query, indexer_ids=[indexer_id], limit=20)
                if results:
                    search_method = 'release_name'
            except Exception as e:
                logger.warning(f"Name search failed: {e}")

        # Filter by quality if specified
        if quality and results:
            quality_lower = quality.lower()
            results = [
                r for r in results
                if quality_lower in r.get('title', '').lower()
            ]

        # Format results
        existing = [
            {
                'title': r.get('title'),
                'size': r.get('size'),
                'indexer': r.get('indexer'),
                'publish_date': r.get('publishDate')
            }
            for r in results[:10]  # Limit to 10 results
        ]

        return {
            'is_duplicate': len(results) > 0,
            'existing_torrents': existing,
            'search_method': search_method,
            'total_found': len(results)
        }


# Singleton instance
_prowlarr_client: Optional[ProwlarrClient] = None


def get_prowlarr_client(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None
) -> Optional[ProwlarrClient]:
    """
    Get or create ProwlarrClient singleton.

    Args:
        base_url: Prowlarr URL (required on first call)
        api_key: API key (required on first call)

    Returns:
        ProwlarrClient instance or None if not configured
    """
    global _prowlarr_client

    if base_url and api_key:
        _prowlarr_client = ProwlarrClient(base_url, api_key)

    return _prowlarr_client


def reset_prowlarr_client() -> None:
    """Reset the Prowlarr client singleton."""
    global _prowlarr_client
    _prowlarr_client = None
