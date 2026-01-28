"""
LaCaleClient for Seedarr v2.0

This module handles La Cale tracker API business logic including multipart form
data preparation, passkey authentication, and upload execution.

Key Features:
    - Multipart form-data preparation with CRITICAL repeated tags fields pattern
    - Passkey authentication for tracker uploads
    - Upload execution with comprehensive error handling
    - Tag and category fetching from tracker API
    - Typed exception handling with retry logic

CRITICAL Implementation Notes:
    - Tags MUST be sent as repeated form fields [('tags', 'ID1'), ('tags', 'ID2')]
    - NEVER use JSON arrays for tags - this will cause HTTP 500 errors
    - This is undocumented La Cale API behavior that MUST be preserved

Usage:
    client = LaCaleClient(
        tracker_url="https://lacale.example.com",
        passkey="your_passkey_here"
    )

    # Upload torrent with session from CloudflareSessionManager
    result = await client.upload_torrent(
        session=authenticated_session,
        torrent_data=torrent_bytes,
        release_name="Movie.2023.1080p.BluRay.x264",
        category_id="1",
        tag_ids=["10", "15", "20"],
        nfo_content="NFO content here"
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
    La Cale tracker API client for business logic operations.

    This class handles all La Cale tracker API interactions including:
        - Multipart form data preparation (with CRITICAL repeated tags fields)
        - Torrent upload execution
        - Tag and category fetching
        - Passkey authentication
        - API error handling and retry logic

    CRITICAL: This client implements La Cale's specific API requirements:
        - Tags MUST be sent as repeated form fields, NOT JSON arrays
        - Multipart form-data is required for uploads
        - Passkey authentication in API calls

    Attributes:
        tracker_url: La Cale tracker base URL
        passkey: User's tracker passkey for authentication
        upload_endpoint: Full URL for torrent upload endpoint
        tags_endpoint: Full URL for tags API endpoint
        categories_endpoint: Full URL for categories API endpoint
    """

    # API endpoint paths (La Cale External API)
    UPLOAD_PATH = "/api/external/upload"
    META_PATH = "/api/external/meta"

    def __init__(self, tracker_url: str, passkey: str):
        """
        Initialize LaCaleClient.

        Args:
            tracker_url: La Cale tracker base URL (e.g., https://lacale.example.com)
            passkey: User's tracker passkey for authentication
        """
        self.tracker_url = tracker_url.rstrip('/')
        self.passkey = passkey

        # Construct API endpoints
        self.upload_endpoint = f"{self.tracker_url}{self.UPLOAD_PATH}"
        self.meta_endpoint = f"{self.tracker_url}{self.META_PATH}"

        logger.info(f"LaCaleClient initialized for tracker: {self.tracker_url}")

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
        - title: Release name
        - categoryId: Category ID
        - tags[]: Tag IDs (repeated field)
        - description: Optional description
        - tmdbId: Optional TMDB ID
        - tmdbType: Optional TMDB type (movie/tv)
        - coverUrl: Optional cover image URL

        Args:
            release_name: Release name/title
            category_id: Tracker category ID
            tag_ids: List of tracker tag IDs
            description: Optional description/plot
            tmdb_id: Optional TMDB ID
            tmdb_type: Optional TMDB type (movie or tv)
            cover_url: Optional cover image URL

        Returns:
            List of tuples for multipart form-data: [('field', 'value'), ...]

        Example:
            data = client._prepare_multipart_data(
                release_name="Movie.2023.1080p",
                category_id="1",
                tag_ids=["10", "15", "20"]
            )
            # Returns: [('title', 'Movie.2023.1080p'), ('categoryId', '1'),
            #           ('tags[]', '10'), ('tags[]', '15'), ('tags[]', '20'), ...]
        """
        logger.debug(f"Preparing multipart data for release: {release_name}")

        # Start with required fields (using La Cale API parameter names)
        data = [
            ('passkey', self.passkey),  # Passkey authentication
            ('title', release_name),
            ('categoryId', category_id)
        ]

        # Add tags as repeated 'tags' fields (per La Cale API docs)
        # Note: tags should be tag slugs, not IDs
        for tag in tag_ids:
            data.append(('tags', str(tag)))
            logger.debug(f"Added tag as repeated field: {tag}")

        # Add optional fields if provided
        if description:
            data.append(('description', description))

        if tmdb_id:
            data.append(('tmdbId', str(tmdb_id)))

        if tmdb_type:
            data.append(('tmdbType', tmdb_type))

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
        for field_name, field_value in data:
            if field_name == 'passkey':
                curl_cmd += f"  -F '{field_name}=YOUR_PASSKEY' \\\n"
            else:
                escaped_value = str(field_value).replace("'", "'\\''")
                curl_cmd += f"  -F '{field_name}={escaped_value}' \\\n"
        for file_field, (filename, _, content_type) in files.items():
            curl_cmd += f"  -F '{file_field}=@{filename}' \\\n"
        logger.info(curl_cmd.rstrip(' \\\n'))

        logger.info("=" * 80)

        try:
            # Execute upload request (async wrapper for sync requests)
            response = await asyncio.to_thread(
                session.post,
                self.upload_endpoint,
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
            torrent_id = response_data.get('torrent_id', response_data.get('id', 'unknown'))
            torrent_url = response_data.get('torrent_url', f"{self.tracker_url}/torrents/{torrent_id}")

            result = {
                'success': True,
                'torrent_id': str(torrent_id),
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
            Dictionary with categories, tagGroups, and tags

        Raises:
            NetworkRetryableError: If network issues occur (retryable)
            TrackerAPIError: If API returns error (non-retryable)
        """
        logger.info(f"Fetching metadata from La Cale: {self.meta_endpoint}")

        try:
            response = await asyncio.to_thread(
                session.get,
                self.meta_endpoint,
                params={'passkey': self.passkey},
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

    async def validate_passkey(self, session: Session) -> bool:
        """
        Validate passkey by making a lightweight API call.

        Args:
            session: Authenticated requests.Session

        Returns:
            True if passkey is valid, False otherwise

        Example:
            is_valid = await client.validate_passkey(session)
            if is_valid:
                print("Passkey is valid")
        """
        try:
            # Try fetching metadata as a lightweight validation check
            await self.get_metadata(session)
            logger.info("Passkey validation successful")
            return True

        except TrackerAPIError as e:
            # 401/403 indicates invalid passkey
            if e.status_code in (401, 403):
                logger.warning(f"Passkey validation failed: {e}")
                return False
            # Other errors might be temporary
            raise

        except Exception as e:
            logger.error(f"Passkey validation error: {e}")
            return False

    # API endpoint for search
    SEARCH_PATH = "/api/external/search"

    @retry_on_network_error(max_retries=3)
    async def search_torrents(
        self,
        session: Session,
        query: str,
        search_type: str = "name"
    ) -> List[Dict[str, Any]]:
        """
        Search for torrents on La Cale.

        Args:
            session: Authenticated requests.Session (with FlareSolverr cookies)
            query: Search query (TMDB ID, IMDB ID, or name)
            search_type: Type of search ('tmdb', 'imdb', 'name')

        Returns:
            List of matching torrents

        Raises:
            NetworkRetryableError: If network issues occur
            TrackerAPIError: If API returns error
        """
        logger.info(f"Searching La Cale torrents: query={query}, type={search_type}")

        search_endpoint = f"{self.tracker_url}{self.SEARCH_PATH}"

        try:
            # Build search parameters based on search type
            params = {"passkey": self.passkey}

            if search_type == "tmdb":
                params["tmdbId"] = query
            elif search_type == "imdb":
                # Ensure tt prefix for IMDB
                imdb_id = query if query.startswith("tt") else f"tt{query}"
                params["imdbId"] = imdb_id
            else:
                # Name search - use the query directly
                params["name"] = query

            # Add limit
            params["perPage"] = 25

            response = await asyncio.to_thread(
                session.get,
                search_endpoint,
                params=params,
                timeout=config.API_REQUEST_TIMEOUT
            )

            logger.debug(f"La Cale search response: {response.status_code}")

            if response.status_code == 401:
                raise TrackerAPIError(
                    "La Cale authentication failed during search",
                    status_code=401
                )

            if response.status_code == 403:
                raise TrackerAPIError(
                    "La Cale search forbidden - check permissions",
                    status_code=403
                )

            if response.status_code != 200:
                logger.warning(
                    f"La Cale search returned status {response.status_code}: "
                    f"{response.text[:200]}"
                )
                return []

            # Parse response
            try:
                data = response.json()

                # Handle different response formats
                if isinstance(data, list):
                    torrents = data
                elif isinstance(data, dict):
                    # Common API patterns: data, results, torrents, items
                    torrents = (
                        data.get("data") or
                        data.get("results") or
                        data.get("torrents") or
                        data.get("items") or
                        []
                    )
                else:
                    torrents = []

                # Normalize torrent format
                results = []
                for t in torrents:
                    results.append({
                        "id": t.get("id"),
                        "name": t.get("name") or t.get("title") or t.get("release_name", ""),
                        "size": t.get("size", 0),
                        "seeders": t.get("seeders", 0),
                        "leechers": t.get("leechers", 0),
                        "tmdb_id": t.get("tmdb_id") or t.get("tmdbId"),
                        "imdb_id": t.get("imdb_id") or t.get("imdbId"),
                        "created_at": t.get("created_at") or t.get("createdAt"),
                    })

                logger.info(f"La Cale search found {len(results)} torrents")
                return results

            except Exception as e:
                logger.error(f"Error parsing La Cale search response: {e}")
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

    def __repr__(self) -> str:
        """String representation of LaCaleClient."""
        return (
            f"<LaCaleClient(tracker_url='{self.tracker_url}', "
            f"passkey='***{self.passkey[-4:] if self.passkey else 'None'}')>"
        )
