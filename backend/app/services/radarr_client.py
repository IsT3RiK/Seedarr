"""
RadarrClient - Integration with Radarr movie manager

This module provides integration with Radarr to retrieve the original
sceneName of downloaded movies. When Radarr renames a file, the original
release name is preserved in movieFile.sceneName.

Usage Example:
    client = RadarrClient(
        base_url="http://localhost:7878",
        api_key="your-api-key"
    )

    scene_name = await client.find_scene_name_by_path("/data/movies/The Matrix (1999)/The Matrix (1999).mkv")
    # Returns: "The.Matrix.1999.1080p.BluRay.x264-GROUP"
"""

import logging
import os
import re
from typing import Dict, List, Any, Optional
from urllib.parse import urljoin

import httpx

from .exceptions import NetworkRetryableError, retry_on_network_error

logger = logging.getLogger(__name__)


def _norm(name: str) -> str:
    """Normalize a filename for cross-platform comparison.

    Removes characters that Windows does not allow in filenames but Linux does
    (primarily ':'), which causes mismatches when Radarr runs on Linux and
    Seedarr reads the SMB-mounted path on Windows.
    """
    for ch in ':*?"<>|':
        name = name.replace(ch, ' ')
    return ' '.join(name.split()).lower()


def _extract_year(name: str) -> Optional[int]:
    """Extract the first 4-digit year (1900-2099) found in a string."""
    m = re.search(r'\b(19|20)\d{2}\b', name)
    return int(m.group(0)) if m else None


def _year_ok(movie_year: Optional[int], scene_name: str) -> bool:
    """
    Return True if the sceneName year is consistent with the movie year.

    Allows a ±1 tolerance (regional release offsets).
    If either year is absent we cannot validate, so we allow it.
    """
    if not movie_year:
        return True
    scene_year = _extract_year(scene_name)
    if not scene_year:
        return True
    if abs(scene_year - movie_year) > 1:
        logger.warning(
            f"Radarr: sceneName year {scene_year} ≠ movie year {movie_year} "
            f"— discarding stale/wrong sceneName: '{scene_name}'"
        )
        return False
    return True


class RadarrError(Exception):
    """Exception raised when Radarr API call fails."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class RadarrClient:
    """
    Client for Radarr API integration.

    Provides sceneName lookup for files managed by Radarr.
    When Radarr downloads and renames a file, it stores the original
    release name (sceneName) in the movieFile record.

    Attributes:
        base_url: Radarr instance URL (e.g., http://localhost:7878)
        api_key: Radarr API key (from Settings -> General -> Security)
        timeout: Request timeout in seconds
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        logger.debug(f"RadarrClient initialized for: {self.base_url}")

    def _get_headers(self) -> Dict[str, str]:
        return {
            'X-Api-Key': self.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    @retry_on_network_error(max_retries=2)
    async def _request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Any:
        url = urljoin(self.base_url + '/', endpoint.lstrip('/'))

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=self._get_headers(),
                    params=params
                )

                if response.status_code in (502, 503, 504):
                    raise NetworkRetryableError(
                        f"Radarr service temporarily unavailable (HTTP {response.status_code})"
                    )

                if response.status_code >= 400:
                    raise RadarrError(
                        f"Radarr API error: {response.status_code}",
                        status_code=response.status_code
                    )

                return response.json()

        except httpx.TimeoutException as e:
            raise NetworkRetryableError(f"Request timeout to {url}") from e
        except httpx.ConnectError as e:
            raise NetworkRetryableError(f"Connection error to {url}") from e
        except httpx.HTTPError as e:
            raise RadarrError(f"HTTP error: {e}")

    async def health_check(self) -> Dict[str, Any]:
        """
        Check Radarr health/connectivity.

        Returns:
            Dict with 'healthy' boolean and 'version' string
        """
        try:
            status = await self._request('GET', '/api/v3/system/status')
            return {
                'healthy': True,
                'version': status.get('version', 'unknown'),
                'app_name': status.get('appName', 'Radarr'),
                'url': self.base_url
            }
        except RadarrError as e:
            return {'healthy': False, 'error': str(e), 'url': self.base_url}

    async def get_movies_with_files(self) -> List[Dict[str, Any]]:
        """
        Get all movies that have a file on disk.

        Returns:
            List of movie records with movieFile embedded
        """
        return await self._request('GET', '/api/v3/movie', params={'hasFile': 'true'})

    async def get_scene_name_from_history(self, movie_id: int, movie_year: Optional[int]) -> Optional[str]:
        """
        Look up the original scene release name from Radarr's import history.

        Used as a fallback when movieFile.sceneName is absent or stale.
        Radarr stores the original torrent/NZB name as 'sourceTitle' in each
        history event. We look at the most recent 'downloadFolderImported' event.

        Args:
            movie_id: Radarr internal movie ID
            movie_year: Expected release year for year-sanity check

        Returns:
            Scene release name from history, or None if not found.
        """
        try:
            events = await self._request(
                'GET', '/api/v3/history/movie',
                params={'movieId': movie_id}
            )
        except Exception as e:
            logger.debug(f"Radarr: failed to fetch history for movie {movie_id}: {e}")
            return None

        if not isinstance(events, list):
            return None

        # Prefer 'downloadFolderImported' events (the actual import), then 'grabbed'
        for event_type in ('downloadFolderImported', 'grabbed'):
            for event in events:  # events are already sorted newest-first by Radarr
                if event.get('eventType') == event_type:
                    source = event.get('sourceTitle', '').strip()
                    if source and _year_ok(movie_year, source):
                        logger.debug(
                            f"Radarr history fallback: found sceneName='{source}' "
                            f"(event={event_type}, movie_id={movie_id})"
                        )
                        return source

        return None

    async def find_scene_name_by_path(self, file_path: str) -> tuple:
        """
        Find the original sceneName for a file managed by Radarr.

        Matches file_path against movieFile.path in Radarr's database.
        Falls back to basename comparison if exact path doesn't match
        (useful when Seedarr and Radarr use different mount paths).

        Args:
            file_path: Absolute path to the media file as seen by Seedarr

        Returns:
            Tuple (scene_name, found_in_radarr).
            scene_name is None if not found or no sceneName stored.
            found_in_radarr is True if the file was matched in Radarr (even without sceneName),
            which allows the caller to skip Sonarr for movie files.
        """
        try:
            movies = await self.get_movies_with_files()
        except Exception as e:
            logger.warning(f"RadarrClient: failed to fetch movies: {e}")
            return None, False

        file_basename = os.path.basename(file_path)

        async def _scene_with_history_fallback(movie: Dict, scene_name: Optional[str], via: str) -> Optional[str]:
            """Return a valid sceneName, falling back to import history if needed."""
            movie_year = movie.get('year')
            if scene_name and _year_ok(movie_year, scene_name):
                logger.debug(f"Radarr {via} match for '{file_path}': sceneName='{scene_name}'")
                return scene_name
            if scene_name:
                # Year check failed — log and try history
                logger.warning(
                    f"Radarr: sceneName year mismatch on {via} match "
                    f"(movie year={movie_year}, scene='{scene_name}') — checking history"
                )
            movie_id = movie.get('id')
            if movie_id:
                hist = await self.get_scene_name_from_history(movie_id, movie_year)
                if hist:
                    logger.info(f"Radarr history fallback resolved sceneName='{hist}'")
                    return hist
            logger.info(
                f"Radarr: '{movie.get('title', '?')} ({movie_year})'"
                f" found by {via} but no valid sceneName — check Radarr history"
            )
            return None

        # Pass 1: exact path match
        for movie in movies:
            movie_file = movie.get('movieFile')
            if not movie_file:
                continue
            if movie_file.get('path') == file_path:
                result = await _scene_with_history_fallback(
                    movie, movie_file.get('sceneName'), 'exact path'
                )
                return result, True

        # Pass 2: normalized basename match (handles Linux ':' vs Windows path differences)
        # e.g. Radarr on Linux stores "Title : Subtitle (2005).mkv" but Windows SMB shows
        # "Title  Subtitle (2005).mkv" because ':' is invalid on Windows.
        norm_basename = _norm(file_basename)
        matched_movie = None
        for movie in movies:
            movie_file = movie.get('movieFile')
            if not movie_file:
                continue
            radarr_path = movie_file.get('path', '')
            if _norm(os.path.basename(radarr_path)) == norm_basename:
                matched_movie = (movie, movie_file)
                scene_name = movie_file.get('sceneName')
                if scene_name and _year_ok(movie.get('year'), scene_name):
                    # Valid sceneName found directly
                    break

        if matched_movie:
            movie, movie_file = matched_movie
            result = await _scene_with_history_fallback(
                movie, movie_file.get('sceneName'), 'basename'
            )
            return result, True

        logger.debug(f"Radarr: no match found for '{file_path}'")
        return None, False
