"""
ConfigAdapter - 100% Config-Driven Generic Tracker Adapter

This adapter uses YAML/JSON configuration to handle tracker operations.
Everything is driven by the config file - no hardcoded logic:

- Mappings: Resolution, source type, category, language conversions
- Workflow: Multi-step request chains (GET CSRF → POST upload)
- Dynamic Sources: Fetch categories/tags from API

The ConfigAdapter is "dumb" - it only:
1. Reads the YAML
2. Executes the steps defined
3. Applies mappings without conditional logic

Architecture:
    TrackerAdapter (interface)
          ↑
          |
    ConfigAdapter (generic implementation)
          |
          ├── Uses TrackerConfigLoader for configuration
          ├── Uses httpx for HTTP requests
          └── Supports Cloudflare bypass via FlareSolverr
"""

import asyncio
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Any, Union, Tuple

import httpx
from requests import Session

from .tracker_adapter import TrackerAdapter
from .tracker_config_loader import TrackerConfigLoader, get_config_loader
from ..services.exceptions import (
    TrackerAPIError,
    CloudflareBypassError,
    NetworkRetryableError
)

logger = logging.getLogger(__name__)


class _RateLimiter:
    """Token bucket rate limiter for tracker API calls."""

    def __init__(self, requests_per_minute: int = 60):
        self.rate = requests_per_minute / 60.0  # tokens per second
        self.max_tokens = requests_per_minute
        self.tokens = float(requests_per_minute)
        self.last_refill = time.monotonic()

    async def acquire(self):
        """Wait until a token is available."""
        while True:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            # Wait for next token
            wait_time = (1.0 - self.tokens) / self.rate
            await asyncio.sleep(wait_time)


class ConfigAdapter(TrackerAdapter):
    """
    100% Configuration-driven tracker adapter.

    This adapter reads ALL behavior from a configuration dictionary,
    allowing new trackers to be added without writing Python code.

    Key Concepts:
    - Mappings: Tables that convert input values to output values (zero logic)
    - Workflow: Chain of HTTP requests (supports CSRF, multi-step uploads)
    - Dynamic Sources: Fetch data from API (categories, tags) with caching

    Supported authentication types:
    - bearer: HTTP Authorization header with Bearer token
    - api_key: API key in header or query parameter
    - passkey: Passkey in URL or form data
    - cookie: Session cookie-based auth
    - none: No authentication required
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

        # Session manager for Cloudflare (lazy init)
        self._session_manager = None

        # Cache for dynamic sources
        self._dynamic_cache: Dict[str, Tuple[Any, float]] = {}

        # Rate limiters (initialized from config)
        self._rate_limiters: Dict[str, _RateLimiter] = {}
        rate_config = self.config.get("rate_limiting", {})
        for action, limits in rate_config.items():
            if isinstance(limits, dict) and "requests_per_minute" in limits:
                self._rate_limiters[action] = _RateLimiter(limits["requests_per_minute"])

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

    # =========================================================================
    # HTTP CLIENT MANAGEMENT
    # =========================================================================

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            headers = self._build_auth_headers()

            # Transfer cookies from FlareSolverr session if available
            cookies = None
            if self._session:
                cookies = httpx.Cookies()
                for cookie in self._session.cookies:
                    cookies.set(cookie.name, cookie.value, domain=cookie.domain)
                logger.debug(f"Transferred {len(self._session.cookies)} cookies to httpx client")

            self._client = httpx.AsyncClient(
                headers=headers,
                cookies=cookies,
                timeout=self.timeout,
                follow_redirects=True
            )
        return self._client

    def _reset_client(self):
        """Reset the HTTP client (needed after Cloudflare session update)."""
        if self._client:
            # We can't await aclose here, so just clear the reference
            self._client = None

    def _build_auth_headers(self) -> Dict[str, str]:
        """Build authentication headers based on config."""
        auth_config = self.config.get("auth", {})
        auth_type = auth_config.get("type", "none")
        headers = {}

        # Use api_key or passkey (backwards compatibility - UI stores in passkey field)
        effective_api_key = self.api_key or self.passkey

        if auth_type == "bearer":
            header_name = auth_config.get("header", "Authorization")
            prefix = auth_config.get("prefix", "Bearer ")
            if effective_api_key:
                headers[header_name] = f"{prefix}{effective_api_key}"
                logger.debug(f"Added Bearer auth header for {self.tracker_name}")
            else:
                logger.warning(f"No API key configured for {self.tracker_name} (auth_type=bearer)")

        elif auth_type == "api_key":
            header_name = auth_config.get("header_name", auth_config.get("header", "X-API-Key"))
            if effective_api_key:
                headers[header_name] = effective_api_key
                logger.debug(f"Added API key header '{header_name}' for {self.tracker_name}")
            else:
                logger.warning(f"No API key configured for {self.tracker_name} (auth_type=api_key)")

        return headers

    def _build_url(self, endpoint_key: str) -> str:
        """Build full URL from endpoint key."""
        endpoints = self.config.get("endpoints", {})
        endpoint = endpoints.get(endpoint_key, "")

        # Handle direct endpoint path
        if endpoint.startswith("/"):
            return f"{self.tracker_url}{endpoint}"

        # Handle base URL prefix
        base = endpoints.get("base", "")
        if base and not endpoint.startswith(base):
            endpoint = f"{base}/{endpoint.lstrip('/')}"

        return f"{self.tracker_url}{endpoint}"

    # =========================================================================
    # CLOUDFLARE SESSION MANAGEMENT
    # =========================================================================

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

    # =========================================================================
    # MAPPINGS - Pure Table Lookups (Zero Logic)
    # =========================================================================

    def _resolve_all_mappings(
        self,
        file_entry: Any,
        kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Apply all mappings defined in the YAML.

        This method has NO hardcoded logic - it just performs table lookups
        from the config. Each mapping defines:
        - input_field: Where to get the source value
        - output_field: What field name to output
        - values: The mapping table
        - default: Value if input is None
        - fallback: Value if no mapping match

        Args:
            file_entry: FileEntry object with metadata
            kwargs: Additional data passed to upload

        Returns:
            Dict with resolved output fields
        """
        resolved = {}
        mappings = self.config.get("mappings", {})

        for mapping_name, mapping_config in mappings.items():
            if not isinstance(mapping_config, dict):
                continue

            input_field = mapping_config.get("input_field")
            output_field = mapping_config.get("output_field", mapping_name)
            values_table = mapping_config.get("values", {})
            default = mapping_config.get("default")
            fallback = mapping_config.get("fallback")
            is_multi = mapping_config.get("multi", False)

            # Get the input value from file_entry or kwargs
            input_value = None
            if file_entry and hasattr(file_entry, input_field):
                input_value = getattr(file_entry, input_field)
            if input_value is None:
                input_value = kwargs.get(input_field)

            # Use default if no input value
            if input_value is None:
                resolved[output_field] = default
                continue

            # Handle multi-value inputs (e.g., languages list)
            if is_multi and isinstance(input_value, list):
                output_values = []
                for v in input_value:
                    v_lower = str(v).lower()
                    mapped = values_table.get(v_lower)
                    if mapped is not None:
                        output_values.append(mapped)
                    elif fallback is not None:
                        output_values.append(fallback)
                resolved[output_field] = output_values if output_values else [default] if default else []
            else:
                # Single value lookup
                input_lower = str(input_value).lower()
                mapped = values_table.get(input_lower)
                if mapped is not None:
                    resolved[output_field] = mapped
                elif fallback is not None:
                    resolved[output_field] = fallback
                else:
                    resolved[output_field] = default

        return resolved

    # =========================================================================
    # WORKFLOW EXECUTION - Chain Requests
    # =========================================================================

    def _interpolate(self, template: str, context: Dict[str, Any]) -> str:
        """
        Replace {variable} placeholders with values from context.

        Example: "{tracker_url}/upload" → "https://torr9.xyz/upload"

        Args:
            template: String with {variable} placeholders
            context: Dict of variable values

        Returns:
            Interpolated string
        """
        if not template or not isinstance(template, str):
            return template

        result = template
        for key, value in context.items():
            if value is not None:
                result = result.replace(f"{{{key}}}", str(value))

        return result

    def _extract_value(
        self,
        response: httpx.Response,
        source_type: str,
        selector: Optional[str] = None,
        attribute: Optional[str] = None,
        cookie_name: Optional[str] = None,
        header_name: Optional[str] = None,
        json_path: Optional[str] = None
    ) -> Any:
        """
        Extract a value from HTTP response.

        Supported extraction types:
        - json: Extract from JSON response using dot notation path
        - html: Extract from HTML using CSS selector
        - cookie: Extract from response cookies
        - header: Extract from response headers

        Args:
            response: HTTP response object
            source_type: Type of extraction (json|html|cookie|header)
            selector: CSS selector for html extraction
            attribute: HTML attribute to get
            cookie_name: Cookie name for cookie extraction
            header_name: Header name for header extraction
            json_path: Dot notation path for json extraction

        Returns:
            Extracted value or None
        """
        try:
            if source_type == "json":
                data = response.json()
                return self._get_nested_value(data, json_path or "")

            elif source_type == "html":
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.text, 'html.parser')
                    element = soup.select_one(selector) if selector else None
                    if element:
                        return element.get(attribute) if attribute else element.text
                except ImportError:
                    logger.warning("BeautifulSoup not installed, HTML extraction unavailable")
                return None

            elif source_type == "cookie":
                return response.cookies.get(cookie_name) if cookie_name else None

            elif source_type == "header":
                return response.headers.get(header_name) if header_name else None

        except Exception as e:
            logger.warning(f"Extraction failed ({source_type}): {e}")

        return None

    def _build_request_body(
        self,
        step: Dict[str, Any],
        context: Dict[str, Any],
        body_type: str
    ) -> Dict[str, Any]:
        """
        Build request body from step fields config.

        Supports:
        - multipart: Files and form data
        - json: JSON body
        - form: URL-encoded form

        Args:
            step: Workflow step configuration
            context: Current context with all values
            body_type: Body type (multipart|json|form)

        Returns:
            Dict with 'files' and 'data' keys for multipart,
            or just data dict for json/form
        """
        fields_config = step.get("fields", {})

        if body_type == "multipart":
            files = {}
            data = []  # List of tuples to support repeated fields

            for field_name, field_config in fields_config.items():
                if not isinstance(field_config, dict):
                    continue

                source = field_config.get("source", field_name)
                field_type = field_config.get("type", "string")
                required = field_config.get("required", False)
                default = field_config.get("default")

                # Get value from context
                value = context.get(source)
                if value is None:
                    value = default
                if value is None and not required:
                    continue
                if value is None and required:
                    logger.warning(f"Missing required field: {source}")
                    continue

                # Determine API field name
                api_name = field_config.get("name", field_name)

                if field_type == "file":
                    if isinstance(value, bytes):
                        filename = self._interpolate(
                            field_config.get("filename", f"{field_name}.bin"),
                            context
                        )
                        files[api_name] = (filename, value)

                elif field_type == "repeated" and isinstance(value, list):
                    # Repeated fields: same key multiple times
                    for v in value:
                        data.append((api_name, str(v)))

                elif field_type == "json":
                    data.append((api_name, json.dumps(value)))

                elif field_type == "boolean":
                    data.append((api_name, "true" if value else "false"))

                else:
                    # String, number, etc.
                    str_value = str(value)

                    # Apply sanitization if configured
                    sanitize = field_config.get("sanitize", {})
                    if sanitize.get("replace_spaces"):
                        str_value = str_value.replace(" ", sanitize["replace_spaces"])
                    max_length = sanitize.get("max_length")
                    if max_length and len(str_value) > max_length:
                        str_value = str_value[:max_length]

                    data.append((api_name, str_value))

            return {"files": files, "data": data}

        elif body_type == "json":
            body = {}
            for field_name, field_config in fields_config.items():
                if not isinstance(field_config, dict):
                    continue
                source = field_config.get("source", field_name)
                api_name = field_config.get("name", field_name)
                value = context.get(source, field_config.get("default"))
                if value is not None:
                    body[api_name] = value
            return {"json": body}

        else:  # form-urlencoded
            data = []
            for field_name, field_config in fields_config.items():
                if not isinstance(field_config, dict):
                    continue
                source = field_config.get("source", field_name)
                api_name = field_config.get("name", field_name)
                value = context.get(source, field_config.get("default"))
                if value is not None:
                    data.append((api_name, str(value)))
            return {"data": data}

    async def _execute_step(
        self,
        step: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a single workflow step.

        A step can be:
        - GET request (for CSRF tokens, etc.)
        - POST request (for upload)

        Supports variable injection from previous steps.

        Args:
            step: Step configuration from workflow
            context: Current context with all variables

        Returns:
            Dict with 'response' and 'extracted' values
        """
        method = step.get("method", "POST")
        url = self._interpolate(step["url"], context)
        body_type = step.get("type", "multipart")

        client = await self._get_client()
        headers = dict(client.headers)

        # Process injections from previous steps
        for injection in step.get("inject", []):
            value = self._interpolate(injection.get("value", ""), context)
            if injection.get("header"):
                headers[injection["field"]] = value
            else:
                context[injection["field"]] = value

        # Build request body
        request_kwargs = {"headers": headers}

        if method.upper() == "GET":
            # GET requests may have query params
            params = {}
            for field_name, field_config in step.get("fields", {}).items():
                if isinstance(field_config, dict):
                    source = field_config.get("source", field_name)
                    api_name = field_config.get("name", field_name)
                    value = context.get(source)
                    if value is not None:
                        params[api_name] = str(value)
            if params:
                request_kwargs["params"] = params

            response = await client.get(url, **request_kwargs)

        else:  # POST, PUT, etc.
            body = self._build_request_body(step, context, body_type)

            if body_type == "multipart":
                # httpx multipart handling - combine files and data into single files dict
                # This is required for async httpx to work correctly
                files_dict = body.get("files", {})
                data_list = body.get("data", [])

                # Build combined multipart form
                # Files: {"field": ("filename", content, "content_type")}
                # Data:  {"field": (None, value)} - None filename = not a file
                multipart_fields = {}

                # Add file fields
                for field_name, (filename, content) in files_dict.items():
                    content_type = "application/x-bittorrent" if filename.endswith(".torrent") else "application/octet-stream"
                    multipart_fields[field_name] = (filename, content, content_type)

                # Add data fields - handle repeated fields by appending index
                field_counts = {}
                for field_name, value in data_list:
                    if field_name in multipart_fields or field_name in field_counts:
                        # Repeated field - use array notation
                        count = field_counts.get(field_name, 0)
                        field_counts[field_name] = count + 1
                        # For repeated fields, httpx needs unique keys
                        # We'll use the bracket notation that works with most APIs
                        actual_name = field_name if field_name.endswith("[]") else f"{field_name}"
                        if actual_name not in multipart_fields:
                            multipart_fields[actual_name] = (None, value)
                        else:
                            # Add as additional entry with index
                            multipart_fields[f"{field_name}[{count}]"] = (None, value)
                    else:
                        multipart_fields[field_name] = (None, value)
                        field_counts[field_name] = 0

                response = await client.post(
                    url,
                    files=multipart_fields if multipart_fields else None,
                    **request_kwargs
                )

            elif body_type == "json":
                response = await client.post(
                    url,
                    json=body.get("json", {}),
                    **request_kwargs
                )

            else:  # form
                response = await client.post(
                    url,
                    data=body.get("data", []),
                    **request_kwargs
                )

        # Extract values for subsequent steps
        extracted = {}
        for extraction in step.get("extract", []):
            extracted[extraction["name"]] = self._extract_value(
                response,
                extraction.get("from", "json"),
                extraction.get("selector"),
                extraction.get("attribute"),
                extraction.get("cookie_name"),
                extraction.get("header_name"),
                extraction.get("json_path")
            )

        return {
            "response": response,
            "extracted": extracted,
            "step_name": step.get("name", "unknown")
        }

    # =========================================================================
    # AUTHENTICATION
    # =========================================================================

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

                # Reset httpx client to pick up Cloudflare cookies
                self._reset_client()

            # For bearer/api_key auth, just verify the key exists
            if self.auth_type in ("bearer", "api_key"):
                # Use api_key or passkey (backwards compatibility)
                effective_api_key = self.api_key or self.passkey
                logger.debug(f"Auth check: api_key={bool(self.api_key)}, passkey={bool(self.passkey)}, effective={bool(effective_api_key)}")
                if effective_api_key:
                    logger.debug(f"API key length: {len(effective_api_key)}, first 8 chars: {effective_api_key[:8]}...")
                if not effective_api_key:
                    raise TrackerAPIError(
                        f"{self.tracker_name} requires API key for authentication"
                    )

                # Skip HTTP validation when Cloudflare is involved
                # The API key will be validated during actual API calls
                # This avoids issues with endpoints that may not support the same auth method
                if self.requires_cloudflare:
                    logger.info(f"Skipping API key validation (Cloudflare tracker) - will validate during actual API calls")
                else:
                    # Make a test request to verify credentials
                    client = await self._get_client()

                    # Try meta endpoint first (most reliable for validation)
                    test_endpoints = ["meta", "health", "categories"]
                    test_url = None

                    for endpoint in test_endpoints:
                        url = self._build_url(endpoint)
                        if url != self.tracker_url and url != f"{self.tracker_url}/":
                            test_url = url
                            break

                    if test_url:
                        try:
                            # Build validation params - include API key as query param if configured
                            auth_config = self.config.get("auth", {})
                            api_key_query_param = auth_config.get("query_param")
                            params = {}
                            if api_key_query_param and effective_api_key:
                                params[api_key_query_param] = effective_api_key

                            logger.debug(f"Validating API key via: GET {test_url}")
                            response = await client.get(test_url, params=params if params else None)
                            logger.debug(f"Validation response: HTTP {response.status_code}")

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

    # =========================================================================
    # UPLOAD TORRENT - Main Entry Point
    # =========================================================================

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
        file_entry: Any = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Upload torrent using config-driven workflow.

        The upload process:
        1. Resolve all mappings from config
        2. Build context with all available data
        3. Execute workflow steps in order
        4. Parse response and return result

        Args:
            torrent_data: Torrent file bytes
            release_name: Release name
            category_id: Category ID
            tag_ids: List of tag IDs
            nfo_data: NFO file bytes
            description: Description text
            tmdb_id: TMDB ID
            tmdb_type: TMDB type (movie/tv)
            cover_url: Cover image URL
            file_entry: Optional FileEntry for additional metadata
            **kwargs: Additional data for mappings

        Returns:
            Dict with upload result
        """
        logger.info(f"Uploading torrent to {self.tracker_name}: {release_name}")

        if not self._authenticated:
            await self.authenticate()

        try:
            # Apply rate limiting for upload
            if "upload" in self._rate_limiters:
                await self._rate_limiters["upload"].acquire()

            # Build initial context with all available data
            # Support api_base_url for trackers with API on subdomain (e.g., api.torr9.xyz)
            api_base_url = self.config.get("api_base_url", self.tracker_url)

            # Use api_key or passkey (backwards compatibility)
            effective_api_key = self.api_key or self.passkey

            context = {
                "tracker_url": self.tracker_url,
                "api_base_url": api_base_url,
                "passkey": self.passkey,
                "api_key": effective_api_key,
                "torrent_data": torrent_data,
                "nfo_data": nfo_data,
                "release_name": release_name,
                "category_id": category_id,
                "category": category_id,  # Alias
                "subcategory": kwargs.get("subcategory_id") or category_id,
                "tag_ids": tag_ids,
                "description": description,
                "tmdb_id": tmdb_id,
                "tmdb_type": tmdb_type,
                "cover_url": cover_url,
                **kwargs
            }

            # Add endpoints to context for interpolation
            endpoints = self.config.get("endpoints", {})
            for key, value in endpoints.items():
                context[f"endpoints.{key}"] = value

            # Resolve mappings if defined
            if file_entry or kwargs:
                resolved = self._resolve_all_mappings(file_entry, kwargs)
                # Only update context with non-None resolved values
                # This prevents mappings from overwriting valid context values with None
                for key, value in resolved.items():
                    if value is not None:
                        context[key] = value

            # Auto-invoke OptionsMapper if config has an options section (1.7)
            if file_entry and self.config.get("options"):
                try:
                    options = self.build_options_from_file_entry(
                        file_entry,
                        release_name=release_name,
                        genres=kwargs.get("genres")
                    )
                    if options:
                        context["options"] = options
                except Exception as e:
                    logger.warning(f"Auto options mapping failed: {e}")

            # Apply name sanitization if configured (1.6)
            if self.config.get("sanitize"):
                context["release_name"] = self._sanitize_name(context["release_name"])

            # Validate upload data (1.5)
            validation_errors = self._validate_upload_data(context)
            if validation_errors:
                raise TrackerAPIError(
                    f"Validation failed: {'; '.join(validation_errors)}"
                )

            # Check for workflow - if not present, use legacy upload
            workflow = self.config.get("workflow", [])
            if not workflow:
                # Fallback to legacy upload method
                return await self._legacy_upload(context)

            # Execute workflow steps
            last_result = None
            for step in workflow:
                # Use sync multipart for steps with repeated fields (1.1)
                if self._step_has_repeated_fields(step) or self._use_requests_session():
                    result = await self._execute_step_sync(step, context)
                else:
                    result = await self._execute_step(step, context)
                # Update context with extracted values
                context.update(result.get("extracted", {}))
                last_result = result

            # Parse final response
            if last_result and last_result.get("response"):
                return self._parse_upload_response(last_result["response"])

            return {
                'success': False,
                'message': 'No response from workflow',
                'torrent_id': None,
                'torrent_url': None
            }

        except (TrackerAPIError, NetworkRetryableError):
            raise

        except httpx.RequestError as e:
            raise NetworkRetryableError(f"Network error during upload: {e}")

        except Exception as e:
            error_msg = f"Upload failed: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg)

    async def _legacy_upload(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Legacy upload method for configs without workflow section.

        Uses the old upload.fields configuration.
        """
        upload_config = self.config.get("upload", {})
        fields_config = upload_config.get("fields", {})

        files = {}
        form_data = []

        for field_name, field_config in fields_config.items():
            if not isinstance(field_config, dict):
                continue

            field_api_name = field_config.get("name", field_name)
            field_type = field_config.get("type", "string")
            source = field_config.get("source", field_name)
            required = field_config.get("required", False)

            # Get value from context
            value = context.get(source)

            # Handle special sources
            if source == "torrent" or field_name == "torrent":
                value = context.get("torrent_data")
            elif source == "nfo" or field_name == "nfo":
                value = context.get("nfo_data")

            # Skip if no value and not required
            if value is None:
                if required:
                    raise TrackerAPIError(f"Missing required field: {source}")
                continue

            # Handle different field types
            if field_type == "file":
                if isinstance(value, bytes):
                    if "torrent" in field_name.lower():
                        filename = f"{context.get('release_name', 'torrent')}.torrent"
                    elif "nfo" in field_name.lower():
                        filename = f"{context.get('release_name', 'file')}.nfo"
                    else:
                        filename = f"{field_name}.bin"
                    files[field_api_name] = (filename, value)

            elif field_type == "json":
                form_data.append((field_api_name, json.dumps(value)))

            elif field_type == "repeated":
                if isinstance(value, list):
                    for v in value:
                        form_data.append((field_api_name, str(v)))

            elif field_type == "boolean":
                form_data.append((field_api_name, "true" if value else "false"))

            else:
                # Apply sanitization if configured
                sanitize = field_config.get("sanitize", {})
                str_value = str(value)

                if sanitize.get("replace_spaces"):
                    str_value = str_value.replace(" ", sanitize["replace_spaces"])

                max_length = sanitize.get("max_length")
                if max_length and len(str_value) > max_length:
                    str_value = str_value[:max_length]

                form_data.append((field_api_name, str_value))

        # Make upload request
        upload_url = self._build_url("upload")

        logger.info(f"Upload URL: {upload_url}")
        logger.info(f"Upload form fields: {[k for k, v in form_data]}")
        logger.info(f"Upload file fields: {list(files.keys())}")

        # Add API key as query param if configured (some trackers need both header + param)
        auth_config = self.config.get("auth", {})
        api_key_query_param = auth_config.get("query_param")
        effective_api_key = self.api_key or self.passkey
        params = {}
        if api_key_query_param and effective_api_key:
            params[api_key_query_param] = effective_api_key

        # Use requests.Session for repeated fields or when configured (1.1 + 1.10)
        if self._upload_has_repeated_fields() or self._use_requests_session():
            # Convert files to sync format
            files_for_sync = {}
            for field_name, (filename, content) in files.items():
                content_type = "application/x-bittorrent" if filename.endswith(".torrent") else "application/octet-stream"
                files_for_sync[field_name] = (filename, content, content_type)

            response = await self._sync_multipart_post(
                url=upload_url,
                files=files_for_sync,
                data=form_data,
                params=params if params else None
            )
        else:
            client = await self._get_client()
            # Merge form_data and file uploads into a single files= parameter
            # to avoid httpx sync/async encoding bug when mixing data= and files=
            if files:
                merged = [(key, (None, val)) for key, val in form_data]
                for key, val in files.items():
                    merged.append((key, val))
                response = await client.post(upload_url, files=merged, params=params if params else None)
            else:
                response = await client.post(upload_url, data=form_data, params=params if params else None)

        return self._parse_upload_response(response)

    def _parse_upload_response(self, response: httpx.Response) -> Dict[str, Any]:
        """Parse upload response based on config."""
        response_config = self.config.get("response", {})
        # Support nested upload response config (e.g., response.upload.success_field)
        if "upload" in response_config and isinstance(response_config["upload"], dict):
            upload_response_config = response_config["upload"]
            # Merge upload-specific fields with general response config
            merged = {**response_config, **upload_response_config}
            merged.pop("upload", None)
            response_config = merged

        result = {
            'success': False,
            'torrent_id': None,
            'torrent_url': None,
            'message': '',
            'response_data': {}
        }

        # Log raw response for debugging
        logger.debug(f"Upload response status: {response.status_code}")
        logger.debug(f"Upload response body: {response.text[:500] if response.text else 'empty'}")

        try:
            data = response.json()
            result['response_data'] = data
            logger.debug(f"Parsed response data: {data}")

            # Check success field - also consider 2xx status codes as success
            success_field = response_config.get("success_field", "success")
            success_value = self._get_nested_value(data, success_field, None)

            # If success field exists, use it; otherwise use HTTP status code
            if success_value is not None:
                result['success'] = bool(success_value)
            else:
                # 2xx status codes are considered success
                result['success'] = 200 <= response.status_code < 300

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
            # If JSON fails but status is 2xx, consider it success
            if 200 <= response.status_code < 300:
                result['success'] = True
                result['message'] = f"Upload successful (HTTP {response.status_code})"
            else:
                result['message'] = f"Invalid JSON response: {response.text[:200]}"

        # Check HTTP status - only override for error codes
        if response.status_code >= 400:
            result['success'] = False
            result['message'] = f"HTTP {response.status_code}: {result['message']}"

        return result

    def _get_nested_value(self, data: Any, path: str, default: Any = None) -> Any:
        """
        Get nested value from dict using dot notation.

        Supports:
        - Simple paths: "data.id"
        - Wildcard arrays: "tagGroups[*].tags[*]" - flattens nested arrays
        - Indexed access: "data[0].name"
        """
        if not path:
            return default

        keys = path.split('.')
        value = data

        for key in keys:
            if value is None:
                return default

            # Handle wildcard: key[*]
            match = re.match(r'^(.+?)\[\*\]$', key)
            if match:
                field_name = match.group(1)
                if isinstance(value, dict):
                    value = value.get(field_name, default)
                if isinstance(value, list):
                    # Flatten: collect results from remaining path on each item
                    remaining = '.'.join(keys[keys.index(key) + 1:])
                    if remaining:
                        results = []
                        for item in value:
                            sub = self._get_nested_value(item, remaining, None)
                            if sub is not None:
                                if isinstance(sub, list):
                                    results.extend(sub)
                                else:
                                    results.append(sub)
                        return results if results else default
                    return value
                return default

            # Handle index: key[N]
            idx_match = re.match(r'^(.+?)\[(\d+)\]$', key)
            if idx_match:
                field_name = idx_match.group(1)
                idx = int(idx_match.group(2))
                if isinstance(value, dict):
                    value = value.get(field_name, default)
                if isinstance(value, list) and idx < len(value):
                    value = value[idx]
                else:
                    return default
                continue

            # Normal dict access
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    # =========================================================================
    # DYNAMIC SOURCES - Fetch from API
    # =========================================================================

    async def _fetch_dynamic_source(self, source_name: str) -> Optional[List[Dict]]:
        """
        Fetch dynamic source data from API with caching.

        Args:
            source_name: Name of the dynamic source (categories, tags, etc.)

        Returns:
            List of items or None
        """
        import time

        dynamic_sources = self.config.get("dynamic_sources", {})
        source_config = dynamic_sources.get(source_name)

        if not source_config:
            return None

        # Check cache
        cache_ttl = source_config.get("cache_ttl", 3600)
        if source_name in self._dynamic_cache:
            cached_data, cached_time = self._dynamic_cache[source_name]
            if time.time() - cached_time < cache_ttl:
                return cached_data

        # Fetch from API
        endpoint = source_config.get("endpoint", "")
        # Use base_url from source config, or api_base_url from main config, or tracker_url
        base_url = source_config.get("base_url") or self.config.get("api_base_url") or self.tracker_url
        url = f"{base_url}{endpoint}"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()

            # Parse response
            response_config = source_config.get("response", {})
            path = response_config.get("path", "")
            items = self._get_nested_value(data, path, []) if path else data

            # Normalize items
            id_field = response_config.get("id_field", "id")
            name_field = response_config.get("name_field", "name")

            normalized = []
            for item in items:
                if isinstance(item, dict):
                    norm = {
                        'id': str(item.get(id_field, '')),
                        'name': item.get(name_field, '')
                    }
                    # Preserve subcategories/children for hierarchical trackers (e.g., C411)
                    for hier_key in ('subcategories', 'children'):
                        if hier_key in item and item[hier_key]:
                            norm['subcategories'] = item[hier_key]
                            break
                    normalized.append(norm)

            # Cache result
            self._dynamic_cache[source_name] = (normalized, time.time())

            return normalized

        except Exception as e:
            logger.warning(f"Failed to fetch dynamic source {source_name}: {e}")
            return None

    # =========================================================================
    # PUBLIC API METHODS
    # =========================================================================

    async def validate_credentials(self) -> bool:
        """Validate credentials based on auth type."""
        logger.info(f"Validating credentials for {self.tracker_name}")

        try:
            if self.auth_type in ("bearer", "api_key"):
                # Use api_key or passkey (backwards compatibility)
                effective_api_key = self.api_key or self.passkey
                if not effective_api_key:
                    return False
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

        # Try dynamic source first
        dynamic_tags = await self._fetch_dynamic_source("tags")
        if dynamic_tags:
            return [
                {
                    'tag_id': t['id'],
                    'label': t['name'],
                    'category': t.get('group', ''),
                    'description': ''
                }
                for t in dynamic_tags
            ]

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

        # Try dynamic source first
        dynamic_cats = await self._fetch_dynamic_source("categories")
        if dynamic_cats:
            result = []
            for c in dynamic_cats:
                cat = {
                    'category_id': c['id'],
                    'name': c['name'],
                    'description': ''
                }
                # Preserve subcategories for hierarchical trackers (e.g., C411)
                if c.get('subcategories'):
                    cat['subcategories'] = c['subcategories']
                result.append(cat)
            return result

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
                    norm = {
                        'category_id': str(cat.get('id') or cat.get('category_id', '')),
                        'name': cat.get('name') or cat.get('label', ''),
                        'description': cat.get('description', '')
                    }
                    # Preserve subcategories for hierarchical trackers (e.g., C411)
                    for hier_key in ('subcategories', 'children'):
                        if hier_key in cat and cat[hier_key]:
                            norm['subcategories'] = cat[hier_key]
                            break
                    normalized.append(norm)

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
        quality: Optional[str] = None,
        file_size: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Check for duplicate releases on tracker using config-driven search."""
        logger.info(f"🔍 Checking duplicates on {self.tracker_name}: tmdb={tmdb_id}, name={release_name}")

        if not self._authenticated:
            await self.authenticate()

        result = {
            'is_duplicate': False,
            'exact_match': False,
            'exact_matches': [],
            'existing_torrents': [],
            'search_method': 'none',
            'message': 'No duplicates found'
        }

        try:
            search_url = self._build_url("search")
            client = await self._get_client()

            # Get search param names from config (with defaults)
            search_config = self.config.get("search", {})
            params_config = search_config.get("params", {})
            tmdb_param = params_config.get("tmdb_id", "tmdbId")
            imdb_param = params_config.get("imdb_id", "imdb")
            query_param = params_config.get("query", "q")

            # Use api_key or passkey for query param auth (some trackers need this)
            effective_api_key = self.api_key or self.passkey
            auth_config = self.config.get("auth", {})
            api_key_query_param = auth_config.get("query_param")

            def build_params(base_params: Dict[str, str]) -> Dict[str, str]:
                """Build params with optional apikey query param."""
                params = base_params.copy()
                if api_key_query_param and effective_api_key:
                    params[api_key_query_param] = effective_api_key
                return params

            # Get response format (json or torznab_xml)
            response_config = search_config.get("response", {})
            response_format = response_config.get("format", "json")

            # Get default query from config (replaces hardcoded "FRENCH")
            default_query = search_config.get("default_query", "")

            # Apply rate limiting for search
            if "search" in self._rate_limiters:
                await self._rate_limiters["search"].acquire()

            # Try TMDB ID first
            if tmdb_id:
                base_params = {tmdb_param: str(tmdb_id)}
                if default_query:
                    base_params[query_param] = default_query
                params = build_params(base_params)
                logger.info(f"🔍 TMDB search: GET {search_url} params={params}")
                response = await client.get(search_url, params=params)
                logger.info(f"🔍 TMDB search response: HTTP {response.status_code}")
                if response.status_code == 200:
                    logger.debug(f"🔍 Response body: {response.text[:500]}")
                    torrents = self._parse_response_auto(response, response_format)
                    logger.info(f"🔍 TMDB search found {len(torrents)} results")
                    if torrents:
                        result['existing_torrents'] = torrents
                        result['search_method'] = 'tmdb'
                        result['is_duplicate'] = True

            # Try IMDB ID (if supported)
            if not result['is_duplicate'] and imdb_id:
                params = build_params({imdb_param: str(imdb_id)})
                logger.info(f"🔍 IMDB search: GET {search_url} params={params}")
                response = await client.get(search_url, params=params)
                if response.status_code == 200:
                    torrents = self._parse_response_auto(response, response_format)
                    logger.info(f"🔍 IMDB search found {len(torrents)} results")
                    if torrents:
                        result['existing_torrents'] = torrents
                        result['search_method'] = 'imdb'
                        result['is_duplicate'] = True

            # Try release name
            if not result['is_duplicate'] and release_name:
                # Extract title from release name
                title = re.sub(r'[\.\s]+(19|20)\d{2}.*', '', release_name)
                title = title.replace('.', ' ')[:200]

                params = build_params({query_param: title})
                logger.info(f"🔍 Name search: GET {search_url} params={params}")
                response = await client.get(search_url, params=params)
                if response.status_code == 200:
                    torrents = self._parse_response_auto(response, response_format)
                    logger.info(f"🔍 Name search found {len(torrents)} results")
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
                    logger.info(f"🔍 Filtered to {len(filtered)} results matching quality {quality}")
                else:
                    result['is_duplicate'] = False
                    result['existing_torrents'] = []

            # Check for exact matches by file size
            if result['is_duplicate'] and file_size:
                tolerance = file_size * 0.01  # 1% tolerance
                for t in result['existing_torrents']:
                    torrent_size = t.get('size', 0)
                    if torrent_size and abs(torrent_size - file_size) <= tolerance:
                        result['exact_matches'].append(t)
                if result['exact_matches']:
                    result['exact_match'] = True
                    logger.info(f"🔍 Found {len(result['exact_matches'])} exact size matches")

            if result['exact_match']:
                result['message'] = f"EXACT MATCH: Found {len(result['exact_matches'])} torrent(s) with same size"
            elif result['is_duplicate']:
                result['message'] = f"Found {len(result['existing_torrents'])} existing release(s) via {result['search_method']} search"
            else:
                result['message'] = "No duplicates found - safe to upload"

            logger.info(f"🔍 Duplicate check result: is_duplicate={result['is_duplicate']}, method={result['search_method']}")
            return result

        except httpx.RequestError as e:
            raise NetworkRetryableError(f"Network error checking duplicates: {e}")

        except Exception as e:
            logger.error(f"Duplicate check failed: {type(e).__name__}: {e}", exc_info=True)
            result['message'] = f"Check failed: {str(e)}"
            return result

    def _parse_response_auto(self, response: httpx.Response, response_format: str = "json") -> List[Dict[str, Any]]:
        """Parse search response based on format (json or torznab_xml)."""
        if response_format == "torznab_xml":
            return self._parse_torznab_xml(response.text)
        try:
            data = response.json()
            return self._parse_search_results(data, raw_text=response.text)
        except json.JSONDecodeError:
            # Maybe it's XML even though config says json
            if response.text.strip().startswith('<?xml') or response.text.strip().startswith('<rss'):
                return self._parse_torznab_xml(response.text)
            return []

    def _parse_search_results(self, data: Any, raw_text: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Parse search results from API response.

        Dispatches to appropriate parser based on search.response.format in config:
        - "json_array" or default: Parse as JSON
        - "torznab_xml": Parse as Torznab XML/RSS
        """
        search_config = self.config.get("search", {})
        response_config = search_config.get("response", {})
        response_format = response_config.get("format", "json")

        # Dispatch to Torznab XML parser
        if response_format == "torznab_xml" and raw_text:
            return self._parse_torznab_xml(raw_text)

        results = []

        if isinstance(data, list):
            torrents = data
        elif isinstance(data, dict):
            torrents = data.get("torrents") or data.get("data") or data.get("results") or []
        else:
            return results

        for t in torrents:
            if isinstance(t, dict):
                # Support multiple field name conventions
                torrent_id = t.get('id') or t.get('guid') or t.get('infoHash') or ''
                name = t.get('name') or t.get('title') or ''
                size = t.get('size', 0)
                info_hash = t.get('infoHash') or t.get('info_hash') or ''

                results.append({
                    'id': str(torrent_id),
                    'torrent_id': str(torrent_id),
                    'name': name,
                    'size': size,
                    'info_hash': info_hash,
                    'seeders': t.get('seeders', 0),
                    'leechers': t.get('leechers', 0),
                    'category': t.get('category', ''),
                    'download_link': t.get('link') or t.get('download_link') or t.get('url', ''),
                    'pub_date': t.get('pubDate') or t.get('uploaded_at') or t.get('created_at', ''),
                    'quality': t.get('quality', ''),
                })
                logger.debug(f"  Parsed: {name[:50]} (size={size}, hash={info_hash[:16] if info_hash else 'N/A'})")

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
        if self.config.get("workflow"):
            features.append("workflow")
        if self.config.get("mappings"):
            features.append("mappings")
        if self.config.get("dynamic_sources"):
            features.append("dynamic_sources")
        if self.config.get("upload", {}).get("fields", {}).get("nfo"):
            features.append("nfo_upload")

        return {
            'name': 'Config Adapter',
            'tracker_name': tracker_config.get("name", self.tracker_name),
            'tracker_url': self.tracker_url,
            'version': '2.0.0',
            'features': features,
            'config_slug': tracker_config.get("slug", self.tracker_slug)
        }

    # =========================================================================
    # LEGACY COMPATIBILITY - build_options methods
    # =========================================================================

    def build_options(self, **kwargs) -> Dict[str, Union[int, List[int]]]:
        """
        Build tracker options using mappings.

        For backwards compatibility - uses mappings section instead of
        the old OptionsMapper.
        """
        # If we have legacy options config, use OptionsMapper
        options_config = self.config.get("options", {})
        if options_config:
            from ..services.options_mapper import OptionsMapper
            mapper = OptionsMapper(options_config)
            return mapper.build_options(**kwargs)

        # Otherwise, use mappings to resolve values
        resolved = self._resolve_all_mappings(None, kwargs)

        # Filter to only return option-like values
        options = {}
        for key, value in resolved.items():
            if isinstance(value, (int, list)):
                options[key] = value

        return options

    def build_options_from_file_entry(
        self,
        file_entry: Any,
        **kwargs
    ) -> Dict[str, Union[int, List[int]]]:
        """
        Build tracker options from a FileEntry.

        For backwards compatibility.
        """
        options_config = self.config.get("options", {})
        if options_config:
            from ..services.options_mapper import OptionsMapper
            mapper = OptionsMapper(options_config)
            return mapper.build_options_from_file_entry(file_entry, **kwargs)

        resolved = self._resolve_all_mappings(file_entry, kwargs)
        options = {}
        for key, value in resolved.items():
            if isinstance(value, (int, list)):
                options[key] = value

        return options

    # =========================================================================
    # SYNC MULTIPART POST - For repeated form fields via requests.Session
    # =========================================================================

    async def _sync_multipart_post(
        self,
        url: str,
        files: Dict[str, tuple],
        data: List[tuple],
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None
    ) -> httpx.Response:
        """
        Post multipart form data using requests.Session via asyncio.to_thread().

        This is needed for trackers like La Cale that require repeated form fields
        (e.g., tags=ID1&tags=ID2). httpx dicts cannot produce repeated keys, but
        requests.Session with a list of tuples can.

        Also used when cloudflare.use_requests_session is true, to avoid
        cookie transfer issues between FlareSolverr (requests) and httpx.

        Args:
            url: Target URL
            files: Dict of file fields {name: (filename, content, content_type)}
            data: List of tuples for form data [(name, value), ...]
            headers: Optional extra headers
            params: Optional query params

        Returns:
            httpx.Response-compatible object
        """
        import requests

        def _do_post():
            session = self._session or requests.Session()

            # Build headers from auth + extra
            req_headers = self._build_auth_headers()
            if headers:
                req_headers.update(headers)

            # Transfer Cloudflare cookies if available
            if self._session and self._session.cookies:
                for cookie in self._session.cookies:
                    session.cookies.set(cookie.name, cookie.value, domain=cookie.domain)

            # Build files list for requests: [(field, (filename, data, content_type)), ...]
            files_list = []
            for field_name, file_tuple in files.items():
                if len(file_tuple) == 3:
                    fname, content, ctype = file_tuple
                    files_list.append((field_name, (fname, content, ctype)))
                else:
                    fname, content = file_tuple
                    files_list.append((field_name, (fname, content)))

            # requests.post with data=list_of_tuples supports repeated fields natively
            resp = session.post(
                url,
                files=files_list if files_list else None,
                data=data if data else None,
                headers=req_headers,
                params=params,
                timeout=self.timeout
            )
            return resp

        # Run synchronous requests in thread pool
        sync_response = await asyncio.to_thread(_do_post)

        # Wrap in httpx.Response for consistent interface
        httpx_response = httpx.Response(
            status_code=sync_response.status_code,
            headers=dict(sync_response.headers),
            content=sync_response.content,
            request=httpx.Request("POST", url)
        )
        return httpx_response

    def _step_has_repeated_fields(self, step: Dict[str, Any]) -> bool:
        """Check if a workflow step has repeated form fields."""
        for field_config in step.get("fields", {}).values():
            if isinstance(field_config, dict) and field_config.get("type") == "repeated":
                return True
        return False

    def _upload_has_repeated_fields(self) -> bool:
        """Check if legacy upload config has repeated form fields."""
        upload_config = self.config.get("upload", {})
        tags_as_repeated = upload_config.get("tags_as_repeated_fields", False)
        if tags_as_repeated:
            return True
        for field_config in upload_config.get("fields", {}).values():
            if isinstance(field_config, dict) and field_config.get("type") == "repeated":
                return True
        return False

    def _use_requests_session(self) -> bool:
        """Check if config requests using requests.Session instead of httpx."""
        cloudflare_config = self.config.get("cloudflare", {})
        return cloudflare_config.get("use_requests_session", False)

    async def _execute_step_sync(
        self,
        step: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a workflow step using requests.Session (sync via asyncio.to_thread).

        Used when:
        - Step has repeated form fields (tags=ID1&tags=ID2)
        - Config has cloudflare.use_requests_session: true

        Args:
            step: Workflow step configuration
            context: Current context with all variables

        Returns:
            Dict with 'response' and 'extracted' values
        """
        method = step.get("method", "POST")
        url = self._interpolate(step["url"], context)
        body_type = step.get("type", "multipart")

        if method.upper() != "POST" or body_type != "multipart":
            # For non-POST or non-multipart, fall back to httpx
            return await self._execute_step(step, context)

        # Build request body
        body = self._build_request_body(step, context, "multipart")
        files_dict = body.get("files", {})
        data_list = body.get("data", [])

        # Convert files to format expected by _sync_multipart_post
        files_for_sync = {}
        for field_name, (filename, content) in files_dict.items():
            content_type = "application/x-bittorrent" if filename.endswith(".torrent") else "application/octet-stream"
            files_for_sync[field_name] = (filename, content, content_type)

        # Build params (api key as query param if configured)
        auth_config = self.config.get("auth", {})
        api_key_query_param = auth_config.get("query_param")
        effective_api_key = self.api_key or self.passkey
        params = {}
        if api_key_query_param and effective_api_key:
            params[api_key_query_param] = effective_api_key

        response = await self._sync_multipart_post(
            url=url,
            files=files_for_sync,
            data=data_list,
            params=params if params else None
        )

        # Extract values for subsequent steps
        extracted = {}
        for extraction in step.get("extract", []):
            extracted[extraction["name"]] = self._extract_value(
                response,
                extraction.get("from", "json"),
                extraction.get("selector"),
                extraction.get("attribute"),
                extraction.get("cookie_name"),
                extraction.get("header_name"),
                extraction.get("json_path")
            )

        return {
            "response": response,
            "extracted": extracted,
            "step_name": step.get("name", "unknown")
        }

    # =========================================================================
    # TORZNAB XML PARSING
    # =========================================================================

    def _parse_torznab_xml(self, xml_text: str) -> List[Dict[str, Any]]:
        """
        Parse Torznab XML/RSS search results.

        C411 and many other trackers return search results in Torznab XML format
        (RSS with torznab namespace extensions).

        Args:
            xml_text: Raw XML response text

        Returns:
            List of parsed torrent dicts
        """
        results = []
        try:
            root = ET.fromstring(xml_text)

            # Find all items (RSS items)
            # Handle both namespaced and non-namespaced
            items = root.findall('.//item')

            for item in items:
                torrent = {}
                torrent['name'] = item.findtext('title', '')
                torrent['id'] = item.findtext('guid', '')
                torrent['download_link'] = item.findtext('link', '')
                torrent['pub_date'] = item.findtext('pubDate', '')
                torrent['category'] = item.findtext('category', '')

                # Parse size from enclosure or torznab attrs
                enclosure = item.find('enclosure')
                if enclosure is not None:
                    torrent['size'] = int(enclosure.get('length', '0') or '0')

                # Parse torznab:attr elements for extra fields
                for attr in item.findall('.//{http://torznab.com/schemas/2015/feed}attr'):
                    attr_name = attr.get('name', '')
                    attr_value = attr.get('value', '')

                    if attr_name == 'seeders':
                        torrent['seeders'] = int(attr_value or '0')
                    elif attr_name == 'peers':
                        torrent['leechers'] = int(attr_value or '0')
                    elif attr_name == 'size' and not torrent.get('size'):
                        torrent['size'] = int(attr_value or '0')
                    elif attr_name == 'infohash':
                        torrent['info_hash'] = attr_value
                    elif attr_name == 'imdbid':
                        torrent['imdb_id'] = attr_value
                    elif attr_name == 'tmdbid':
                        torrent['tmdb_id'] = attr_value

                # Also try non-namespaced torznab:attr (some trackers)
                for attr in item.findall('.//attr'):
                    attr_name = attr.get('name', '')
                    attr_value = attr.get('value', '')
                    if attr_name == 'seeders' and 'seeders' not in torrent:
                        torrent['seeders'] = int(attr_value or '0')
                    elif attr_name == 'peers' and 'leechers' not in torrent:
                        torrent['leechers'] = int(attr_value or '0')
                    elif attr_name == 'infohash' and 'info_hash' not in torrent:
                        torrent['info_hash'] = attr_value

                torrent.setdefault('seeders', 0)
                torrent.setdefault('leechers', 0)
                torrent.setdefault('size', 0)
                torrent.setdefault('info_hash', '')
                torrent.setdefault('torrent_id', torrent.get('id', ''))
                torrent.setdefault('quality', '')

                results.append(torrent)

        except ET.ParseError as e:
            logger.warning(f"Failed to parse Torznab XML: {e}")
        except Exception as e:
            logger.warning(f"Torznab XML parsing error: {e}")

        return results

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def _validate_upload_data(self, context: Dict[str, Any]) -> List[str]:
        """
        Validate upload data against YAML validation rules.

        Reads the validation section from config and checks:
        - required fields are present
        - min_length constraints
        - patterns (regex)

        Args:
            context: Upload context dict

        Returns:
            List of validation error messages (empty = valid)
        """
        errors = []
        validation_config = self.config.get("validation", {})

        for field_name, rules in validation_config.items():
            if not isinstance(rules, dict):
                continue

            value = context.get(field_name)

            # Required check
            if rules.get("required", False) and not value:
                errors.append(f"Missing required field: {field_name}")
                continue

            if value is None:
                continue

            # Min length
            min_length = rules.get("min_length")
            if min_length and isinstance(value, (str, bytes)):
                if len(value) < min_length:
                    errors.append(f"{field_name} too short: {len(value)} < {min_length}")

            # Max length
            max_length = rules.get("max_length")
            if max_length and isinstance(value, str) and len(value) > max_length:
                errors.append(f"{field_name} too long: {len(value)} > {max_length}")

            # Pattern
            pattern = rules.get("pattern")
            if pattern and isinstance(value, str):
                if not re.match(pattern, value):
                    errors.append(f"{field_name} does not match pattern: {pattern}")

        return errors

    # =========================================================================
    # SANITIZATION PIPELINE
    # =========================================================================

    def _sanitize_name(self, name: str, operations: Optional[List[Dict]] = None) -> str:
        """
        Apply sanitization pipeline to a name/string.

        Operations are defined in YAML:
        sanitize:
          operations:
            - type: "replace_spaces"
              replacement: "."
            - type: "remove_pattern"
              pattern: "\\(.*?\\)"
            - type: "collapse_dots"
            - type: "max_length"
              length: 255

        Args:
            name: Input string to sanitize
            operations: List of operation dicts (if None, reads from config)

        Returns:
            Sanitized string
        """
        if operations is None:
            sanitize_config = self.config.get("sanitize", {})
            operations = sanitize_config.get("operations", [])

        if not operations:
            return name

        result = name
        for op in operations:
            op_type = op.get("type", "")

            if op_type == "replace_spaces":
                replacement = op.get("replacement", ".")
                result = result.replace(" ", replacement)

            elif op_type == "remove_pattern":
                pattern = op.get("pattern", "")
                if pattern:
                    result = re.sub(pattern, "", result)

            elif op_type == "collapse_dots":
                result = re.sub(r'\.{2,}', '.', result)

            elif op_type == "strip_dots":
                result = result.strip('.')

            elif op_type == "max_length":
                length = op.get("length", 255)
                result = result[:length]

            elif op_type == "lowercase":
                result = result.lower()

            elif op_type == "uppercase":
                result = result.upper()

        return result

    # =========================================================================
    # TMDB DATA BUILDER
    # =========================================================================

    async def build_tmdb_data(
        self,
        tmdb_id: str,
        tmdb_type: str,
        db: Any
    ) -> Optional[Dict[str, Any]]:
        """
        Build tracker-specific TMDB data from cached TMDB info.

        Uses the tmdb_data section in YAML config to define the output format.
        If no tmdb_data section exists, builds a generic format.

        This replaces the hardcoded _build_c411_tmdb_data() in pipeline.py.

        Args:
            tmdb_id: TMDB ID
            tmdb_type: "movie" or "tv"
            db: Database session

        Returns:
            TMDB data dict formatted for the tracker's API, or None
        """
        if not tmdb_id:
            return None

        try:
            from ..models.tmdb_cache import TMDBCache
            from ..services.tmdb_cache_service import TMDBCacheService

            cache_entry = TMDBCache.get_cached(db, tmdb_id)
            if not cache_entry:
                logger.warning(f"No cached TMDB data for ID: {tmdb_id}")
                return None

            # Refresh if missing critical fields
            extra_data = cache_entry.extra_data or {}
            needs_refresh = (
                not extra_data.get('release_date')
                or not extra_data.get('production_countries')
                or not extra_data.get('imdb_id')
            )
            if not needs_refresh and cache_entry.cast and len(cache_entry.cast) > 0:
                first_cast = cache_entry.cast[0]
                if isinstance(first_cast, dict) and not first_cast.get('profile_path'):
                    needs_refresh = True

            if needs_refresh:
                try:
                    tmdb_service = TMDBCacheService(db)
                    await tmdb_service.get_metadata(tmdb_id, force_refresh=True)
                    cache_entry = TMDBCache.get_cached(db, tmdb_id)
                    if not cache_entry:
                        return None
                except Exception as e:
                    logger.warning(f"Failed to refresh TMDB cache for {tmdb_id}: {e}")

            extra_data = cache_entry.extra_data or {}
            ratings = cache_entry.ratings or {}

            # Build base TMDB data
            tmdb_data = {
                "id": int(tmdb_id),
                "title": cache_entry.title,
                "originalTitle": extra_data.get('original_title', cache_entry.title),
                "overview": cache_entry.plot or "",
                "voteAverage": ratings.get('vote_average', 0),
                "voteCount": ratings.get('vote_count', 0),
            }

            # Release date
            if extra_data.get('release_date'):
                tmdb_data["releaseDate"] = extra_data['release_date']
            elif cache_entry.year:
                tmdb_data["releaseDate"] = f"{cache_entry.year}-01-01"

            # Production countries
            if extra_data.get('production_countries'):
                tmdb_data["productionCountries"] = extra_data['production_countries']

            # IMDB ID
            if extra_data.get('imdb_id'):
                tmdb_data["imdbId"] = extra_data['imdb_id']

            # Runtime
            if extra_data.get('runtime'):
                tmdb_data["runtime"] = extra_data['runtime']

            # Poster/Backdrop
            if extra_data.get('poster_path'):
                tmdb_data["posterPath"] = extra_data['poster_path']
            if extra_data.get('backdrop_path'):
                tmdb_data["backdropPath"] = extra_data['backdrop_path']

            # Genres
            if extra_data.get('genres'):
                genres_raw = extra_data['genres']
                tmdb_genres = []
                for i, g in enumerate(genres_raw, start=1):
                    if isinstance(g, dict):
                        tmdb_genres.append({"id": g.get('id', i), "name": g.get('name', '')})
                    else:
                        tmdb_genres.append({"id": i, "name": g})
                tmdb_data["genres"] = tmdb_genres

            # Credits (cast)
            if cache_entry.cast:
                tmdb_data["credits"] = {
                    "cast": [
                        {
                            "id": actor.get('id', i),
                            "name": actor.get('name', 'Unknown'),
                            "character": actor.get('character', ''),
                            "profile_path": actor.get('profile_path', '')
                        }
                        for i, actor in enumerate(cache_entry.cast[:10], start=1)
                    ],
                    "crew": []
                }

            # TV-specific field renaming
            if tmdb_type == 'tv':
                tmdb_data["name"] = tmdb_data.pop("title", "")
                tmdb_data["originalName"] = tmdb_data.pop("originalTitle", "")
                tmdb_data["firstAirDate"] = tmdb_data.pop("releaseDate", "")

            logger.debug(f"Built TMDB data for {tmdb_id}: {cache_entry.title}")
            return tmdb_data

        except Exception as e:
            logger.warning(f"Failed to build TMDB data: {e}")
            return None

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
