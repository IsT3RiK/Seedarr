"""
LaCaleClient for Seedarr v2.0

This module handles La Cale tracker External API business logic including multipart form
data preparation, API key authentication, and upload execution.

La Cale External API Documentation: https://la-cale.space/api/external/docs

Key Features:
    - Multipart form-data preparation with CRITICAL repeated tags fields pattern
    - X-Api-Key header authentication for all requests
    - Upload execution with comprehensive error handling
    - Tag and category fetching from tracker API
    - Torrent search by text query or TMDB ID
    - Typed exception handling with retry logic

API Endpoints:
    - GET /api/external - Search torrents (q, tmdbId, cat params)
    - GET /api/external/meta - Get categories, tag groups, ungrouped tags
    - POST /api/external/upload - Upload torrent (multipart form-data)
    - GET /api/torrents/download/<infoHash> - Download torrent file

Authentication:
    - All requests: X-Api-Key header (recommended)
    - Alternative: apikey query param for GET requests

CRITICAL Implementation Notes:
    - Tags MUST be sent as repeated form fields [('tags', 'ID1'), ('tags', 'ID2')]
    - tmdbType must be uppercase: "MOVIE" or "TV"
    - Torrent file must contain source flag "lacale"

Usage:
    client = LaCaleClient(
        tracker_url="https://la-cale.space",
        api_key="your_api_key_here"
    )

    # Upload torrent with session from CloudflareSessionManager
    result = await client.upload_torrent(
        session=authenticated_session,
        torrent_data=torrent_bytes,
        release_name="Movie.2023.1080p.BluRay.x264",
        category_id="cat_films",
        tag_ids=["tag_1080p", "tag_bluray"],
        nfo_data=nfo_bytes
    )
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
import requests
from requests import Session

from .exceptions import (
    TrackerAPIError,
    NetworkRetryableError,
    retry_on_network_error,
    classify_http_error
)
from .rate_limiter import rate_limited
from app.config import config

logger = logging.getLogger(__name__)


class LaCaleClient:
    """
    La Cale tracker External API client for business logic operations.

    This class handles all La Cale tracker API interactions including:
        - Multipart form data preparation (with CRITICAL repeated tags fields)
        - Torrent upload execution
        - Tag and category fetching
        - Torrent search by text or TMDB ID
        - X-Api-Key header authentication
        - API error handling and retry logic

    CRITICAL: This client implements La Cale's specific API requirements:
        - Tags MUST be sent as repeated form fields, NOT JSON arrays
        - Multipart form-data is required for uploads
        - X-Api-Key header authentication for all requests
        - tmdbType must be uppercase: "MOVIE" or "TV"

    Attributes:
        tracker_url: La Cale tracker base URL
        api_key: User's API key for authentication (X-Api-Key)
        upload_endpoint: Full URL for torrent upload endpoint
        meta_endpoint: Full URL for metadata API endpoint
        search_endpoint: Full URL for search API endpoint
    """

    # API endpoint paths (La Cale External API)
    UPLOAD_PATH = "/api/external/upload"
    META_PATH = "/api/external/meta"
    SEARCH_PATH = "/api/external"  # Search endpoint (same as base external API)

    def __init__(self, tracker_url: str, api_key: str = None, passkey: str = None):
        """
        Initialize LaCaleClient.

        Args:
            tracker_url: La Cale tracker base URL (e.g., https://la-cale.space)
            api_key: User's API key for authentication (X-Api-Key)
            passkey: Alias for api_key (for backwards compatibility)

        Note:
            Either api_key or passkey can be provided. They are the same value
            (the La Cale API key). passkey is kept for backwards compatibility.
        """
        self.tracker_url = tracker_url.rstrip('/')
        # Accept either api_key or passkey (backwards compatibility)
        self.api_key = api_key or passkey
        if not self.api_key:
            raise ValueError("Either api_key or passkey must be provided")

        # Construct API endpoints
        self.upload_endpoint = f"{self.tracker_url}{self.UPLOAD_PATH}"
        self.meta_endpoint = f"{self.tracker_url}{self.META_PATH}"
        self.search_endpoint = f"{self.tracker_url}{self.SEARCH_PATH}"

        logger.info(f"LaCaleClient initialized for tracker: {self.tracker_url}")

    def _get_auth_headers(self) -> Dict[str, str]:
        """
        Get authentication headers for API requests.

        Returns:
            Dictionary with X-Api-Key header
        """
        return {"X-Api-Key": self.api_key}

    def _prepare_multipart_data(
        self,
        release_name: str,
        category_id: str,
        tag_ids: List[str],
        description: Optional[str] = None,
        tmdb_id: Optional[str] = None,
        tmdb_type: Optional[str] = None,
        cover_url: Optional[str] = None
    ) -> List[tuple]:
        """
        Prepare multipart form data for La Cale upload API.

        Uses La Cale External API parameter names:
        - title: Release name (required)
        - categoryId: Category ID (required)
        - tags: Tag IDs (repeated field)
        - description: Optional description
        - tmdbId: Optional TMDB ID
        - tmdbType: Optional TMDB type (MOVIE/TV - uppercase)
        - coverUrl: Optional cover image URL

        Note: Authentication is via X-Api-Key header, not form data.

        Args:
            release_name: Release name/title
            category_id: Tracker category ID
            tag_ids: List of tracker tag IDs
            description: Optional description/plot
            tmdb_id: Optional TMDB ID
            tmdb_type: Optional TMDB type (MOVIE or TV)
            cover_url: Optional cover image URL

        Returns:
            List of tuples for multipart form-data: [('field', 'value'), ...]

        Example:
            data = client._prepare_multipart_data(
                release_name="Movie.2023.1080p",
                category_id="cat_films",
                tag_ids=["tag_1080p", "tag_bluray"]
            )
            # Returns: [('title', 'Movie.2023.1080p'), ('categoryId', 'cat_films'),
            #           ('tags', 'tag_1080p'), ('tags', 'tag_bluray'), ...]
        """
        logger.debug(f"Preparing multipart data for release: {release_name}")

        # Start with required fields (using La Cale API parameter names)
        # Note: Authentication via X-Api-Key header (handled in upload_torrent method)
        data = [
            ('title', release_name),
            ('categoryId', category_id)
        ]

        # Add tags as repeated 'tags' fields (per La Cale API docs)
        # Tags expect IDs (not slugs) per docs
        for tag in tag_ids:
            data.append(('tags', str(tag)))
            logger.debug(f"Added tag as repeated field: {tag}")

        # Add optional fields if provided
        if description:
            data.append(('description', description))

        if tmdb_id:
            data.append(('tmdbId', str(tmdb_id)))

        if tmdb_type:
            # tmdbType must be uppercase: MOVIE or TV
            data.append(('tmdbType', tmdb_type.upper() if tmdb_type else None))

        if cover_url:
            data.append(('coverUrl', cover_url))

        logger.info(
            f"Multipart data prepared: {len(data)} fields, "
            f"{len(tag_ids)} tags as repeated fields"
        )

        return data

    @rate_limited(service="tracker", tokens=1)
    @retry_on_network_error(max_retries=3)
    async def upload_torrent(
        self,
        session: Session,
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
        Upload .torrent file with metadata to La Cale tracker.

        This method prepares the multipart form data according to La Cale's External API
        requirements and executes the upload.

        Args:
            session: Authenticated requests.Session (from CloudflareSessionManager)
            torrent_data: Raw .torrent file bytes (must be private torrent)
            release_name: Release name/title
            category_id: Tracker category ID
            tag_ids: List of tracker tag IDs
            nfo_data: NFO file content as bytes (required, min 50 chars)
            description: Optional description/plot summary
            tmdb_id: Optional TMDB ID
            tmdb_type: Optional TMDB type (movie or tv)
            cover_url: Optional cover image URL

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
            TrackerAPIError: If upload fails due to invalid data (non-retryable)
            NetworkRetryableError: If network/connectivity issues occur (retryable)

        Example:
            result = await client.upload_torrent(
                session=auth_session,
                torrent_data=torrent_bytes,
                release_name="Movie.2023.1080p.BluRay.x264",
                category_id="1",
                tag_ids=["10", "15", "20"],
                nfo_data=nfo_bytes
            )
        """
        logger.info(f"Uploading torrent to La Cale: {release_name}")

        # Validate required fields
        if not torrent_data:
            raise TrackerAPIError("Torrent data is empty")

        if not release_name:
            raise TrackerAPIError("Release name is required")

        if not category_id:
            raise TrackerAPIError("Category ID is required")

        if not nfo_data or len(nfo_data) < 50:
            raise TrackerAPIError("NFO file is required and must be at least 50 characters")

        # Log upload parameters
        logger.info(f"Upload parameters:")
        logger.info(f"  - release_name: {release_name}")
        logger.info(f"  - category_id: {category_id}")
        logger.info(f"  - tag_ids: {tag_ids if tag_ids else '(none)'}")
        logger.info(f"  - tmdb_id: {tmdb_id}")
        logger.info(f"  - tmdb_type: {tmdb_type}")
        logger.info(f"  - description length: {len(description) if description else 0}")
        logger.info(f"  - cover_url: {cover_url}")
        logger.info(f"  - torrent_data size: {len(torrent_data)} bytes")
        logger.info(f"  - nfo_data size: {len(nfo_data)} bytes")

        # Warn if no tags
        if not tag_ids:
            logger.warning("⚠ No tags provided for upload - tracker may require tags!")

        # Prepare multipart form data
        data = self._prepare_multipart_data(
            release_name=release_name,
            category_id=category_id,
            tag_ids=tag_ids if tag_ids else [],
            description=description,
            tmdb_id=tmdb_id,
            tmdb_type=tmdb_type,
            cover_url=cover_url
        )

        # Prepare files for upload (using La Cale API parameter names)
        files = {
            'file': ('torrent.torrent', torrent_data, 'application/x-bittorrent'),
            'nfoFile': ('release.nfo', nfo_data, 'text/plain')
        }

        logger.debug(
            f"Uploading to {self.upload_endpoint} with {len(data)} form fields, "
            f"{len(files)} files"
        )

        # DEBUG: Log complete upload request details
        logger.info("=" * 80)
        logger.info("UPLOAD REQUEST TO TRACKER API")
        logger.info("=" * 80)
        logger.info(f"Endpoint: POST {self.upload_endpoint}")
        logger.info(f"Content-Type: multipart/form-data")
        logger.info("")
        logger.info("Form Fields:")
        logger.info("-" * 80)
        for field_name, field_value in data:
            if field_name == 'passkey':
                logger.info(f"  {field_name:20} = {field_value[:10]}...{field_value[-4:]} (hidden)")
            elif field_name == 'description':
                preview = field_value[:100] + '...' if len(field_value) > 100 else field_value
                logger.info(f"  {field_name:20} = {preview}")
            else:
                logger.info(f"  {field_name:20} = {field_value}")

        logger.info("")
        logger.info("Files:")
        logger.info("-" * 80)
        for file_field, (filename, file_data, content_type) in files.items():
            file_size_kb = len(file_data) / 1024 if file_data else 0
            logger.info(f"  {file_field:20} = {filename}")
            logger.info(f"    {'':20}   Size: {file_size_kb:.2f} KB ({len(file_data)} bytes)")
            logger.info(f"    {'':20}   Content-Type: {content_type}")

            # Show file preview for text files (NFO)
            if file_field == 'nfoFile' and file_data:
                try:
                    preview = file_data.decode('utf-8', errors='ignore')[:200]
                    logger.info(f"    {'':20}   Preview: {preview[:100]}...")
                except:
                    pass

        logger.info("")
        logger.info("Equivalent curl command:")
        logger.info("-" * 80)
        curl_cmd = f"curl -X POST '{self.upload_endpoint}' \\\n"
        curl_cmd += f"  -H 'X-Api-Key: YOUR_API_KEY' \\\n"
        for field_name, field_value in data:
            escaped_value = str(field_value).replace("'", "'\\''")
            curl_cmd += f"  -F '{field_name}={escaped_value}' \\\n"
        for file_field, (filename, _, content_type) in files.items():
            curl_cmd += f"  -F '{file_field}=@{filename}' \\\n"
        logger.info(curl_cmd.rstrip(' \\\n'))

        logger.info("=" * 80)

        try:
            # Execute upload request (async wrapper for sync requests)
            # Authentication via X-Api-Key header
            response = await asyncio.to_thread(
                session.post,
                self.upload_endpoint,
                headers=self._get_auth_headers(),
                data=data,  # Multipart form data with repeated tags fields
                files=files,
                timeout=config.API_REQUEST_TIMEOUT
            )

            # Log response details
            logger.debug(
                f"Upload response: HTTP {response.status_code}, "
                f"Content-Length: {len(response.content)}"
            )

            # Handle HTTP errors
            if response.status_code != 200:
                error_msg = f"Upload failed with HTTP {response.status_code}"

                # Try to parse error response - log full response for debugging
                response_data = {}
                try:
                    response_data = response.json()
                    logger.error(f"Tracker API error response (JSON): {response_data}")
                    if 'message' in response_data:
                        error_msg = f"{error_msg}: {response_data['message']}"
                    elif 'error' in response_data:
                        error_msg = f"{error_msg}: {response_data['error']}"
                    elif 'errors' in response_data:
                        error_msg = f"{error_msg}: {response_data['errors']}"
                except Exception:
                    logger.error(f"Tracker API error response (raw): {response.text[:500]}")
                    error_msg = f"{error_msg}: {response.text[:200]}"

                logger.error(f"Upload error: {error_msg}")
                logger.error(f"Upload params: release_name={release_name}, category_id={category_id}, tags={tag_ids}")

                # Classify error as retryable or non-retryable
                raise classify_http_error(
                    status_code=response.status_code,
                    message=error_msg,
                    response_data=response_data if 'response_data' in locals() else None
                )

            # Parse successful response
            try:
                response_data = response.json()
            except Exception as e:
                logger.error(f"Failed to parse upload response as JSON: {e}")
                raise TrackerAPIError(
                    "Invalid JSON response from tracker",
                    status_code=response.status_code,
                    response_data={'raw_text': response.text[:500]}
                )

            # Extract torrent info from response
            # New API returns: success, id, slug, link
            torrent_id = response_data.get('id', response_data.get('torrent_id', 'unknown'))
            torrent_slug = response_data.get('slug', '')
            torrent_url = response_data.get('link', response_data.get('torrent_url', f"{self.tracker_url}/torrents/{torrent_slug or torrent_id}"))

            result = {
                'success': response_data.get('success', True),
                'torrent_id': str(torrent_id),
                'torrent_slug': torrent_slug,
                'torrent_url': torrent_url,
                'message': response_data.get('message', 'Upload successful'),
                'response_data': response_data
            }

            logger.info(
                f"Successfully uploaded torrent: {release_name} "
                f"(ID: {torrent_id}, URL: {torrent_url})"
            )

            return result

        except requests.exceptions.Timeout as e:
            error_msg = f"Upload request timeout after {config.API_REQUEST_TIMEOUT}s"
            logger.error(f"{error_msg}: {e}")
            raise NetworkRetryableError(error_msg, original_exception=e)

        except requests.exceptions.ConnectionError as e:
            error_msg = f"Failed to connect to tracker at {self.upload_endpoint}"
            logger.error(f"{error_msg}: {e}")
            raise NetworkRetryableError(error_msg, original_exception=e)

        except requests.exceptions.RequestException as e:
            error_msg = f"Upload request failed: {type(e).__name__}"
            logger.error(f"{error_msg}: {e}")
            raise NetworkRetryableError(error_msg, original_exception=e)

        except (TrackerAPIError, NetworkRetryableError):
            # Re-raise our custom exceptions
            raise

        except Exception as e:
            error_msg = f"Unexpected error during upload: {type(e).__name__}"
            logger.error(f"{error_msg}: {e}", exc_info=True)
            raise TrackerAPIError(error_msg)

    @rate_limited(service="tracker", tokens=1)
    @retry_on_network_error(max_retries=3)
    async def get_metadata(self, session: Session) -> Dict[str, Any]:
        """
        Fetch metadata (categories, tag groups, tags) from La Cale tracker API.

        Args:
            session: Authenticated requests.Session

        Returns:
            Dictionary with categories, tagGroups, ungroupedTags

        Raises:
            NetworkRetryableError: If network issues occur (retryable)
            TrackerAPIError: If API returns error (non-retryable)
        """
        logger.info(f"Fetching metadata from La Cale: {self.meta_endpoint}")

        try:
            response = await asyncio.to_thread(
                session.get,
                self.meta_endpoint,
                headers=self._get_auth_headers(),
                timeout=config.API_REQUEST_TIMEOUT
            )

            if response.status_code != 200:
                error_msg = f"Failed to fetch metadata: HTTP {response.status_code}"
                logger.error(error_msg)
                raise classify_http_error(
                    status_code=response.status_code,
                    message=error_msg
                )

            # Parse metadata response
            metadata = response.json()
            logger.info(f"Successfully fetched metadata from La Cale")
            return metadata

        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to fetch metadata: {type(e).__name__}"
            logger.error(f"{error_msg}: {e}")
            raise NetworkRetryableError(error_msg, original_exception=e)

        except (TrackerAPIError, NetworkRetryableError):
            raise

        except Exception as e:
            error_msg = f"Unexpected error fetching metadata: {type(e).__name__}"
            logger.error(f"{error_msg}: {e}", exc_info=True)
            raise TrackerAPIError(error_msg)

    @retry_on_network_error(max_retries=3)
    async def get_tags(self, session: Session) -> List[Dict[str, Any]]:
        """
        Fetch available tags from La Cale tracker API.

        Args:
            session: Authenticated requests.Session

        Returns:
            List of tag dictionaries:
                [
                    {
                        'tag_id': str,
                        'label': str,
                        'group': str,
                        'description': str
                    },
                    ...
                ]

        Raises:
            NetworkRetryableError: If network issues occur (retryable)
            TrackerAPIError: If API returns error (non-retryable)

        Example:
            tags = await client.get_tags(session)
            for tag in tags:
                print(f"Tag: {tag['label']} (ID: {tag['tag_id']})")
        """
        logger.info(f"Fetching tags from La Cale: {self.meta_endpoint}")

        try:
            # Get metadata which contains tags
            metadata = await self.get_metadata(session)

            # Extract tags from tagGroups and standalone tags
            tags = []

            # Process tag groups
            for group in metadata.get('tagGroups', []):
                if group is None:
                    continue
                group_name = group.get('name', '')
                group_tags = group.get('tags', [])
                if group_tags is None:
                    continue
                for tag in group_tags:
                    if tag is None:
                        continue
                    tags.append({
                        'tag_id': str(tag.get('id', '')),
                        'label': tag.get('name', 'Unknown'),
                        'group': group_name,
                        'description': tag.get('description', '')
                    })

            # Process standalone tags
            standalone_tags = metadata.get('tags', [])
            if standalone_tags:
                for tag in standalone_tags:
                    if tag is None:
                        continue
                    tags.append({
                        'tag_id': str(tag.get('id', '')),
                        'label': tag.get('name', 'Unknown'),
                        'group': '',
                        'description': tag.get('description', '')
                    })

            logger.info(f"Successfully fetched {len(tags)} tags from La Cale")
            return tags

        except (TrackerAPIError, NetworkRetryableError):
            raise

        except Exception as e:
            error_msg = f"Unexpected error fetching tags: {type(e).__name__}"
            logger.error(f"{error_msg}: {e}", exc_info=True)
            raise TrackerAPIError(error_msg)

    @retry_on_network_error(max_retries=3)
    async def get_categories(self, session: Session) -> List[Dict[str, Any]]:
        """
        Fetch available categories from La Cale tracker API.

        Args:
            session: Authenticated requests.Session

        Returns:
            List of category dictionaries:
                [
                    {
                        'category_id': str,
                        'name': str,
                        'slug': str
                    },
                    ...
                ]

        Raises:
            NetworkRetryableError: If network issues occur (retryable)
            TrackerAPIError: If API returns error (non-retryable)

        Example:
            categories = await client.get_categories(session)
            for cat in categories:
                print(f"Category: {cat['name']} (ID: {cat['category_id']})")
        """
        logger.info(f"Fetching categories from La Cale: {self.meta_endpoint}")

        try:
            # Get metadata which contains categories
            metadata = await self.get_metadata(session)

            # Extract categories
            categories = []
            for cat in metadata.get('categories', []):
                categories.append({
                    'category_id': str(cat.get('id', '')),
                    'name': cat.get('name', 'Unknown'),
                    'slug': cat.get('slug', '')
                })

            logger.info(f"Successfully fetched {len(categories)} categories from La Cale")
            return categories

        except (TrackerAPIError, NetworkRetryableError):
            raise

        except Exception as e:
            error_msg = f"Unexpected error fetching categories: {type(e).__name__}"
            logger.error(f"{error_msg}: {e}", exc_info=True)
            raise TrackerAPIError(error_msg)

    async def validate_api_key(self, session: Session) -> bool:
        """
        Validate API key by making a lightweight API call.

        Args:
            session: Authenticated requests.Session

        Returns:
            True if API key is valid, False otherwise

        Example:
            is_valid = await client.validate_api_key(session)
            if is_valid:
                print("API key is valid")
        """
        try:
            # Try fetching metadata as a lightweight validation check
            await self.get_metadata(session)
            logger.info("API key validation successful")
            return True

        except TrackerAPIError as e:
            # 401/403 indicates invalid API key
            if e.status_code in (401, 403):
                logger.warning(f"API key validation failed: {e}")
                return False
            # Other errors might be temporary
            raise

        except Exception as e:
            logger.error(f"API key validation error: {e}")
            return False

    # Alias for backwards compatibility
    async def validate_passkey(self, session: Session) -> bool:
        """Alias for validate_api_key for backwards compatibility."""
        return await self.validate_api_key(session)

    @retry_on_network_error(max_retries=3)
    async def search_torrents(
        self,
        session: Session,
        query: str,
        search_type: str = "name",
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for torrents on La Cale.

        Uses the new La Cale External API format:
        - GET /api/external with X-Api-Key header + apikey query param
        - Parameters: apikey, q (text search), tmdbId (TMDB ID), cat (category slug)
        - Returns max 50 results sorted by date descending

        Args:
            session: Authenticated requests.Session (with FlareSolverr cookies)
            query: Search query (TMDB ID or text search)
            search_type: Type of search ('tmdb' or 'name')
            category: Optional category slug to filter (e.g., 'films', 'series')

        Returns:
            List of matching torrents with normalized format

        Raises:
            NetworkRetryableError: If network issues occur
            TrackerAPIError: If API returns error

        Note:
            IMDB search is not directly supported by the new API.
            Use TMDB ID or text search instead.
        """
        logger.info(f"Searching La Cale torrents: query={query}, type={search_type}, category={category}")

        try:
            # Build search parameters based on search type
            # Per Prowlarr YAML: apikey as query param, q always provided (default "FRENCH")
            params = {
                "apikey": self.api_key  # API key as query param (alternative auth)
            }

            if search_type == "tmdb":
                params["tmdbId"] = query
                # Per Prowlarr YAML: q defaults to "FRENCH" when searching by TMDB ID only
                params["q"] = "FRENCH"
            else:
                # Text search - use 'q' parameter (max 200 chars)
                params["q"] = query[:200] if query else "FRENCH"

            # Add category filter if provided
            if category:
                params["cat"] = category

            # Log params without exposing full API key
            log_params = {k: (v[:10] + '...' if k == 'apikey' else v) for k, v in params.items()}
            logger.info(f"La Cale search request: GET {self.search_endpoint} params={log_params}")

            response = await asyncio.to_thread(
                session.get,
                self.search_endpoint,
                headers=self._get_auth_headers(),
                params=params,
                timeout=config.API_REQUEST_TIMEOUT
            )

            logger.info(f"La Cale search response: HTTP {response.status_code}, length={len(response.content)}")

            if response.status_code == 401:
                raise TrackerAPIError(
                    "La Cale authentication failed during search - check API key",
                    status_code=401
                )

            if response.status_code == 403:
                raise TrackerAPIError(
                    "La Cale search forbidden - invalid API key",
                    status_code=403
                )

            if response.status_code == 400:
                logger.warning(
                    f"La Cale search bad request (400): {response.text[:500]}"
                )
                # Try to parse error details
                try:
                    error_data = response.json()
                    logger.warning(f"La Cale search error details: {error_data}")
                except:
                    pass
                return []

            if response.status_code != 200:
                logger.warning(
                    f"La Cale search returned status {response.status_code}: "
                    f"{response.text[:500]}"
                )
                # Try to parse error details
                try:
                    error_data = response.json()
                    logger.warning(f"La Cale search error details: {error_data}")
                except:
                    pass
                return []

            # Parse response - new API returns JSON array directly
            try:
                # Log raw response for debugging
                logger.info(f"La Cale search raw response (first 500 chars): {response.text[:500]}")
                data = response.json()
                logger.info(f"La Cale search parsed response type: {type(data)}, length: {len(data) if isinstance(data, list) else 'N/A'}")

                # API returns a list of torrents directly
                if isinstance(data, list):
                    torrents = data
                    logger.info(f"La Cale API returned {len(torrents)} raw results")
                else:
                    logger.warning(f"La Cale API returned non-list response: {type(data)}")
                    torrents = []

                # Normalize torrent format from new API response
                # Per Prowlarr YAML: title, infoHash, link, size, pubDate, category, seeders, leechers
                results = []
                for t in torrents:
                    # Use infoHash as ID if guid is not present
                    torrent_id = t.get("guid") or t.get("id") or t.get("infoHash") or "unknown"
                    results.append({
                        "id": torrent_id,
                        "name": t.get("title", ""),
                        "size": t.get("size", 0),
                        "seeders": t.get("seeders", 0),
                        "leechers": t.get("leechers", 0),
                        "info_hash": t.get("infoHash"),
                        "category": t.get("category"),
                        "download_link": t.get("link"),
                        "pub_date": t.get("pubDate"),
                    })
                    # Debug log each result
                    logger.debug(f"  Parsed torrent: {t.get('title', 'N/A')[:50]} (hash={t.get('infoHash', 'N/A')[:16] if t.get('infoHash') else 'N/A'})")

                logger.info(f"La Cale search found {len(results)} torrents")
                if results:
                    logger.debug(f"First result: {results[0].get('name', 'N/A')}")
                return results

            except Exception as e:
                logger.error(f"Error parsing La Cale search response: {e}")
                logger.error(f"Raw response text: {response.text[:500]}")
                return []

        except (TrackerAPIError, NetworkRetryableError):
            raise

        except requests.exceptions.Timeout as e:
            error_msg = f"Search request timeout for La Cale"
            logger.error(f"{error_msg}: {e}")
            raise NetworkRetryableError(error_msg, original_exception=e)

        except requests.exceptions.ConnectionError as e:
            error_msg = f"Cannot connect to La Cale for search"
            logger.error(f"{error_msg}: {e}")
            raise NetworkRetryableError(error_msg, original_exception=e)

        except Exception as e:
            logger.error(f"Error searching La Cale: {type(e).__name__}: {e}")
            return []

    @property
    def passkey(self) -> str:
        """Alias for api_key for backwards compatibility."""
        return self.api_key

    def __repr__(self) -> str:
        """String representation of LaCaleClient."""
        return (
            f"<LaCaleClient(tracker_url='{self.tracker_url}', "
            f"api_key='***{self.api_key[-4:] if self.api_key else 'None'}')>"
        )
