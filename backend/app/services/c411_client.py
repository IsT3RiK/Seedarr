"""
C411 Tracker API Client for Seedarr v2.0

This module implements the API client for the C411 private tracker.
Unlike La Cale, C411 uses Bearer token authentication and does not
require Cloudflare bypass.

API Specification:
    - Upload Endpoint: POST https://c411.org/api/torrents
    - Authentication: Authorization: Bearer {API_KEY}
    - Announce URL: https://c411.org/announce/{passkey}

Required Fields (multipart/form-data):
    - torrent: .torrent file
    - nfo: NFO file
    - title: Release title (min 3 chars)
    - description: Description (min 20 chars, BBCode or HTML)
    - categoryId: Category ID (integer)
    - subcategoryId: Subcategory ID (integer)

Optional Fields:
    - options: JSON with optionTypeId -> optionValueId mappings
        - Type 1: Language (multi-select): 1=Anglais, 2=VFF, 4=Multi, 6=VFQ, 8=VOSTFR
        - Type 2: Quality: 10=BluRay 4K, 12=BluRay Remux, 25=WEB-DL 1080, etc.
        - Type 7: Season (TV): 118=Serie integrale, 121=S01, 122=S02, etc.
        - Type 6: Episode (TV): 96=Saison complete, 97=E01, 98=E02, etc.
    - tmdbData: Full TMDB metadata as JSON object
    - rawgData: RAWG metadata for games as JSON object
    - isExclusive: "true" for exclusive releases
    - uploaderNote: Note for moderators

Differences from La Cale:
    - No Cloudflare protection (no FlareSolverr needed)
    - Bearer token instead of passkey in form fields
    - Uses subcategoryId instead of tags
    - Simpler authentication flow

Usage:
    client = C411Client(
        tracker_url="https://c411.org",
        api_key="your_api_key",
        passkey="your_passkey"
    )

    result = await client.upload_torrent(
        torrent_data=torrent_bytes,
        release_name="Movie.2024.1080p.WEB.EAC3.x264-TP",
        category_id="1",
        subcategory_id="10",
        nfo_data=nfo_bytes,
        description="Movie description",
        options={"1": [4], "2": 25},
        tmdb_data={"id": 12345, "title": "Movie", ...}
    )
"""

import httpx
import json
import logging
import re
from typing import Dict, Any, Optional, List, Union

from .exceptions import TrackerAPIError, NetworkRetryableError, retry_on_network_error

logger = logging.getLogger(__name__)


def sanitize_release_name_for_c411(name: str) -> str:
    """
    Sanitize release name to conform to C411 naming requirements.

    C411 requires:
    - Dots (.) as word separators (no spaces, hyphens except before team, or underscores)
    - No parentheses or brackets

    Examples:
        "Olympus Has Fallen (2013) MULTi VFF 2160p" -> "Olympus.Has.Fallen.2013.MULTi.VFF.2160p"
        "Movie Name [2024] FRENCH 1080p-TEAM" -> "Movie.Name.2024.FRENCH.1080p-TEAM"

    Args:
        name: Original release name

    Returns:
        Sanitized release name conforming to C411 requirements
    """
    if not name:
        return name

    # Preserve the team tag (everything after the last hyphen if it looks like a team)
    team_match = re.search(r'-([A-Za-z0-9]+)$', name)
    team_suffix = ""
    name_without_team = name

    if team_match:
        team_suffix = f"-{team_match.group(1)}"
        name_without_team = name[:team_match.start()]

    # Remove parentheses and brackets but keep their content
    # (2013) -> 2013, [2024] -> 2024
    name_without_team = re.sub(r'[\(\[\{]([^\)\]\}]+)[\)\]\}]', r'\1', name_without_team)

    # Replace spaces, underscores, and standalone hyphens with dots
    # But don't touch hyphens that are part of codec names like DTS-HD or WEB-DL
    name_without_team = re.sub(r'[\s_]+', '.', name_without_team)

    # Replace multiple dots with single dot
    name_without_team = re.sub(r'\.+', '.', name_without_team)

    # Strip leading/trailing dots
    name_without_team = name_without_team.strip('.')

    result = f"{name_without_team}{team_suffix}"
    logger.debug(f"Sanitized release name for C411: '{name}' -> '{result}'")

    return result


class C411Client:
    """
    API client for C411 tracker.

    This client handles all C411 API interactions:
    - Torrent uploads with Bearer token authentication
    - Category and subcategory management
    - Error handling and response parsing

    Attributes:
        tracker_url: C411 tracker base URL
        api_key: API key for Bearer authentication
        passkey: Passkey for announce URL (not used for API auth)
        default_category_id: Default category for uploads
        default_subcategory_id: Default subcategory for uploads

    Example:
        >>> client = C411Client(
        ...     tracker_url="https://c411.org",
        ...     api_key="your_api_key"
        ... )
        >>> result = await client.upload_torrent(
        ...     torrent_data=torrent_bytes,
        ...     release_name="Movie.2024.1080p",
        ...     category_id="1",
        ...     subcategory_id="10",
        ...     nfo_data=nfo_bytes
        ... )
    """

    # API endpoints
    UPLOAD_ENDPOINT = "/api/torrents"
    CATEGORIES_ENDPOINT = "/api/categories"

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
        Initialize C411Client.

        Args:
            tracker_url: C411 tracker base URL (e.g., "https://c411.org")
            api_key: API key for Bearer token authentication
            passkey: Passkey for announce URL (optional, not used for API)
            default_category_id: Default category ID for uploads
            default_subcategory_id: Default subcategory ID for uploads
            timeout: HTTP request timeout in seconds
        """
        self.tracker_url = tracker_url.rstrip('/')
        self.api_key = api_key
        self.passkey = passkey
        self.default_category_id = default_category_id
        self.default_subcategory_id = default_subcategory_id
        self.timeout = timeout

    def _get_headers(self) -> Dict[str, str]:
        """
        Get HTTP headers with Bearer token authentication.

        Returns:
            Headers dictionary with Authorization header
        """
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def validate_api_key(self) -> bool:
        """
        Validate API key by making a test request.

        C411 doesn't have a dedicated validation endpoint, so we try
        to access the upload endpoint with a GET request. A 401/403
        means invalid credentials, while 405 (Method Not Allowed)
        means we reached the endpoint with valid credentials.

        Returns:
            True if API key is valid, False otherwise

        Raises:
            NetworkRetryableError: If network issues occur
        """
        logger.info("Validating C411 API key...")

        if not self.api_key:
            logger.warning("No API key configured")
            return False

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Try to access the upload endpoint - we expect 405 Method Not Allowed
                # if credentials are valid (since it only accepts POST)
                url = f"{self.tracker_url}{self.UPLOAD_ENDPOINT}"
                headers = self._get_headers()

                response = await client.get(url, headers=headers)

                logger.debug(f"C411 validation response: {response.status_code}")

                if response.status_code == 401:
                    logger.warning("C411 API key is invalid (401 Unauthorized)")
                    return False

                if response.status_code == 403:
                    logger.warning("C411 API key is forbidden (403 Forbidden)")
                    return False

                # 405 Method Not Allowed = endpoint exists and auth is valid
                # 200, 404, etc. = also means auth passed
                if response.status_code in (200, 405, 404, 400, 422):
                    logger.info(f"C411 API key validated successfully (status: {response.status_code})")
                    return True

                logger.warning(
                    f"Unexpected response validating C411 API key: "
                    f"{response.status_code}"
                )
                return True  # Assume valid if not explicitly rejected

        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to C411: {e}"
            logger.error(error_msg)
            raise NetworkRetryableError(error_msg)

        except httpx.TimeoutException as e:
            error_msg = f"Timeout connecting to C411: {e}"
            logger.error(error_msg)
            raise NetworkRetryableError(error_msg)

        except Exception as e:
            logger.error(f"Error validating C411 API key: {e}")
            return False

    @retry_on_network_error(max_retries=3)
    async def upload_torrent(
        self,
        torrent_data: bytes,
        release_name: str,
        category_id: str,
        subcategory_id: str,
        nfo_data: bytes,
        description: Optional[str] = None,
        options: Optional[Dict[str, Union[int, List[int]]]] = None,
        tmdb_data: Optional[Dict[str, Any]] = None,
        rawg_data: Optional[Dict[str, Any]] = None,
        is_exclusive: bool = False,
        uploader_note: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Upload a torrent to C411.

        This method uploads a .torrent file with metadata to C411 tracker
        using Bearer token authentication.

        Args:
            torrent_data: Raw .torrent file bytes
            release_name: Release name/title (min 3 chars)
            category_id: Category ID (integer as string)
            subcategory_id: Subcategory ID (integer as string)
            nfo_data: NFO file content as bytes
            description: Description text (min 20 chars, BBCode or HTML)
            options: Options dict with optionTypeId -> optionValueId mappings
                Example: {"1": [2, 4], "2": 25, "7": 121, "6": 96}
                - Type 1: Language (multi-select): 1=Anglais, 2=VFF, 4=Multi, 6=VFQ, 8=VOSTFR
                - Type 2: Quality: 10=BluRay 4K, 12=BluRay Remux, 25=WEB-DL 1080, etc.
                - Type 7: Season: 118=Serie integrale, 121=S01, 122=S02, etc.
                - Type 6: Episode: 96=Saison complete, 97=E01, 98=E02, etc.
            tmdb_data: Full TMDB metadata as dict (will be JSON serialized)
            rawg_data: RAWG metadata for games as dict (will be JSON serialized)
            is_exclusive: True for exclusive releases
            uploader_note: Note for moderators
            **kwargs: Additional fields (ignored)

        Returns:
            Dictionary with upload result:
            {
                'success': bool,
                'torrent_id': str,
                'torrent_url': str,
                'message': str,
                'response_data': dict
            }

        Raises:
            TrackerAPIError: If upload fails
            NetworkRetryableError: If network issues occur
        """
        # Sanitize release name for C411 requirements (dots as separators, no parentheses)
        original_name = release_name
        release_name = sanitize_release_name_for_c411(release_name)
        if release_name != original_name:
            logger.info(f"Sanitized release name for C411: '{original_name}' -> '{release_name}'")

        logger.info(f"Uploading torrent to C411: {release_name}")

        # Validate required fields
        if len(release_name) < 3:
            raise TrackerAPIError(
                f"Release name too short (min 3 chars): {release_name}"
            )

        # Generate description if not provided
        if not description or len(description) < 20:
            description = f"Release: {release_name}\n\nUploaded by Seedarr v2.0"
            if len(description) < 20:
                description = description + " " * (20 - len(description))

        # Use defaults if not specified
        category_id = category_id or self.default_category_id
        subcategory_id = subcategory_id or self.default_subcategory_id

        if not category_id or not subcategory_id:
            raise TrackerAPIError(
                "Category ID and Subcategory ID are required for C411 uploads"
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Prepare multipart form data
                files = {
                    "torrent": (
                        f"{release_name}.torrent",
                        torrent_data,
                        "application/x-bittorrent"
                    ),
                    "nfo": (
                        f"{release_name}.nfo",
                        nfo_data,
                        "text/plain"
                    ),
                }

                data = {
                    "title": release_name,
                    "description": description,
                    "categoryId": int(category_id),
                    "subcategoryId": int(subcategory_id),
                }

                # Add optional fields
                if options:
                    data["options"] = json.dumps(options)
                    logger.debug(f"C411 options: {options}")

                if tmdb_data:
                    data["tmdbData"] = json.dumps(tmdb_data)
                    logger.debug(f"C411 tmdbData: id={tmdb_data.get('id')}, title={tmdb_data.get('title')}")

                    # Also send genres as separate fields (C411 may expect this in different formats)
                    genres = tmdb_data.get('genres', [])
                    if genres:
                        # Try sending as JSON array of {id, name} objects
                        data["genres"] = json.dumps(genres)
                        # Also try sending just the IDs as a list (in case C411 expects that)
                        genre_ids = [g.get('id') for g in genres if isinstance(g, dict) and g.get('id')]
                        if genre_ids:
                            data["genreIds"] = json.dumps(genre_ids)
                        logger.debug(f"C411 genres: {genres}, genreIds: {genre_ids}")

                if rawg_data:
                    data["rawgData"] = json.dumps(rawg_data)
                    logger.debug(f"C411 rawgData: id={rawg_data.get('id')}, name={rawg_data.get('name')}")

                if is_exclusive:
                    data["isExclusive"] = "true"
                    logger.debug("C411 isExclusive: true")

                if uploader_note:
                    data["uploaderNote"] = uploader_note
                    logger.debug(f"C411 uploaderNote: {uploader_note[:50]}...")

                logger.debug(f"C411 upload data: {data}")
                logger.debug(f"C411 upload files: torrent={len(torrent_data)} bytes, nfo={len(nfo_data)} bytes")

                # Make upload request
                response = await client.post(
                    f"{self.tracker_url}{self.UPLOAD_ENDPOINT}",
                    headers=self._get_headers(),
                    files=files,
                    data=data
                )

                logger.debug(f"C411 response status: {response.status_code}")
                logger.debug(f"C411 response body: {response.text[:500]}")

                # Parse response
                if response.status_code == 401:
                    raise TrackerAPIError(
                        "C411 authentication failed - invalid API key",
                        status_code=401
                    )

                if response.status_code == 403:
                    raise TrackerAPIError(
                        "C411 upload forbidden - check permissions",
                        status_code=403
                    )

                if response.status_code == 422:
                    # Validation error
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('message', str(error_data))
                    except Exception:
                        error_msg = response.text
                    raise TrackerAPIError(
                        f"C411 validation error: {error_msg}",
                        status_code=422
                    )

                if response.status_code not in (200, 201):
                    # Extract user-friendly message from JSON response
                    friendly_msg = None
                    try:
                        error_data = response.json()
                        raw_msg = error_data.get('message', '')

                        # Simplify known C411 error messages
                        if response.status_code == 429 and 'en attente de validation' in raw_msg:
                            # Extract pending count and limit
                            import re
                            pending = re.search(r'(\d+) torrent\(s\) en attente', raw_msg)
                            limit = re.search(r'limite\s*:\s*(\d+)', raw_msg)
                            p = pending.group(1) if pending else "?"
                            l = limit.group(1) if limit else "?"
                            friendly_msg = (
                                f"Upload impossible : {p} torrent(s) en attente de validation "
                                f"(limite : {l}). Attendez la validation par la Team Pending."
                            )
                        else:
                            friendly_msg = raw_msg
                    except Exception:
                        pass

                    if friendly_msg:
                        raise TrackerAPIError(
                            friendly_msg,
                            status_code=response.status_code
                        )
                    raise TrackerAPIError(
                        f"C411 upload failed with status {response.status_code}: "
                        f"{response.text[:200]}",
                        status_code=response.status_code
                    )

                # Parse successful response
                try:
                    response_data = response.json()
                except Exception:
                    response_data = {"raw": response.text}

                # Extract torrent ID and URL from response
                torrent_id = str(response_data.get('id', response_data.get('torrent_id', '')))
                torrent_url = response_data.get('url', '')

                if not torrent_url and torrent_id:
                    torrent_url = f"{self.tracker_url}/torrents/{torrent_id}"

                logger.info(
                    f"Successfully uploaded to C411: "
                    f"ID={torrent_id}, URL={torrent_url}"
                )

                return {
                    'success': True,
                    'torrent_id': torrent_id,
                    'torrent_url': torrent_url,
                    'message': 'Upload successful',
                    'response_data': response_data
                }

        except TrackerAPIError:
            raise

        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to C411: {e}"
            logger.error(error_msg)
            raise NetworkRetryableError(error_msg)

        except httpx.TimeoutException as e:
            error_msg = f"Timeout uploading to C411: {e}"
            logger.error(error_msg)
            raise NetworkRetryableError(error_msg)

        except Exception as e:
            error_msg = f"Unexpected error uploading to C411: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    @retry_on_network_error(max_retries=3)
    async def get_categories(self) -> List[Dict[str, Any]]:
        """
        Fetch available categories from C411.

        Returns:
            List of category dictionaries

        Raises:
            NetworkRetryableError: If network issues occur
            TrackerAPIError: If API returns error
        """
        logger.info("Fetching C411 categories...")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.tracker_url}{self.CATEGORIES_ENDPOINT}",
                    headers=self._get_headers()
                )

                if response.status_code != 200:
                    raise TrackerAPIError(
                        f"Failed to fetch C411 categories: {response.status_code}",
                        status_code=response.status_code
                    )

                data = response.json()

                # C411 API wraps categories in a 'data' key
                if isinstance(data, dict) and 'data' in data:
                    categories = data['data']
                elif isinstance(data, list):
                    categories = data
                else:
                    categories = []

                logger.info(f"Fetched {len(categories)} categories from C411")
                return categories

        except TrackerAPIError:
            raise

        except httpx.HTTPError as e:
            error_msg = f"Network error fetching C411 categories: {e}"
            logger.error(error_msg)
            raise NetworkRetryableError(error_msg)

        except Exception as e:
            error_msg = f"Error fetching C411 categories: {e}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg)

    # Torznab API endpoint (used by Prowlarr, Sonarr, Radarr)
    # Note: Must end with "/" to avoid 301 redirect
    TORZNAB_ENDPOINT = "/api/"

    @retry_on_network_error(max_retries=3)
    async def search_torrents(
        self,
        query: str,
        search_type: str = "name"
    ) -> List[Dict[str, Any]]:
        """
        Search for torrents on C411 using the Torznab API.

        C411 exposes a Torznab-compatible API at /api that is used by
        Prowlarr, Sonarr, Radarr and other indexers.

        Args:
            query: Search query (TMDB ID, IMDB ID, or name)
            search_type: Type of search ('tmdb', 'imdb', 'name')

        Returns:
            List of matching torrents

        Raises:
            NetworkRetryableError: If network issues occur
            TrackerAPIError: If API returns error
        """
        logger.info(f"Searching C411 via Torznab API: query={query}, type={search_type}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Build Torznab search parameters
                params = {
                    "apikey": self.api_key,
                    "extended": "1",  # Get extended info
                }

                if search_type == "tmdb":
                    # Search by TMDB ID - use movie search with tmdbid parameter
                    params["t"] = "movie"
                    params["tmdbid"] = query
                elif search_type == "imdb":
                    # Search by IMDB ID - use movie search with imdbid parameter
                    imdb_id = query if str(query).startswith("tt") else f"tt{query}"
                    params["t"] = "movie"
                    params["imdbid"] = imdb_id
                else:
                    # Text search
                    params["t"] = "search"
                    params["q"] = query

                url = f"{self.tracker_url}{self.TORZNAB_ENDPOINT}"
                logger.debug(f"Torznab search URL: {url} with params: {params}")

                response = await client.get(url, params=params)

                logger.debug(f"C411 Torznab response: {response.status_code}")

                if response.status_code == 401:
                    raise TrackerAPIError(
                        "C411 authentication failed - invalid API key",
                        status_code=401
                    )

                if response.status_code == 403:
                    raise TrackerAPIError(
                        "C411 search forbidden - check permissions",
                        status_code=403
                    )

                if response.status_code != 200:
                    logger.warning(
                        f"C411 Torznab search returned status {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                    return []

                # Parse Torznab XML response
                logger.debug(f"Torznab response (first 500 chars): {response.text[:500]}")
                results = self._parse_torznab_response(response.text)
                logger.info(f"C411 Torznab search found {len(results)} torrents")

                if results:
                    logger.debug(f"First result: {results[0]}")

                return results

        except TrackerAPIError:
            raise

        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to C411 for search: {e}"
            logger.error(error_msg)
            raise NetworkRetryableError(error_msg)

        except httpx.TimeoutException as e:
            error_msg = f"Timeout searching C411: {e}"
            logger.error(error_msg)
            raise NetworkRetryableError(error_msg)

        except Exception as e:
            logger.error(f"Error searching C411: {type(e).__name__}: {e}")
            return []

    def _parse_torznab_response(self, xml_text: str) -> List[Dict[str, Any]]:
        """
        Parse Torznab XML response into a list of torrent dictionaries.

        Torznab responses are RSS-like XML with <item> elements containing
        torrent metadata in <torznab:attr> elements.

        Args:
            xml_text: Raw XML response text

        Returns:
            List of normalized torrent dictionaries
        """
        import xml.etree.ElementTree as ET

        results = []

        try:
            # Parse XML
            root = ET.fromstring(xml_text)

            # Find all items (torrents) - handle namespaces
            # Torznab uses RSS format: /rss/channel/item
            items = root.findall('.//item')

            if not items:
                # Try without namespace
                items = root.findall('channel/item')

            logger.debug(f"Found {len(items)} items in Torznab response")

            for item in items:
                torrent = {}

                # Standard RSS fields
                torrent['name'] = self._get_xml_text(item, 'title', '')
                torrent['link'] = self._get_xml_text(item, 'link', '')
                torrent['guid'] = self._get_xml_text(item, 'guid', '')
                torrent['pub_date'] = self._get_xml_text(item, 'pubDate', '')
                torrent['size'] = self._parse_int(self._get_xml_text(item, 'size', '0'))
                torrent['description'] = self._get_xml_text(item, 'description', '')

                # Parse torznab:attr elements for extended metadata
                # These have format: <torznab:attr name="seeders" value="10"/>
                for attr in item.findall('.//{http://torznab.com/schemas/2015/feed}attr'):
                    attr_name = attr.get('name', '')
                    attr_value = attr.get('value', '')

                    if attr_name == 'seeders':
                        torrent['seeders'] = self._parse_int(attr_value)
                    elif attr_name == 'peers':
                        torrent['leechers'] = self._parse_int(attr_value) - torrent.get('seeders', 0)
                    elif attr_name == 'grabs':
                        torrent['completions'] = self._parse_int(attr_value)
                    elif attr_name == 'size':
                        torrent['size'] = self._parse_int(attr_value)
                    elif attr_name == 'imdbid':
                        torrent['imdb_id'] = attr_value
                    elif attr_name == 'tmdbid':
                        torrent['tmdb_id'] = attr_value
                    elif attr_name == 'category':
                        torrent['category_id'] = attr_value
                    elif attr_name == 'downloadvolumefactor':
                        torrent['freeleech'] = float(attr_value) < 1.0
                    elif attr_name == 'id' or attr_name == 'guid':
                        torrent['id'] = attr_value

                # Also try without namespace (some implementations)
                for attr in item.findall('.//attr'):
                    attr_name = attr.get('name', '')
                    attr_value = attr.get('value', '')

                    if attr_name == 'seeders' and 'seeders' not in torrent:
                        torrent['seeders'] = self._parse_int(attr_value)
                    elif attr_name == 'peers' and 'leechers' not in torrent:
                        torrent['leechers'] = self._parse_int(attr_value) - torrent.get('seeders', 0)
                    elif attr_name == 'imdbid' and 'imdb_id' not in torrent:
                        torrent['imdb_id'] = attr_value
                    elif attr_name == 'tmdbid' and 'tmdb_id' not in torrent:
                        torrent['tmdb_id'] = attr_value

                # Extract ID from guid or link if not already set
                if not torrent.get('id'):
                    # Try to extract ID from link like /torrent/123/name
                    link = torrent.get('link', '') or torrent.get('guid', '')
                    id_match = re.search(r'/torrent/(\d+)', link)
                    if id_match:
                        torrent['id'] = id_match.group(1)

                if torrent.get('name'):
                    results.append(torrent)

            logger.debug(f"Parsed {len(results)} torrents from Torznab response")

        except ET.ParseError as e:
            logger.error(f"Failed to parse Torznab XML: {e}")
            logger.debug(f"XML content (first 500 chars): {xml_text[:500]}")

        except Exception as e:
            logger.error(f"Error parsing Torznab response: {type(e).__name__}: {e}")

        return results

    def _get_xml_text(self, element, tag: str, default: str = '') -> str:
        """Get text content of a child XML element."""
        child = element.find(tag)
        return child.text if child is not None and child.text else default

    def _parse_int(self, value: str) -> int:
        """Parse string to int, returning 0 on failure."""
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    @property
    def announce_url(self) -> Optional[str]:
        """
        Get the announce URL for C411.

        Format: https://c411.org/announce/{passkey}

        Returns:
            Announce URL or None if passkey not configured
        """
        if not self.passkey:
            return None
        return f"{self.tracker_url}/announce/{self.passkey}"
