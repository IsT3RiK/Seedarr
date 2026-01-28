"""
ConfigurableUploader - JSON-based Upload Configuration System

This module provides a flexible upload system that works with JSON configuration
instead of hardcoded Python adapters. This allows adding new tracker support
without writing code - just configure the JSON.

Features:
    - Template-based configuration for common patterns
    - Support for REST API, multipart forms, custom auth
    - Field mapping from release data to tracker API
    - Validation and error handling

Templates:
    - rest_api_bearer: REST API with Bearer token auth (C411 style)
    - multipart_form: Multipart form upload (La Cale style)
    - torznab_upload: Torznab-compatible upload API

Usage:
    uploader = ConfigurableUploader(tracker.upload_config)
    result = await uploader.upload(
        torrent_data=bytes,
        release_data={...}
    )
"""

import logging
import httpx
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)


# =============================================================================
# Upload Configuration Templates
# =============================================================================

UPLOAD_TEMPLATES = {
    "rest_api_bearer": {
        "name": "REST API with Bearer Token",
        "description": "Standard REST API upload with Bearer token authentication (e.g., C411)",
        "config": {
            "type": "rest_api",
            "endpoint": "/api/torrents",
            "method": "POST",
            "content_type": "multipart/form-data",
            "auth": {
                "type": "bearer",
                "header": "Authorization",
                "prefix": "Bearer ",
                "credential_field": "api_key"
            },
            "fields": {
                "torrent": {
                    "type": "file",
                    "form_name": "torrent",
                    "source": "torrent_data",
                    "filename": "{release_name}.torrent",
                    "required": True
                },
                "nfo": {
                    "type": "file",
                    "form_name": "nfo",
                    "source": "nfo_data",
                    "filename": "{release_name}.nfo",
                    "required": True
                },
                "title": {
                    "type": "string",
                    "form_name": "title",
                    "source": "release_name",
                    "required": True
                },
                "description": {
                    "type": "string",
                    "form_name": "description",
                    "source": "description",
                    "required": True,
                    "min_length": 20
                },
                "categoryId": {
                    "type": "string",
                    "form_name": "categoryId",
                    "source": "category_id",
                    "required": True
                },
                "subcategoryId": {
                    "type": "string",
                    "form_name": "subcategoryId",
                    "source": "subcategory_id",
                    "required": False
                }
            },
            "response": {
                "success_field": "success",
                "torrent_id_field": "data.id",
                "torrent_url_template": "{tracker_url}/torrents/{torrent_id}",
                "error_field": "message"
            }
        }
    },

    "multipart_form_passkey": {
        "name": "Multipart Form with Passkey",
        "description": "Traditional form upload with passkey in URL (e.g., La Cale)",
        "config": {
            "type": "multipart_form",
            "endpoint": "/api/external/upload",
            "method": "POST",
            "content_type": "multipart/form-data",
            "auth": {
                "type": "query_param",
                "param_name": "passkey",
                "credential_field": "passkey"
            },
            "cloudflare": {
                "enabled": True,
                "flaresolverr_required": True
            },
            "fields": {
                "torrent": {
                    "type": "file",
                    "form_name": "torrent",
                    "source": "torrent_data",
                    "filename": "{release_name}.torrent",
                    "required": True
                },
                "nfo": {
                    "type": "file",
                    "form_name": "nfo",
                    "source": "nfo_data",
                    "filename": "{release_name}.nfo",
                    "required": True
                },
                "name": {
                    "type": "string",
                    "form_name": "name",
                    "source": "release_name",
                    "required": True
                },
                "description": {
                    "type": "string",
                    "form_name": "description",
                    "source": "description",
                    "required": False
                },
                "category_id": {
                    "type": "string",
                    "form_name": "category_id",
                    "source": "category_id",
                    "required": True
                },
                "tags": {
                    "type": "array",
                    "form_name": "tagId[]",
                    "source": "tag_ids",
                    "required": False,
                    "repeat_field": True
                },
                "tmdb_id": {
                    "type": "string",
                    "form_name": "tmdb_id",
                    "source": "tmdb_id",
                    "required": False
                }
            },
            "response": {
                "success_field": "success",
                "torrent_id_field": "data.torrent_id",
                "torrent_url_template": "{tracker_url}/torrents/{torrent_id}",
                "error_field": "message"
            }
        }
    },

    "json_api": {
        "name": "JSON API",
        "description": "JSON body API upload with API key header",
        "config": {
            "type": "json_api",
            "endpoint": "/api/upload",
            "method": "POST",
            "content_type": "application/json",
            "auth": {
                "type": "header",
                "header": "X-Api-Key",
                "credential_field": "api_key"
            },
            "fields": {
                "torrent": {
                    "type": "base64",
                    "json_field": "torrent",
                    "source": "torrent_data",
                    "required": True
                },
                "nfo": {
                    "type": "base64",
                    "json_field": "nfo",
                    "source": "nfo_data",
                    "required": True
                },
                "name": {
                    "type": "string",
                    "json_field": "name",
                    "source": "release_name",
                    "required": True
                },
                "category": {
                    "type": "string",
                    "json_field": "category",
                    "source": "category_id",
                    "required": True
                }
            },
            "response": {
                "success_field": "success",
                "torrent_id_field": "id",
                "error_field": "error"
            }
        }
    }
}


def get_upload_templates() -> Dict[str, Any]:
    """Get all available upload configuration templates."""
    return {
        key: {
            "name": template["name"],
            "description": template["description"],
            "config": template["config"]
        }
        for key, template in UPLOAD_TEMPLATES.items()
    }


def get_template_config(template_name: str) -> Optional[Dict[str, Any]]:
    """Get a specific template configuration."""
    if template_name in UPLOAD_TEMPLATES:
        return UPLOAD_TEMPLATES[template_name]["config"].copy()
    return None


# =============================================================================
# Configurable Uploader
# =============================================================================

class ConfigurableUploader:
    """
    Configurable uploader that uses JSON configuration to perform uploads.

    Instead of hardcoded Python adapters, this class reads a JSON config
    that describes how to upload to a specific tracker.

    Attributes:
        config: Upload configuration dictionary
        tracker_url: Base URL of the tracker
        credentials: Dict with passkey, api_key, etc.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        tracker_url: str,
        credentials: Dict[str, str],
        flaresolverr_url: Optional[str] = None,
        timeout: int = 120
    ):
        """
        Initialize ConfigurableUploader.

        Args:
            config: Upload configuration dictionary
            tracker_url: Base URL of the tracker
            credentials: Dict with authentication credentials
            flaresolverr_url: FlareSolverr URL if Cloudflare bypass needed
            timeout: Request timeout in seconds
        """
        self.config = config
        self.tracker_url = tracker_url.rstrip('/')
        self.credentials = credentials
        self.flaresolverr_url = flaresolverr_url
        self.timeout = timeout

        logger.info(
            f"ConfigurableUploader initialized for {tracker_url} "
            f"with config type: {config.get('type', 'unknown')}"
        )

    def _get_credential(self, field: str) -> Optional[str]:
        """Get a credential by field name."""
        return self.credentials.get(field)

    def _build_auth_headers(self) -> Dict[str, str]:
        """Build authentication headers based on config."""
        headers = {}
        auth_config = self.config.get("auth", {})

        if auth_config.get("type") == "bearer":
            credential = self._get_credential(auth_config.get("credential_field", "api_key"))
            if credential:
                prefix = auth_config.get("prefix", "Bearer ")
                header_name = auth_config.get("header", "Authorization")
                headers[header_name] = f"{prefix}{credential}"

        elif auth_config.get("type") == "header":
            credential = self._get_credential(auth_config.get("credential_field", "api_key"))
            if credential:
                header_name = auth_config.get("header", "X-Api-Key")
                headers[header_name] = credential

        return headers

    def _build_auth_params(self) -> Dict[str, str]:
        """Build authentication query parameters based on config."""
        params = {}
        auth_config = self.config.get("auth", {})

        if auth_config.get("type") == "query_param":
            credential = self._get_credential(auth_config.get("credential_field", "passkey"))
            if credential:
                param_name = auth_config.get("param_name", "passkey")
                params[param_name] = credential

        return params

    def _format_string(self, template: str, data: Dict[str, Any]) -> str:
        """Format a template string with data."""
        try:
            return template.format(**data)
        except KeyError:
            return template

    def _get_nested_value(self, data: Dict, path: str) -> Any:
        """Get a nested value from a dict using dot notation."""
        keys = path.split('.')
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        return value

    def _build_form_data(
        self,
        release_data: Dict[str, Any],
        torrent_data: bytes,
        nfo_data: bytes
    ) -> tuple:
        """
        Build multipart form data based on config.

        Returns:
            Tuple of (files_dict, data_dict)
        """
        files = {}
        data = {}

        fields_config = self.config.get("fields", {})
        format_data = {
            "release_name": release_data.get("release_name", "release"),
            "tracker_url": self.tracker_url,
            **release_data
        }

        for field_name, field_config in fields_config.items():
            field_type = field_config.get("type")
            form_name = field_config.get("form_name", field_name)
            source = field_config.get("source")
            required = field_config.get("required", False)

            # Get source value
            if source == "torrent_data":
                value = torrent_data
            elif source == "nfo_data":
                value = nfo_data
            else:
                value = release_data.get(source)

            # Check required
            if required and not value:
                if field_type == "string":
                    # Use a default or raise error
                    min_length = field_config.get("min_length", 0)
                    if min_length > 0:
                        raise ValueError(f"Required field '{field_name}' is missing or empty")
                else:
                    raise ValueError(f"Required field '{field_name}' is missing")

            if value is None:
                continue

            # Process by type
            if field_type == "file":
                filename = self._format_string(
                    field_config.get("filename", f"{field_name}.bin"),
                    format_data
                )
                files[form_name] = (filename, value)

            elif field_type == "string":
                data[form_name] = str(value)

            elif field_type == "array":
                # Handle array fields (like tags)
                if field_config.get("repeat_field"):
                    # Repeat the field for each value
                    if isinstance(value, list):
                        for v in value:
                            if form_name not in data:
                                data[form_name] = []
                            data[form_name].append(str(v))
                else:
                    data[form_name] = value

        return files, data

    async def _upload_multipart(
        self,
        release_data: Dict[str, Any],
        torrent_data: bytes,
        nfo_data: bytes
    ) -> Dict[str, Any]:
        """Perform multipart form upload."""
        endpoint = self.config.get("endpoint", "/api/upload")
        url = f"{self.tracker_url}{endpoint}"

        headers = self._build_auth_headers()
        params = self._build_auth_params()

        files_dict, data_dict = self._build_form_data(
            release_data, torrent_data, nfo_data
        )

        # Handle repeated fields (like tags)
        # httpx needs special handling for repeated fields
        form_data = []
        for key, value in data_dict.items():
            if isinstance(value, list):
                for v in value:
                    form_data.append((key, str(v)))
            else:
                form_data.append((key, str(value)))

        logger.info(f"Uploading to {url} with {len(files_dict)} files")
        logger.debug(f"Form data keys: {list(data_dict.keys())}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    params=params,
                    data=form_data,
                    files=files_dict
                )

                return self._parse_response(response, release_data)

        except httpx.TimeoutException:
            logger.error(f"Upload timeout to {url}")
            return {
                "success": False,
                "message": "Upload timeout"
            }
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return {
                "success": False,
                "message": str(e)
            }

    async def _upload_json(
        self,
        release_data: Dict[str, Any],
        torrent_data: bytes,
        nfo_data: bytes
    ) -> Dict[str, Any]:
        """Perform JSON API upload."""
        import base64

        endpoint = self.config.get("endpoint", "/api/upload")
        url = f"{self.tracker_url}{endpoint}"

        headers = self._build_auth_headers()
        headers["Content-Type"] = "application/json"
        params = self._build_auth_params()

        # Build JSON body
        json_body = {}
        fields_config = self.config.get("fields", {})

        for field_name, field_config in fields_config.items():
            field_type = field_config.get("type")
            json_field = field_config.get("json_field", field_name)
            source = field_config.get("source")

            # Get source value
            if source == "torrent_data":
                value = torrent_data
            elif source == "nfo_data":
                value = nfo_data
            else:
                value = release_data.get(source)

            if value is None:
                continue

            # Process by type
            if field_type == "base64":
                json_body[json_field] = base64.b64encode(value).decode('utf-8')
            elif field_type == "string":
                json_body[json_field] = str(value)
            else:
                json_body[json_field] = value

        logger.info(f"Uploading JSON to {url}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    params=params,
                    json=json_body
                )

                return self._parse_response(response, release_data)

        except httpx.TimeoutException:
            logger.error(f"Upload timeout to {url}")
            return {
                "success": False,
                "message": "Upload timeout"
            }
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return {
                "success": False,
                "message": str(e)
            }

    def _parse_response(
        self,
        response: httpx.Response,
        release_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Parse upload response based on config."""
        response_config = self.config.get("response", {})

        logger.info(f"Upload response status: {response.status_code}")
        logger.debug(f"Response body: {response.text[:500]}")

        if response.status_code >= 400:
            return {
                "success": False,
                "message": f"HTTP {response.status_code}: {response.text[:200]}"
            }

        try:
            data = response.json()
        except Exception:
            # Non-JSON response
            if response.status_code < 300:
                return {
                    "success": True,
                    "message": "Upload completed (no JSON response)"
                }
            return {
                "success": False,
                "message": f"Invalid response: {response.text[:200]}"
            }

        # Check success field
        success_field = response_config.get("success_field", "success")
        success = self._get_nested_value(data, success_field)

        if success is False:
            error_field = response_config.get("error_field", "message")
            error = self._get_nested_value(data, error_field) or "Upload failed"
            return {
                "success": False,
                "message": str(error)
            }

        # Get torrent ID
        torrent_id_field = response_config.get("torrent_id_field", "id")
        torrent_id = self._get_nested_value(data, torrent_id_field)

        # Build torrent URL
        url_template = response_config.get("torrent_url_template", "")
        torrent_url = self._format_string(url_template, {
            "tracker_url": self.tracker_url,
            "torrent_id": torrent_id or "",
            **release_data
        })

        return {
            "success": True,
            "torrent_id": str(torrent_id) if torrent_id else None,
            "torrent_url": torrent_url,
            "message": "Upload successful",
            "response_data": data
        }

    async def upload(
        self,
        torrent_data: bytes,
        nfo_data: bytes,
        release_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Upload a torrent using the configured method.

        Args:
            torrent_data: Raw .torrent file bytes
            nfo_data: NFO file bytes
            release_data: Dict with release info (release_name, category_id, etc.)

        Returns:
            Dict with upload result
        """
        config_type = self.config.get("type", "multipart_form")

        logger.info(
            f"Starting configurable upload: type={config_type}, "
            f"release={release_data.get('release_name', 'unknown')}"
        )

        if config_type in ("rest_api", "multipart_form"):
            return await self._upload_multipart(release_data, torrent_data, nfo_data)
        elif config_type == "json_api":
            return await self._upload_json(release_data, torrent_data, nfo_data)
        else:
            logger.error(f"Unknown upload config type: {config_type}")
            return {
                "success": False,
                "message": f"Unknown upload config type: {config_type}"
            }

    async def test_connection(self) -> Dict[str, Any]:
        """
        Test connection to the tracker.

        Attempts to verify that the tracker is reachable and credentials work.

        Returns:
            Dict with test result
        """
        # Try a simple request to verify connectivity
        try:
            headers = self._build_auth_headers()
            params = self._build_auth_params()

            async with httpx.AsyncClient(timeout=10) as client:
                # Try the base URL first
                response = await client.get(
                    self.tracker_url,
                    headers=headers,
                    params=params,
                    follow_redirects=True
                )

                if response.status_code < 400:
                    return {
                        "success": True,
                        "message": f"Connected to {self.tracker_url}",
                        "status_code": response.status_code
                    }
                elif response.status_code == 403:
                    return {
                        "success": False,
                        "message": "Access denied - check credentials or Cloudflare bypass",
                        "status_code": response.status_code
                    }
                else:
                    return {
                        "success": False,
                        "message": f"HTTP {response.status_code}",
                        "status_code": response.status_code
                    }

        except httpx.TimeoutException:
            return {
                "success": False,
                "message": "Connection timeout"
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e)
            }


# =============================================================================
# Helper Functions
# =============================================================================

def create_uploader_from_tracker(
    tracker: Any,
    flaresolverr_url: Optional[str] = None
) -> Optional[ConfigurableUploader]:
    """
    Create a ConfigurableUploader from a Tracker model instance.

    Args:
        tracker: Tracker model instance
        flaresolverr_url: FlareSolverr URL if needed

    Returns:
        ConfigurableUploader instance or None if no config
    """
    if not tracker.upload_config:
        return None

    credentials = {
        "passkey": tracker.passkey,
        "api_key": tracker.api_key
    }

    return ConfigurableUploader(
        config=tracker.upload_config,
        tracker_url=tracker.tracker_url,
        credentials=credentials,
        flaresolverr_url=flaresolverr_url
    )


def validate_upload_config(config: Dict[str, Any]) -> tuple:
    """
    Validate an upload configuration.

    Args:
        config: Configuration dictionary to validate

    Returns:
        Tuple of (is_valid: bool, errors: List[str])
    """
    errors = []

    # Required top-level fields
    if "type" not in config:
        errors.append("Missing 'type' field")
    elif config["type"] not in ("rest_api", "multipart_form", "json_api"):
        errors.append(f"Unknown type: {config['type']}")

    if "endpoint" not in config:
        errors.append("Missing 'endpoint' field")

    if "fields" not in config:
        errors.append("Missing 'fields' configuration")
    elif not isinstance(config["fields"], dict):
        errors.append("'fields' must be a dictionary")
    else:
        # Check for required fields
        has_torrent = False
        for field_name, field_config in config["fields"].items():
            if field_config.get("source") == "torrent_data":
                has_torrent = True
        if not has_torrent:
            errors.append("No torrent file field configured")

    # Auth validation
    auth = config.get("auth", {})
    if auth:
        auth_type = auth.get("type")
        if auth_type not in (None, "bearer", "header", "query_param"):
            errors.append(f"Unknown auth type: {auth_type}")

    return len(errors) == 0, errors
