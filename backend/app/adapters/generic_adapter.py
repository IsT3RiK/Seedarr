"""
GenericTrackerAdapter - Fallback TrackerAdapter Implementation

This module provides a generic/placeholder tracker adapter that can be
used as a fallback when no specific adapter is available. It implements
the TrackerAdapter interface with basic functionality.

Use Cases:
    - Testing and development
    - Trackers without specific implementations
    - Future tracker support scaffolding

Note: This adapter provides minimal functionality and should be extended
for production use with specific tracker implementations.
"""

import logging
from typing import Dict, List, Optional, Any

from .tracker_adapter import TrackerAdapter
from ..services.exceptions import TrackerAPIError, NetworkRetryableError

logger = logging.getLogger(__name__)


class GenericTrackerAdapter(TrackerAdapter):
    """
    Generic tracker adapter for basic tracker support.

    This adapter provides a minimal implementation of the TrackerAdapter
    interface. It's intended as a fallback/placeholder for trackers that
    don't have specific implementations.

    Note: Uploads via this adapter will not actually work without
    customization for the specific tracker's API.

    Attributes:
        tracker_url: Tracker base URL
        passkey: Passkey for authentication
        api_key: API key if used by tracker
        authenticated: Whether authentication succeeded
    """

    def __init__(
        self,
        tracker_url: str,
        passkey: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 60
    ):
        """
        Initialize GenericTrackerAdapter.

        Args:
            tracker_url: Tracker base URL
            passkey: Passkey for authentication (optional)
            api_key: API key for authentication (optional)
            timeout: HTTP request timeout in seconds
        """
        self.tracker_url = tracker_url
        self.passkey = passkey
        self.api_key = api_key
        self.timeout = timeout
        self.authenticated = False

        logger.info(
            f"GenericTrackerAdapter initialized for: {tracker_url}"
        )

    async def authenticate(self) -> bool:
        """
        Authenticate with the tracker.

        Generic implementation - always returns True.
        Override for actual authentication logic.

        Returns:
            True (placeholder)
        """
        logger.info(f"Generic authentication for: {self.tracker_url}")

        # Placeholder - real implementation would verify credentials
        if self.passkey or self.api_key:
            self.authenticated = True
            logger.info("Generic authentication successful (placeholder)")
            return True

        logger.warning("No credentials provided for generic adapter")
        self.authenticated = True  # Allow anyway for testing
        return True

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
        Upload a torrent to the tracker.

        Generic implementation - returns a placeholder result.
        Override for actual upload logic.

        Returns:
            Placeholder upload result

        Raises:
            TrackerAPIError: Generic adapter cannot perform real uploads
        """
        logger.warning(
            f"GenericTrackerAdapter.upload_torrent() called for: {release_name}. "
            f"This is a placeholder and does not perform actual uploads."
        )

        raise TrackerAPIError(
            f"GenericTrackerAdapter does not support real uploads. "
            f"Please implement a specific adapter for: {self.tracker_url}"
        )

    async def validate_credentials(self) -> bool:
        """
        Validate tracker credentials.

        Generic implementation - checks if credentials are provided.

        Returns:
            True if credentials are provided, False otherwise
        """
        logger.info("Validating generic credentials")

        if self.passkey and len(self.passkey) >= 10:
            return True
        if self.api_key and len(self.api_key) >= 10:
            return True

        logger.warning("No valid credentials for generic adapter")
        return False

    async def get_tags(self) -> List[Dict[str, Any]]:
        """
        Fetch available tags from tracker.

        Generic implementation - returns empty list.

        Returns:
            Empty list
        """
        logger.info("GenericTrackerAdapter.get_tags() - returning empty list")
        return []

    async def get_categories(self) -> List[Dict[str, Any]]:
        """
        Fetch available categories from tracker.

        Generic implementation - returns empty list.

        Returns:
            Empty list
        """
        logger.info("GenericTrackerAdapter.get_categories() - returning empty list")
        return []

    async def check_duplicate(
        self,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        release_name: Optional[str] = None,
        quality: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Check for duplicate releases on the tracker via Torznab API.

        Search cascade:
        1. TMDB ID search (most reliable)
        2. IMDB ID search
        3. Release name search (fallback)

        Returns:
            Dictionary with is_duplicate, existing_torrents, search_method, message
        """
        import aiohttp
        import xml.etree.ElementTree as ET

        logger.info(
            f"GenericTrackerAdapter.check_duplicate() - "
            f"tmdb_id={tmdb_id}, imdb_id={imdb_id}, release_name={release_name}"
        )

        if not self.api_key:
            return {
                'is_duplicate': False,
                'existing_torrents': [],
                'search_method': 'none',
                'message': 'No API key configured for Torznab search'
            }

        # Build Torznab API base URL
        api_base = f"{self.tracker_url.rstrip('/')}/api"

        async def torznab_search(params: Dict[str, str], method: str) -> List[Dict]:
            """Execute Torznab search and parse results."""
            params['apikey'] = self.api_key
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_base, params=params, timeout=self.timeout) as response:
                        if response.status != 200:
                            logger.warning(f"Torznab search failed: HTTP {response.status}")
                            return []

                        xml_content = await response.text()
                        return parse_torznab_results(xml_content)
            except Exception as e:
                logger.error(f"Torznab search error ({method}): {e}")
                return []

        def parse_torznab_results(xml_content: str) -> List[Dict]:
            """Parse Torznab XML response."""
            results = []
            try:
                root = ET.fromstring(xml_content)
                # Handle RSS format (Torznab standard)
                for item in root.findall('.//item'):
                    title = item.findtext('title', '')
                    link = item.findtext('link', '')
                    guid = item.findtext('guid', '')
                    size = item.findtext('size', '0')

                    # Get torznab attributes
                    for attr in item.findall('.//{http://torznab.com/schemas/2015/feed}attr'):
                        name = attr.get('name', '')
                        value = attr.get('value', '')
                        if name == 'size' and value:
                            size = value

                    results.append({
                        'title': title,
                        'link': link,
                        'guid': guid,
                        'size': int(size) if size.isdigit() else 0
                    })
            except ET.ParseError as e:
                logger.error(f"Failed to parse Torznab XML: {e}")
            return results

        existing_torrents = []
        search_method = 'none'

        # 1. Search by TMDB ID
        if tmdb_id:
            logger.info(f"Searching by TMDB ID: {tmdb_id}")
            # Try movie search
            results = await torznab_search({'t': 'movie', 'tmdbid': str(tmdb_id)}, 'tmdb_movie')
            if results:
                existing_torrents = results
                search_method = 'tmdb_movie'
            else:
                # Try TV search
                results = await torznab_search({'t': 'tvsearch', 'tmdbid': str(tmdb_id)}, 'tmdb_tv')
                if results:
                    existing_torrents = results
                    search_method = 'tmdb_tv'

        # 2. Search by IMDB ID
        if not existing_torrents and imdb_id:
            logger.info(f"Searching by IMDB ID: {imdb_id}")
            results = await torznab_search({'t': 'movie', 'imdbid': imdb_id.replace('tt', '')}, 'imdb')
            if results:
                existing_torrents = results
                search_method = 'imdb'

        # 3. Search by release name
        if not existing_torrents and release_name:
            # Extract key terms from release name for search
            # Try to get the movie/series title
            search_query = release_name.split('(')[0].strip()  # Get title before year
            if not search_query or len(search_query) < 3:
                search_query = release_name.split('.')[0]  # Get first part before dot
            if len(search_query) < 3:
                search_query = release_name[:50]

            logger.info(f"Searching by name: '{search_query}' (from: {release_name})")
            results = await torznab_search({'t': 'search', 'q': search_query}, 'name')
            search_method = 'name'  # Mark that we attempted a search

            if results:
                logger.info(f"Found {len(results)} results from name search")
                # Filter results that match closely
                matching = [r for r in results if self._similar_release(r['title'], release_name)]
                if matching:
                    existing_torrents = matching
                    search_method = 'name_exact'
                else:
                    # Show potential matches even if not exact
                    existing_torrents = results[:5]  # Top 5 results
                    search_method = 'name_partial'
            else:
                logger.info("No results from name search")

        is_duplicate = len(existing_torrents) > 0

        message = f"Found {len(existing_torrents)} existing torrent(s)" if is_duplicate else "No duplicates found"
        if search_method != 'none':
            message += f" (via {search_method})"

        logger.info(f"Duplicate check result: is_duplicate={is_duplicate}, method={search_method}, count={len(existing_torrents)}")

        return {
            'is_duplicate': is_duplicate,
            'existing_torrents': existing_torrents[:10],  # Limit to 10
            'search_method': search_method,
            'message': message
        }

    def _similar_release(self, title1: str, title2: str) -> bool:
        """
        Check if two release names are similar enough to be considered duplicates.

        Compares normalized versions of the release names.

        Args:
            title1: First release name
            title2: Second release name

        Returns:
            True if releases are similar, False otherwise
        """
        def normalize(s: str) -> str:
            """Normalize release name for comparison."""
            s = s.lower()
            # Remove common separators
            for char in '.-_':
                s = s.replace(char, ' ')
            # Remove quality indicators for comparison
            for q in ['2160p', '1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'avc',
                      'bluray', 'webrip', 'web-dl', 'webdl', 'hdtv', 'hdrip', 'bdrip',
                      'remux', 'hdr', 'hdr10', 'dv', 'dolby vision', 'atmos']:
                s = s.replace(q, '')
            # Normalize whitespace
            return ' '.join(s.split())

        norm1 = normalize(title1)
        norm2 = normalize(title2)

        # Check if one contains the other (for partial matches)
        if norm1 in norm2 or norm2 in norm1:
            return True

        # Check word overlap
        words1 = set(norm1.split())
        words2 = set(norm2.split())

        if not words1 or not words2:
            return False

        # Calculate Jaccard similarity
        intersection = words1 & words2
        union = words1 | words2
        similarity = len(intersection) / len(union)

        return similarity > 0.6  # 60% word overlap

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform health check on adapter.

        Generic implementation - returns basic status.

        Returns:
            Dictionary with health check results
        """
        return {
            'healthy': True,
            'tracker_reachable': True,  # Assumed
            'credentials_valid': bool(self.passkey or self.api_key),
            'flaresolverr_available': True,  # Not used
            'circuit_breaker_state': 'not_applicable',
            'details': {
                'note': 'GenericTrackerAdapter provides placeholder functionality'
            }
        }

    def get_adapter_info(self) -> Dict[str, Any]:
        """
        Get information about this tracker adapter.

        Returns:
            Dictionary with adapter information
        """
        return {
            'name': 'Generic Tracker Adapter',
            'tracker_name': 'Generic',
            'tracker_url': self.tracker_url,
            'version': '1.0.0',
            'features': [
                'placeholder',
                'testing'
            ]
        }

    def __repr__(self) -> str:
        """String representation of GenericTrackerAdapter."""
        return (
            f"<GenericTrackerAdapter("
            f"tracker_url='{self.tracker_url}', "
            f"authenticated={self.authenticated}"
            f")>"
        )
