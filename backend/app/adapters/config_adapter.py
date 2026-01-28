"""
ConfigAdapter - Config-Driven Generic Tracker Adapter

This adapter uses YAML/JSON configuration to handle tracker operations,
eliminating the need to write custom adapter code for each tracker.

Architecture:
    TrackerAdapter (interface)
          ↑
          |
    ConfigAdapter (generic implementation)
          |
          └── Uses TrackerConfigLoader for configuration
          └── Uses OptionsMapper for metadata mapping
          └── Uses httpx for HTTP requests

Key Features:
    - Config-driven authentication (bearer, passkey, cookie, api_key)
    - Dynamic field mapping for uploads
    - Generic options mapping
    - Supports Cloudflare bypass via FlareSolverr
    - No code changes needed for new trackers

Usage:
    config = TrackerConfigLoader().load("c411")
    adapter = ConfigAdapter(
        config=config,
        tracker_url="https://c411.example.com",
        api_key="your_api_key"
    )
    await adapter.authenticate()
    result = await adapter.upload_torrent(...)
"""

import json
import logging
import re
from typing import Dict, List, Optional, Any, Union

import httpx
from requests import Session

from .tracker_adapter import TrackerAdapter
from .tracker_config_loader import TrackerConfigLoader, get_config_loader
from ..services.options_mapper import OptionsMapper
from ..services.exceptions import (
    TrackerAPIError,
    CloudflareBypassError,
    NetworkRetryableError
)

logger = logging.getLogger(__name__)


class ConfigAdapter(TrackerAdapter):
    """
    Configuration-driven tracker adapter.

    This adapter reads all behavior from a configuration dictionary,
    allowing new trackers to be added without writing Python code.

    Supported authentication types:
    - bearer: HTTP Authorization header with Bearer token
    - api_key: API key in header or query parameter
    - passkey: Passkey in URL or form data
    - cookie: Session cookie-based auth
    - none: No authentication required

    Supported field types:
    - file: File upload (bytes)
    - string: String value
    - json: JSON-encoded object
    - boolean: Boolean value
    - repeated: Multiple values with same key (e.g., tags[]=1&tags[]=2)
    - number: Numeric value
    """

    def __init__(
        self,
        config: Dict[str, Any],
        tracker_url: str,
        api_key: Optional[str] = None,
        passkey: Optional[str] = None,
        flaresolverr_url: Optional[str] = None,
        flaresolverr_timeout: int = 60000,
        timeout: int = 60,
        **kwargs
    ):
        """
        Initialize ConfigAdapter.

        Args:
            config: Tracker configuration dictionary (from YAML/JSON)
            tracker_url: Tracker base URL
            api_key: API key for bearer/api_key auth
            passkey: Passkey for passkey auth
            flaresolverr_url: FlareSolverr URL for Cloudflare bypass
            flaresolverr_timeout: FlareSolverr timeout in ms
            timeout: HTTP request timeout in seconds
            **kwargs: Additional tracker-specific parameters
        """
        self.config = config
        self.tracker_url = tracker_url.rstrip('/')
        self.api_key = api_key
        self.passkey = passkey
        self.flaresolverr_url = flaresolverr_url
        self.flaresolverr_timeout = flaresolverr_timeout
        self.timeout = timeout
        self.extra_config = kwargs

        # Initialize HTTP client
        self._client: Optional[httpx.AsyncClient] = None
        self._session: Optional[Session] = None
        self._authenticated = False

        # Initialize options mapper from config
        options_config = self.config.get("options", {})
        self.options_mapper = OptionsMapper(options_config)

        # Session manager for Cloudflare (lazy init)
        self._session_manager = None

        # Get tracker info from config
        tracker_config = self.config.get("tracker", {})
        self.tracker_name = tracker_config.get("name", "Unknown")
        self.tracker_slug = tracker_config.get("slug", "unknown")

        logger.info(
            f"ConfigAdapter initialized for {self.tracker_name} "
            f"(slug: {self.tracker_slug})"
        )

    @property
    def requires_cloudflare(self) -> bool:
        """Check if tracker requires Cloudflare bypass."""
        cloudflare_config = self.config.get("cloudflare", {})
        return cloudflare_config.get("enabled", False)

    @property
    def auth_type(self) -> str:
        """Get authentication type from config."""
        auth_config = self.config.get("auth", {})
        return auth_config.get("type", "none")

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            headers = self._build_auth_headers()
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=self.timeout,
                follow_redirects=True
            )
        return self._client

    def _build_auth_headers(self) -> Dict[str, str]:
        """Build authentication headers based on config."""
        auth_config = self.config.get("auth", {})
        auth_type = auth_config.get("type", "none")
        headers = {}

        if auth_type == "bearer":
            header_name = auth_config.get("header", "Authorization")
            prefix = auth_config.get("prefix", "Bearer ")
            if self.api_key:
                headers[header_name] = f"{prefix}{self.api_key}"

        elif auth_type == "api_key":
            header_name = auth_config.get("header", "X-API-Key")
            if self.api_key:
                headers[header_name] = self.api_key

        return headers

    def _build_url(self, endpoint_key: str) -> str:
        """Build full URL from endpoint key."""
        endpoints = self.config.get("endpoints", {})
        endpoint = endpoints.get(endpoint_key, "")

        # Handle base URL prefix
        base = endpoints.get("base", "")
        if base and not endpoint.startswith(base):
            endpoint = f"{base}/{endpoint.lstrip('/')}"

        return f"{self.tracker_url}{endpoint}"

    async def _init_cloudflare_session(self):
        """Initialize Cloudflare session manager if needed."""
        if self.requires_cloudflare and self.flaresolverr_url:
            from ..services.cloudflare_session_manager import CloudflareSessionManager

            if self._session_manager is None:
                self._session_manager = CloudflareSessionManager(
                    flaresolverr_url=self.flaresolverr_url,
                    max_timeout=self.flaresolverr_timeout
                )

            self._session = await self._session_manager.get_session(
                tracker_url=self.tracker_url
            )
            return True
        return False

    async def authenticate(self) -> bool:
        """
        Authenticate with the tracker.

        Handles different auth types:
        - bearer: API key already in headers
        - passkey: Verify passkey by making a test request
        - cookie: Use FlareSolverr to get session cookies
        """
        logger.info(f"Authenticating with {self.tracker_name}")

        try:
            # Handle Cloudflare bypass first
            if self.requires_cloudflare:
                if not self.flaresolverr_url:
                    raise TrackerAPIError(
                        f"{self.tracker_name} requires Cloudflare bypass but "
                        "FlareSolverr URL not configured"
                    )

                await self._init_cloudflare_session()
                logger.info("Cloudflare session established")

            # For bearer/api_key auth, just verify the key works
            if self.auth_type in ("bearer", "api_key"):
                if not self.api_key:
                    raise TrackerAPIError(
                        f"{self.tracker_name} requires API key for authentication"
                    )

                # Make a test request to verify credentials
                client = await self._get_client()
                health_url = self._build_url("health")

                if health_url == self.tracker_url:
                    # No health endpoint configured, use categories
                    health_url = self._build_url("categories")

                try:
                    response = await client.get(health_url)
                    if response.status_code == 401:
                        raise TrackerAPIError(
                            "Invalid API key - authentication rejected",
                            status_code=401
                        )
                    elif response.status_code == 403:
                        raise TrackerAPIError(
                            "Access forbidden - check API key permissions",
                            status_code=403
                        )
                except httpx.RequestError as e:
                    raise NetworkRetryableError(f"Network error during auth: {e}")

            # For passkey auth, verify passkey format
            elif self.auth_type == "passkey":
                if not self.passkey or len(self.passkey) < 10:
                    raise TrackerAPIError(
                        f"Invalid passkey format for {self.tracker_name}"
                    )

            self._authenticated = True
            logger.info(f"Successfully authenticated with {self.tracker_name}")
            return True

        except (TrackerAPIError, CloudflareBypassError, NetworkRetryableError):
            raise

        except Exception as e:
            error_msg = f"Authentication failed: {type(e).__name__}: {e}"
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
        Upload torrent using config-driven field mapping.

        The upload configuration determines:
        - Which fields to send
        - How to format each field
        - Field names and types
        """
        logger.info(f"Uploading torrent to {self.tracker_name}: {release_name}")

        if not self._authenticated:
            await self.authenticate()

        try:
            # Build form data from config
            upload_config = self.config.get("upload", {})
            fields_config = upload_config.get("fields", {})

            # Prepare data sources for field mapping
            data_sources = {
                "torrent": torrent_data,
                "nfo": nfo_data,
                "release_name": release_name,
                "category_id": category_id,
                "tag_ids": tag_ids,
                "description": description,
                "tmdb_id": tmdb_id,
                "tmdb_type": tmdb_type,
                "cover_url": cover_url,
                **kwargs
            }

            # Build multipart form data
            files = {}
            form_data = {}

            for field_name, field_config in fields_config.items():
                if isinstance(field_config, dict):
                    field_api_name = field_config.get("name", field_name)
                    field_type = field_config.get("type", "string")
                    source = field_config.get("source", field_name)
                    required = field_config.get("required", False)

                    # Get value from data sources
                    value = data_sources.get(source)

                    # Skip if no value and not required
                    if value is None:
                        if required:
                            raise TrackerAPIError(f"Missing required field: {source}")
                        continue

                    # Handle different field types
                    if field_type == "file":
                        if isinstance(value, bytes):
                            filename = f"{field_name}.torrent" if field_name == "torrent" else f"{field_name}.nfo"
                            files[field_api_name] = (filename, value)

                    elif field_type == "json":
                        form_data[field_api_name] = json.dumps(value)

                    elif field_type == "repeated":
                        # Handle repeated fields (e.g., tags[])
                        if isinstance(value, list):
                            for v in value:
                                if field_api_name not in form_data:
                                    form_data[field_api_name] = []
                                form_data[field_api_name].append(str(v))

                    elif field_type == "boolean":
                        form_data[field_api_name] = "true" if value else "false"

                    else:
                        # Apply sanitization if configured
                        sanitize = field_config.get("sanitize", {})
                        str_value = str(value)

                        if sanitize.get("replace_spaces"):
                            str_value = str_value.replace(" ", sanitize["replace_spaces"])

                        max_length = sanitize.get("max_length")
                        if max_length and len(str_value) > max_length:
                            str_value = str_value[:max_length]

                        form_data[field_api_name] = str_value

            # Make upload request
            upload_url = self._build_url("upload")
            client = await self._get_client()

            # Handle repeated fields for multipart
            data_for_request = {}
            for key, value in form_data.items():
                if isinstance(value, list):
                    # For httpx, repeated fields need special handling
                    for v in value:
                        if key.endswith("[]"):
                            data_for_request[key] = v  # Will be overwritten, handle differently
                        else:
                            data_for_request[f"{key}[]"] = v
                else:
                    data_for_request[key] = value

            response = await client.post(
                upload_url,
                data=data_for_request,
                files=files
            )

            # Parse response
            result = self._parse_upload_response(response)
            logger.info(f"Upload result: {result}")

            return result

        except (TrackerAPIError, NetworkRetryableError):
            raise

        except httpx.RequestError as e:
            raise NetworkRetryableError(f"Network error during upload: {e}")

        except Exception as e:
            error_msg = f"Upload failed: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    def _parse_upload_response(self, response: httpx.Response) -> Dict[str, Any]:
        """Parse upload response based on config."""
        response_config = self.config.get("response", {})

        result = {
            'success': False,
            'torrent_id': None,
            'torrent_url': None,
            'message': '',
            'response_data': {}
        }

        try:
            data = response.json()
            result['response_data'] = data

            # Check success field
            success_field = response_config.get("success_field", "success")
            result['success'] = self._get_nested_value(data, success_field, False)

            # Get torrent ID
            torrent_id_field = response_config.get("torrent_id_field", "data.id")
            result['torrent_id'] = str(self._get_nested_value(data, torrent_id_field, ""))

            # Build torrent URL
            if result['torrent_id']:
                url_template = response_config.get(
                    "torrent_url_template",
                    "{tracker_url}/torrent/{torrent_id}"
                )
                result['torrent_url'] = url_template.format(
                    tracker_url=self.tracker_url,
                    torrent_id=result['torrent_id']
                )

            # Get error message if failed
            if not result['success']:
                error_field = response_config.get("error_field", "error")
                result['message'] = str(self._get_nested_value(data, error_field, "Upload failed"))
            else:
                result['message'] = "Upload successful"

        except json.JSONDecodeError:
            result['message'] = f"Invalid JSON response: {response.text[:200]}"

        # Check HTTP status
        if response.status_code >= 400:
            result['success'] = False
            result['message'] = f"HTTP {response.status_code}: {result['message']}"

        return result

    def _get_nested_value(self, data: dict, path: str, default: Any = None) -> Any:
        """Get nested value from dict using dot notation."""
        keys = path.split('.')
        value = data

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    async def validate_credentials(self) -> bool:
        """Validate credentials based on auth type."""
        logger.info(f"Validating credentials for {self.tracker_name}")

        try:
            if self.auth_type in ("bearer", "api_key"):
                if not self.api_key:
                    return False
                # Try to authenticate
                await self.authenticate()
                return True

            elif self.auth_type == "passkey":
                return bool(self.passkey and len(self.passkey) >= 10)

            return True  # No auth required

        except TrackerAPIError:
            return False

        except (NetworkRetryableError, CloudflareBypassError):
            raise

        except Exception as e:
            logger.error(f"Credential validation error: {e}")
            return False

    async def get_tags(self) -> List[Dict[str, Any]]:
        """Fetch tags from tracker API."""
        logger.info(f"Fetching tags from {self.tracker_name}")

        if not self._authenticated:
            await self.authenticate()

        try:
            tags_url = self._build_url("tags")
            client = await self._get_client()

            response = await client.get(tags_url)
            response.raise_for_status()

            data = response.json()

            # Parse tags from response
            tags = []
            if isinstance(data, list):
                tags = data
            elif isinstance(data, dict):
                # Try common response patterns
                tags = data.get("tags") or data.get("data") or []

            # Normalize tag format
            normalized = []
            for tag in tags:
                if isinstance(tag, dict):
                    normalized.append({
                        'tag_id': str(tag.get('id') or tag.get('tag_id', '')),
                        'label': tag.get('name') or tag.get('label', ''),
                        'category': tag.get('category') or tag.get('group', ''),
                        'description': tag.get('description', '')
                    })

            logger.info(f"Fetched {len(normalized)} tags from {self.tracker_name}")
            return normalized

        except httpx.RequestError as e:
            raise NetworkRetryableError(f"Network error fetching tags: {e}")

        except Exception as e:
            error_msg = f"Failed to fetch tags: {e}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg)

    async def get_categories(self) -> List[Dict[str, Any]]:
        """Fetch categories from tracker API."""
        logger.info(f"Fetching categories from {self.tracker_name}")

        if not self._authenticated:
            await self.authenticate()

        try:
            categories_url = self._build_url("categories")
            client = await self._get_client()

            response = await client.get(categories_url)
            response.raise_for_status()

            data = response.json()

            # Parse categories from response
            categories = []
            if isinstance(data, list):
                categories = data
            elif isinstance(data, dict):
                categories = data.get("categories") or data.get("data") or []

            # Normalize category format
            normalized = []
            for cat in categories:
                if isinstance(cat, dict):
                    normalized.append({
                        'category_id': str(cat.get('id') or cat.get('category_id', '')),
                        'name': cat.get('name') or cat.get('label', ''),
                        'description': cat.get('description', '')
                    })

            logger.info(f"Fetched {len(normalized)} categories from {self.tracker_name}")
            return normalized

        except httpx.RequestError as e:
            raise NetworkRetryableError(f"Network error fetching categories: {e}")

        except Exception as e:
            error_msg = f"Failed to fetch categories: {e}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg)

    async def check_duplicate(
        self,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        release_name: Optional[str] = None,
        quality: Optional[str] = None
    ) -> Dict[str, Any]:
        """Check for duplicate releases on tracker."""
        logger.info(f"Checking duplicates on {self.tracker_name}")

        if not self._authenticated:
            await self.authenticate()

        result = {
            'is_duplicate': False,
            'existing_torrents': [],
            'search_method': 'none',
            'message': 'No duplicates found'
        }

        try:
            search_url = self._build_url("search")
            client = await self._get_client()

            # Try TMDB ID first
            if tmdb_id:
                response = await client.get(search_url, params={"tmdb": tmdb_id})
                if response.status_code == 200:
                    data = response.json()
                    torrents = self._parse_search_results(data)
                    if torrents:
                        result['existing_torrents'] = torrents
                        result['search_method'] = 'tmdb'
                        result['is_duplicate'] = True

            # Try IMDB ID
            if not result['is_duplicate'] and imdb_id:
                response = await client.get(search_url, params={"imdb": imdb_id})
                if response.status_code == 200:
                    data = response.json()
                    torrents = self._parse_search_results(data)
                    if torrents:
                        result['existing_torrents'] = torrents
                        result['search_method'] = 'imdb'
                        result['is_duplicate'] = True

            # Try release name
            if not result['is_duplicate'] and release_name:
                # Extract title from release name
                title = re.sub(r'[\.\s]+(19|20)\d{2}.*', '', release_name)
                title = title.replace('.', ' ')[:50]

                response = await client.get(search_url, params={"q": title})
                if response.status_code == 200:
                    data = response.json()
                    torrents = self._parse_search_results(data)
                    if torrents:
                        result['existing_torrents'] = torrents
                        result['search_method'] = 'name'
                        result['is_duplicate'] = True

            # Filter by quality if specified
            if result['is_duplicate'] and quality:
                quality_lower = quality.lower()
                filtered = [
                    t for t in result['existing_torrents']
                    if quality_lower in t.get('name', '').lower()
                ]
                if filtered:
                    result['existing_torrents'] = filtered
                else:
                    result['is_duplicate'] = False
                    result['existing_torrents'] = []

            if result['is_duplicate']:
                result['message'] = f"Found {len(result['existing_torrents'])} existing release(s)"
            else:
                result['message'] = "No duplicates found - safe to upload"

            return result

        except httpx.RequestError as e:
            raise NetworkRetryableError(f"Network error checking duplicates: {e}")

        except Exception as e:
            logger.warning(f"Duplicate check failed: {e}")
            return result

    def _parse_search_results(self, data: Any) -> List[Dict[str, Any]]:
        """Parse search results from API response."""
        results = []

        if isinstance(data, list):
            torrents = data
        elif isinstance(data, dict):
            torrents = data.get("torrents") or data.get("data") or []
        else:
            return results

        for t in torrents:
            if isinstance(t, dict):
                results.append({
                    'torrent_id': str(t.get('id', '')),
                    'name': t.get('name') or t.get('title', ''),
                    'url': t.get('url', ''),
                    'quality': t.get('quality', ''),
                    'uploaded_at': t.get('uploaded_at') or t.get('created_at', '')
                })

        return results

    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on adapter and dependencies."""
        logger.info(f"Health check for {self.tracker_name}")

        health = {
            'healthy': True,
            'tracker_reachable': False,
            'authenticated': self._authenticated,
            'flaresolverr_available': True,
            'circuit_breaker_state': 'closed',
            'details': {}
        }

        # Check FlareSolverr if required
        if self.requires_cloudflare:
            if not self.flaresolverr_url:
                health['healthy'] = False
                health['flaresolverr_available'] = False
                health['details']['flaresolverr'] = 'URL not configured'
            elif self._session_manager:
                try:
                    fs_healthy = await self._session_manager.health_check()
                    health['flaresolverr_available'] = fs_healthy
                    health['circuit_breaker_state'] = self._session_manager.circuit_state.value
                    if not fs_healthy:
                        health['healthy'] = False
                        health['details']['flaresolverr'] = 'Service unavailable'
                except Exception as e:
                    health['healthy'] = False
                    health['flaresolverr_available'] = False
                    health['details']['flaresolverr'] = str(e)

        # Check tracker connectivity
        try:
            await self.authenticate()
            health['tracker_reachable'] = True
            health['authenticated'] = True
        except NetworkRetryableError as e:
            health['healthy'] = False
            health['tracker_reachable'] = False
            health['details']['tracker'] = f'Network error: {e}'
        except TrackerAPIError as e:
            health['tracker_reachable'] = True
            health['authenticated'] = False
            health['healthy'] = False
            health['details']['credentials'] = str(e)
        except Exception as e:
            health['healthy'] = False
            health['details']['error'] = str(e)

        return health

    def get_adapter_info(self) -> Dict[str, Any]:
        """Get adapter information."""
        tracker_config = self.config.get("tracker", {})

        features = ["config_driven"]
        if self.requires_cloudflare:
            features.append("cloudflare_bypass")
        if self.config.get("upload", {}).get("fields", {}).get("nfo"):
            features.append("nfo_upload")
        if self.config.get("options"):
            features.append("options_mapping")

        return {
            'name': 'Config Adapter',
            'tracker_name': tracker_config.get("name", self.tracker_name),
            'tracker_url': self.tracker_url,
            'version': '1.0.0',
            'features': features,
            'config_slug': tracker_config.get("slug", self.tracker_slug)
        }

    def build_options(self, **kwargs) -> Dict[str, Union[int, List[int]]]:
        """
        Build tracker options using the options mapper.

        Convenience method for pipeline integration.
        """
        return self.options_mapper.build_options(**kwargs)

    def build_options_from_file_entry(self, file_entry: Any, **kwargs) -> Dict[str, Union[int, List[int]]]:
        """
        Build tracker options from a FileEntry.

        Convenience method for pipeline integration.
        """
        return self.options_mapper.build_options_from_file_entry(file_entry, **kwargs)

    async def close(self):
        """Close HTTP client and cleanup resources."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def __repr__(self) -> str:
        return (
            f"<ConfigAdapter("
            f"tracker='{self.tracker_name}', "
            f"url='{self.tracker_url}', "
            f"auth_type='{self.auth_type}', "
            f"authenticated={self._authenticated}"
            f")>"
        )


def create_config_adapter_from_tracker(
    tracker: Any,
    flaresolverr_url: Optional[str] = None
) -> ConfigAdapter:
    """
    Factory function to create ConfigAdapter from a Tracker model.

    Args:
        tracker: Tracker model instance
        flaresolverr_url: FlareSolverr URL for Cloudflare bypass

    Returns:
        Configured ConfigAdapter instance
    """
    # Load config
    loader = get_config_loader()
    config = loader.load_from_tracker(tracker)

    if not config:
        # Try loading by slug
        config = loader.load(tracker.slug)

    return ConfigAdapter(
        config=config,
        tracker_url=tracker.tracker_url,
        api_key=tracker.api_key,
        passkey=tracker.passkey,
        flaresolverr_url=flaresolverr_url,
        default_category_id=tracker.default_category_id,
        default_subcategory_id=getattr(tracker, 'default_subcategory_id', None)
    )
