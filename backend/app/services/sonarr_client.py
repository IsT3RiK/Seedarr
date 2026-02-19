"""
SonarrClient - Integration with Sonarr TV series manager

This module provides integration with Sonarr to retrieve the original
sceneName of downloaded episodes. When Sonarr renames a file, the original
release name is preserved in episodeFile.sceneName.

Usage Example:
    client = SonarrClient(
        base_url="http://localhost:8989",
        api_key="your-api-key"
    )

    scene_name = await client.find_scene_name_by_path("/data/tv/Breaking Bad/Season 01/Breaking Bad - S01E01.mkv")
    # Returns: "Breaking.Bad.S01E01.720p.BluRay.x264-GROUP"
"""

import asyncio
import logging
import os
from typing import Dict, List, Any, Optional
from urllib.parse import urljoin

import httpx

from .exceptions import NetworkRetryableError, retry_on_network_error

logger = logging.getLogger(__name__)


class SonarrError(Exception):
    """Exception raised when Sonarr API call fails."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class SonarrClient:
    """
    Client for Sonarr API integration.

    Provides sceneName lookup for episode files managed by Sonarr.
    When Sonarr downloads and renames a file, it stores the original
    release name (sceneName) in the episodeFile record.

    Attributes:
        base_url: Sonarr instance URL (e.g., http://localhost:8989)
        api_key: Sonarr API key (from Settings -> General -> Security)
        timeout: Request timeout in seconds
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        logger.debug(f"SonarrClient initialized for: {self.base_url}")

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
                        f"Sonarr service temporarily unavailable (HTTP {response.status_code})"
                    )

                if response.status_code >= 400:
                    raise SonarrError(
                        f"Sonarr API error: {response.status_code}",
                        status_code=response.status_code
                    )

                return response.json()

        except httpx.TimeoutException as e:
            raise NetworkRetryableError(f"Request timeout to {url}") from e
        except httpx.ConnectError as e:
            raise NetworkRetryableError(f"Connection error to {url}") from e
        except httpx.HTTPError as e:
            raise SonarrError(f"HTTP error: {e}")

    async def health_check(self) -> Dict[str, Any]:
        """
        Check Sonarr health/connectivity.

        Returns:
            Dict with 'healthy' boolean and 'version' string
        """
        try:
            status = await self._request('GET', '/api/v3/system/status')
            return {
                'healthy': True,
                'version': status.get('version', 'unknown'),
                'app_name': status.get('appName', 'Sonarr'),
                'url': self.base_url
            }
        except SonarrError as e:
            return {'healthy': False, 'error': str(e), 'url': self.base_url}

    async def get_all_episode_files(self) -> List[Dict[str, Any]]:
        """
        Get all episode files from Sonarr.

        Tries the bulk endpoint first (/api/v3/episodefile without seriesId).
        If that's not supported, falls back to iterating series.

        Returns:
            List of episodeFile records
        """
        # Try bulk endpoint (works in Sonarr v3+)
        try:
            result = await self._request('GET', '/api/v3/episodefile')
            if isinstance(result, list):
                return result
        except Exception:
            pass

        # Fallback: parallel fetch across all series (max 10 concurrent)
        all_files: List[Dict[str, Any]] = []
        try:
            series_list = await self._request('GET', '/api/v3/series')
        except Exception as e:
            logger.warning(f"Sonarr: failed to get series list: {e}")
            return all_files

        sem = asyncio.Semaphore(10)

        async def _fetch_series(series_id: int) -> List[Dict[str, Any]]:
            async with sem:
                try:
                    files = await self._request(
                        'GET', '/api/v3/episodefile',
                        params={'seriesId': series_id}
                    )
                    return files if isinstance(files, list) else []
                except Exception as e:
                    logger.debug(f"Sonarr: failed to get files for series {series_id}: {e}")
                    return []

        series_ids = [s.get('id') for s in series_list if s.get('id')]
        logger.debug(f"Sonarr: fetching episode files for {len(series_ids)} series (parallel, max 10)")
        results = await asyncio.gather(*[_fetch_series(sid) for sid in series_ids])
        for chunk in results:
            all_files.extend(chunk)

        return all_files

    async def find_scene_name_by_path(self, file_path: str) -> Optional[str]:
        """
        Find the original sceneName for a file managed by Sonarr.

        Matches file_path against episodeFile.path in Sonarr's database.
        Falls back to basename comparison if exact path doesn't match.

        Args:
            file_path: Absolute path to the media file as seen by Seedarr

        Returns:
            Original scene release name, or None if not found / no sceneName.
        """
        try:
            episode_files = await self.get_all_episode_files()
        except Exception as e:
            logger.warning(f"SonarrClient: failed to fetch episode files: {e}")
            return None

        file_basename = os.path.basename(file_path)

        # Pass 1: exact path match
        for ep_file in episode_files:
            if ep_file.get('path') == file_path:
                scene_name = ep_file.get('sceneName')
                if scene_name:
                    logger.debug(f"Sonarr exact match for '{file_path}': sceneName='{scene_name}'")
                    return scene_name

        # Pass 2: basename match — collect all candidates, pick the most recently added
        candidates = []
        for ep_file in episode_files:
            sonarr_path = ep_file.get('path', '')
            if os.path.basename(sonarr_path) == file_basename:
                scene_name = ep_file.get('sceneName')
                if scene_name:
                    candidates.append((ep_file.get('dateAdded', ''), scene_name))

        if candidates:
            # Sort descending by dateAdded (ISO string comparison works for sorting)
            candidates.sort(key=lambda x: x[0], reverse=True)
            scene_name = candidates[0][1]
            logger.debug(
                f"Sonarr basename match for '{file_basename}': sceneName='{scene_name}'"
                f" ({len(candidates)} candidate(s), picked most recent)"
            )
            return scene_name

        logger.debug(f"Sonarr: no match found for '{file_path}'")
        return None
