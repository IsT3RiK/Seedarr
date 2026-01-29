"""
Settings API Routes for Seedarr v2.0

This module provides FastAPI routes for managing application settings through the
database-driven configuration system. All runtime settings are stored in the
Settings database model and editable via these API endpoints.

Features:
    - GET /settings: Render settings UI page
    - GET /api/settings: Retrieve current settings as JSON
    - PUT /api/settings: Update settings
    - POST /api/settings/export: Export settings to JSON (includes all fields for full backup)
    - POST /api/settings/import: Import settings from JSON file
    - POST /api/settings/test-connection: Test external service connections

Security:
    - Sensitive values (passkeys, passwords) masked in GET responses
    - Raw values only returned when needed for editing
    - Export includes all fields including sensitive ones for full backup/restore
"""

from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime, timezone
import logging
import json
import httpx

from app.models.settings import Settings
from app.models.validators import (
    validate_url,
    validate_path_no_traversal,
    sanitize_path,
    path_validator,
    url_validator
)
from app.utils.tmdb_auth import detect_tmdb_credential_type, format_tmdb_request

logger = logging.getLogger(__name__)

# Router
router = APIRouter()

# Templates - auto-detect path based on working directory
import os
templates_dir = "templates" if os.path.exists("templates") else "backend/templates"
templates = Jinja2Templates(directory=templates_dir)

# Database dependency
from app.database import get_db


class SettingsUpdateRequest(BaseModel):
    """Request model for updating settings with validation."""

    # Tracker settings - URL must be http/https
    tracker_url: Optional[str] = Field(
        None,
        description="La Cale tracker base URL",
        examples=["https://lacale.example.com"]
    )
    tracker_passkey: Optional[str] = Field(
        None,
        description="Tracker passkey for authentication",
        min_length=16
    )
    # Note: announce_url is computed from tracker_url + passkey (not stored separately)

    # External services - URLs must be http/https
    flaresolverr_url: Optional[str] = Field(
        None,
        description="FlareSolverr service URL",
        examples=["http://localhost:8191"]
    )
    qbittorrent_host: Optional[str] = Field(
        None,
        description="qBittorrent Web UI host:port",
        examples=["localhost:8080"]
    )
    qbittorrent_username: Optional[str] = Field(None, description="qBittorrent username")
    qbittorrent_password: Optional[str] = Field(None, description="qBittorrent password")
    tmdb_api_key: Optional[str] = Field(
        None,
        description="TMDB API key or Bearer token",
        min_length=16
    )

    # Prowlarr integration
    prowlarr_url: Optional[str] = Field(
        None,
        description="Prowlarr instance URL",
        examples=["http://localhost:9696"]
    )
    prowlarr_api_key: Optional[str] = Field(
        None,
        description="Prowlarr API key",
        min_length=16
    )

    # Directory paths - validated for path traversal
    input_media_path: Optional[str] = Field(None, description="Input media directory path")
    output_dir: Optional[str] = Field(None, description="Output directory path")

    # Application settings with bounds
    log_level: Optional[str] = Field(
        None,
        description="Log level (DEBUG, INFO, WARNING, ERROR)",
        pattern=r'^(DEBUG|INFO|WARNING|ERROR)$'
    )
    tmdb_cache_ttl_days: Optional[int] = Field(
        None,
        description="TMDB cache TTL in days",
        ge=1,
        le=365
    )
    tag_sync_interval_hours: Optional[int] = Field(
        None,
        description="Tag sync interval in hours",
        ge=1,
        le=168
    )

    # Validators using Pydantic v2 syntax
    @field_validator('tracker_url', 'flaresolverr_url', 'prowlarr_url', mode='before')
    @classmethod
    def validate_url_fields(cls, v: Optional[str]) -> Optional[str]:
        """Validate URL fields have proper http/https format."""
        return url_validator(v)

    @field_validator('input_media_path', 'output_dir', mode='before')
    @classmethod
    def validate_path_fields(cls, v: Optional[str]) -> Optional[str]:
        """Validate and sanitize path fields, blocking path traversal."""
        return path_validator(v)


class SettingsResponse(BaseModel):
    """Response model for settings."""

    id: int
    # Tracker settings
    tracker_url: Optional[str]
    tracker_passkey: Optional[str]
    announce_url: Optional[str]  # Computed from tracker_url + passkey
    # External services
    flaresolverr_url: Optional[str]
    qbittorrent_host: Optional[str]
    qbittorrent_username: Optional[str]
    qbittorrent_password: Optional[str]
    tmdb_api_key: Optional[str]
    # Prowlarr integration
    prowlarr_url: Optional[str]
    prowlarr_api_key: Optional[str]
    # Directory paths
    input_media_path: Optional[str]
    output_dir: Optional[str]
    # Application settings
    log_level: Optional[str]
    tmdb_cache_ttl_days: Optional[int]
    tag_sync_interval_hours: Optional[int]
    # Timestamps
    created_at: Optional[str]
    updated_at: Optional[str]


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    """
    Render the settings UI page.

    This page displays all configuration settings with a form for editing.
    Sensitive fields (passkeys, passwords) are displayed with password inputs.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML response with settings form
    """
    try:
        settings = Settings.get_settings(db)

        # Pass settings to template (unmask for editing)
        settings_dict = settings.to_dict(mask_secrets=False)

        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "settings": settings_dict,
                "log_levels": ["DEBUG", "INFO", "WARNING", "ERROR"]
            }
        )
    except Exception as e:
        logger.error(f"Error rendering settings page: {e}")
        raise HTTPException(status_code=500, detail=f"Error loading settings: {str(e)}")


@router.get("/api/settings", response_model=SettingsResponse)
async def get_settings(db: Session = Depends(get_db), mask_secrets: bool = True):
    """
    Get current application settings.

    Args:
        db: Database session
        mask_secrets: If True, mask sensitive values with asterisks

    Returns:
        Current settings as JSON
    """
    try:
        settings = Settings.get_settings(db)
        settings_dict = settings.to_dict(mask_secrets=mask_secrets)
        return settings_dict
    except Exception as e:
        logger.error(f"Error retrieving settings: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving settings: {str(e)}")


@router.put("/api/settings", response_model=SettingsResponse)
async def update_settings(
    settings_update: SettingsUpdateRequest,
    db: Session = Depends(get_db)
):
    """
    Update application settings.

    Only provided fields will be updated. Null/missing fields are ignored.

    Args:
        settings_update: Settings update request
        db: Database session

    Returns:
        Updated settings
    """
    try:
        # Convert to dict and filter out None values
        # Pydantic validation already applied via field_validator decorators
        update_data = settings_update.model_dump(exclude_none=True)

        # Additional validation is handled by Pydantic Field constraints:
        # - log_level: pattern validation
        # - tmdb_cache_ttl_days: ge=1, le=365
        # - tag_sync_interval_hours: ge=1, le=168
        # - URLs: validated via url_validator
        # - Paths: validated via path_validator

        # Update settings
        settings = Settings.update_settings(db, **update_data)

        logger.info(f"Settings updated: {list(update_data.keys())}")

        return settings.to_dict(mask_secrets=True)

    except HTTPException:
        raise
    except ValueError as e:
        # Pydantic validation errors
        logger.warning(f"Validation error updating settings: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating settings: {str(e)}")


@router.post("/api/settings/export")
async def export_settings(db: Session = Depends(get_db)):
    """
    Export current application settings as JSON.

    Returns settings in a structured format with metadata (version, timestamp).
    All settings are exported including sensitive fields (passkeys, passwords, API keys)
    to allow full backup and restore functionality.

    WARNING: The exported file contains sensitive credentials. Store securely.

    Args:
        db: Database session

    Returns:
        JSON object with version, timestamp, and settings
    """
    try:
        settings = Settings.get_settings(db)
        settings_dict = settings.to_dict(mask_secrets=False)

        # Only exclude metadata fields (id, timestamps) - keep all config fields including sensitive ones
        excluded_fields = {'id', 'created_at', 'updated_at'}

        exported_settings = {
            key: value for key, value in settings_dict.items()
            if key not in excluded_fields
        }

        # Build export response with metadata
        export_data = {
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "settings": exported_settings
        }

        logger.info("Settings exported successfully (including sensitive fields)")
        return export_data

    except Exception as e:
        logger.error(f"Error exporting settings: {e}")
        raise HTTPException(status_code=500, detail=f"Error exporting settings: {str(e)}")


@router.post("/api/settings/import")
async def import_settings(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Import application settings from a JSON file.

    Accepts a JSON file with settings data (in the format produced by export).
    Validates the format and updates settings. Sensitive fields should be
    configured manually for security reasons.

    Args:
        file: Uploaded JSON file
        db: Database session

    Returns:
        Success message with count of imported settings
    """
    try:
        # Validate file type
        if not file.filename.endswith('.json'):
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Please upload a JSON file."
            )

        # Read and parse JSON content
        content = await file.read()
        try:
            import_data = json.loads(content)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON format: {str(e)}"
            )

        # Validate import data structure
        if not isinstance(import_data, dict):
            raise HTTPException(
                status_code=400,
                detail="Invalid import format. Expected JSON object."
            )

        # Extract settings from import data
        # Support both direct settings and wrapped format (with version/metadata)
        if "settings" in import_data:
            settings_data = import_data["settings"]
            version = import_data.get("version", "unknown")
            logger.info(f"Importing settings from file version {version}")
        else:
            # Assume direct settings format
            settings_data = import_data
            logger.info("Importing settings from direct format")

        if not isinstance(settings_data, dict):
            raise HTTPException(
                status_code=400,
                detail="Invalid settings format. Expected object."
            )

        # Filter out None values and metadata fields
        excluded_fields = {'id', 'created_at', 'updated_at'}
        update_data = {
            key: value for key, value in settings_data.items()
            if value is not None and key not in excluded_fields
        }

        if not update_data:
            raise HTTPException(
                status_code=400,
                detail="No valid settings found in import file."
            )

        # Validate settings fields using validators module
        if 'log_level' in update_data:
            if update_data['log_level'] not in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid log_level. Must be DEBUG, INFO, WARNING, or ERROR"
                )

        if 'tmdb_cache_ttl_days' in update_data:
            if not isinstance(update_data['tmdb_cache_ttl_days'], int) or update_data['tmdb_cache_ttl_days'] < 1:
                raise HTTPException(
                    status_code=400,
                    detail="tmdb_cache_ttl_days must be an integer of at least 1"
                )

        if 'tag_sync_interval_hours' in update_data:
            if not isinstance(update_data['tag_sync_interval_hours'], int) or update_data['tag_sync_interval_hours'] < 1:
                raise HTTPException(
                    status_code=400,
                    detail="tag_sync_interval_hours must be an integer of at least 1"
                )

        # Validate URLs
        url_fields = ['tracker_url', 'flaresolverr_url', 'prowlarr_url']
        for field in url_fields:
            if field in update_data and update_data[field]:
                if not validate_url(update_data[field]):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid URL format for {field}. Must be http or https."
                    )

        # Validate and sanitize paths
        path_fields = ['input_media_path', 'output_dir']
        for field in path_fields:
            if field in update_data and update_data[field]:
                # Sanitize path
                update_data[field] = sanitize_path(update_data[field])
                # Check for path traversal
                if not validate_path_no_traversal(update_data[field]):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Path traversal not allowed in {field}"
                    )

        # Update settings in database
        Settings.update_settings(db, **update_data)

        logger.info(f"Settings imported successfully: {list(update_data.keys())}")

        return {
            "success": True,
            "message": "Settings imported successfully",
            "imported_count": len(update_data),
            "imported_fields": list(update_data.keys())
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error importing settings: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error importing settings: {str(e)}"
        )


@router.post("/api/settings/test-connection")
async def test_connection(
    service: str,
    api_key: Optional[str] = None,
    url: Optional[str] = None,
    host: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    tracker_url: Optional[str] = None,
    passkey: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Test connection to external services.

    Args:
        service: Service name (flaresolverr, qbittorrent, tmdb, tracker)
        api_key: Optional API key to test (for TMDB). If not provided, uses database value.
        tracker_url: Optional tracker URL (for tracker test)
        passkey: Optional passkey (for tracker test)
        db: Database session

    Returns:
        Connection test result
    """
    try:
        settings = Settings.get_settings(db)

        if service == "flaresolverr":
            # Use form value if provided, otherwise fall back to database
            flaresolverr_url = url if url else settings.flaresolverr_url

            if not flaresolverr_url:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Please configure FlareSolverr URL first"
                }

            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    # FlareSolverr health endpoint
                    response = await client.get(f"{flaresolverr_url.rstrip('/')}/health")

                    if response.status_code == 200:
                        logger.info("FlareSolverr connection test successful")
                        return {
                            "service": service,
                            "status": "success",
                            "message": "FlareSolverr is running and healthy",
                            "url": flaresolverr_url
                        }
                    else:
                        return {
                            "service": service,
                            "status": "error",
                            "message": f"FlareSolverr returned HTTP {response.status_code}",
                            "url": flaresolverr_url
                        }
            except httpx.TimeoutException:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Connection timeout. Check if FlareSolverr is running."
                }
            except httpx.ConnectError:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Cannot connect to FlareSolverr. Check URL and if service is running."
                }
            except Exception as e:
                return {
                    "service": service,
                    "status": "error",
                    "message": f"Connection error: {str(e)}"
                }

        elif service == "qbittorrent":
            # Use form values if provided, otherwise fall back to database
            qbit_host = host if host else settings.qbittorrent_host
            qbit_username = username if username is not None else settings.qbittorrent_username
            qbit_password = password if password is not None else settings.qbittorrent_password

            if not qbit_host:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Please configure qBittorrent host first"
                }

            try:
                # Build qBittorrent base URL
                qbit_url = qbit_host
                if not qbit_url.startswith(("http://", "https://")):
                    qbit_url = f"http://{qbit_url}"

                async with httpx.AsyncClient(timeout=10.0) as client:
                    # First try to get app version
                    version_response = await client.get(f"{qbit_url}/api/v2/app/version")

                    # If 403, try to authenticate first (some qBit configs require auth for all endpoints)
                    if version_response.status_code == 403:
                        if not qbit_username:
                            return {
                                "service": service,
                                "status": "error",
                                "message": "qBittorrent requires authentication. Please configure credentials."
                            }

                        # Try to login
                        login_response = await client.post(
                            f"{qbit_url}/api/v2/auth/login",
                            data={
                                "username": qbit_username,
                                "password": qbit_password or ""
                            }
                        )

                        if login_response.text == "Ok.":
                            # Auth successful, now get version
                            version_response = await client.get(f"{qbit_url}/api/v2/app/version")
                            if version_response.status_code == 200:
                                version = version_response.text
                                logger.info("qBittorrent connection and auth successful")
                                return {
                                    "service": service,
                                    "status": "success",
                                    "message": f"Connected to qBittorrent v{version} (authenticated)",
                                    "host": qbit_host
                                }
                            else:
                                return {
                                    "service": service,
                                    "status": "warning",
                                    "message": "Authenticated but could not get version",
                                    "host": qbit_host
                                }
                        else:
                            return {
                                "service": service,
                                "status": "error",
                                "message": f"Authentication failed. Check username/password. Response: {login_response.text}",
                                "host": qbit_host
                            }

                    elif version_response.status_code == 200:
                        version = version_response.text
                        # Try to login if credentials are provided
                        if qbit_username:
                            login_response = await client.post(
                                f"{qbit_url}/api/v2/auth/login",
                                data={
                                    "username": qbit_username,
                                    "password": qbit_password or ""
                                }
                            )
                            if login_response.text == "Ok.":
                                logger.info("qBittorrent connection and auth successful")
                                return {
                                    "service": service,
                                    "status": "success",
                                    "message": f"Connected to qBittorrent v{version} (authenticated)",
                                    "host": qbit_host
                                }
                            else:
                                return {
                                    "service": service,
                                    "status": "warning",
                                    "message": f"qBittorrent v{version} found but authentication failed",
                                    "host": qbit_host
                                }
                        else:
                            logger.info("qBittorrent connection successful (no auth)")
                            return {
                                "service": service,
                                "status": "success",
                                "message": f"Connected to qBittorrent v{version}",
                                "host": qbit_host
                            }
                    else:
                        return {
                            "service": service,
                            "status": "error",
                            "message": f"qBittorrent returned HTTP {version_response.status_code}"
                        }
            except httpx.TimeoutException:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Connection timeout. Check if qBittorrent Web UI is enabled."
                }
            except httpx.ConnectError:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Cannot connect to qBittorrent. Check host and if Web UI is enabled."
                }
            except Exception as e:
                return {
                    "service": service,
                    "status": "error",
                    "message": f"Connection error: {str(e)}"
                }
        elif service == "tmdb":
            # Use provided api_key if available, otherwise fall back to database value
            tmdb_key = api_key if api_key else settings.tmdb_api_key

            if not tmdb_key:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Please configure TMDB API key first"
                }

            # Detect credential type and format request
            try:
                credential_type = detect_tmdb_credential_type(tmdb_key)
                params, headers = format_tmdb_request(tmdb_key)

                logger.info(f"Testing TMDB connection using {credential_type} authentication")

                # Make test request to TMDB configuration endpoint (lightweight endpoint for testing)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        "https://api.themoviedb.org/3/configuration",
                        params=params,
                        headers=headers
                    )

                    # Check response status
                    if response.status_code == 200:
                        logger.info(f"✓ TMDB connection test successful ({credential_type})")

                        # Fetch sample movie data (Harry Potter 3) for template preview
                        sample_data = None
                        try:
                            # Harry Potter and the Prisoner of Azkaban - TMDB ID 673
                            movie_response = await client.get(
                                "https://api.themoviedb.org/3/movie/673",
                                params={**params, "language": "fr-FR", "append_to_response": "credits,videos"},
                                headers=headers
                            )
                            if movie_response.status_code == 200:
                                movie = movie_response.json()
                                credits = movie.get('credits', {})
                                cast = credits.get('cast', [])[:6]
                                crew = credits.get('crew', [])
                                directors = [c['name'] for c in crew if c.get('job') == 'Director']
                                videos = movie.get('videos', {}).get('results', [])
                                trailer = next((v for v in videos if v.get('type') == 'Trailer' and v.get('site') == 'YouTube'), None)

                                sample_data = {
                                    "title": movie.get('title', ''),
                                    "original_title": movie.get('original_title', ''),
                                    "year": movie.get('release_date', '')[:4] if movie.get('release_date') else '',
                                    "release_date": movie.get('release_date', ''),
                                    "poster_url": f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}" if movie.get('poster_path') else '',
                                    "backdrop_url": f"https://image.tmdb.org/t/p/original{movie.get('backdrop_path')}" if movie.get('backdrop_path') else '',
                                    "rating": str(round(movie.get('vote_average', 0), 1)),
                                    "rating_10": f"{round(movie.get('vote_average', 0), 1)}/10",
                                    "genres": ', '.join([g['name'] for g in movie.get('genres', [])]),
                                    "overview": movie.get('overview', ''),
                                    "tagline": movie.get('tagline', ''),
                                    "runtime": f"{movie.get('runtime', 0) // 60}h et {movie.get('runtime', 0) % 60}min" if movie.get('runtime') else '',
                                    "country": ', '.join([c['iso_3166_1'] for c in movie.get('production_countries', [])[:2]]),
                                    "director": ', '.join(directors),
                                    "tmdb_id": str(movie.get('id', '')),
                                    "imdb_id": movie.get('imdb_id', ''),
                                    "tmdb_url": f"https://www.themoviedb.org/movie/{movie.get('id')}",
                                    "trailer_url": f"https://www.youtube.com/watch?v={trailer['key']}" if trailer else '',
                                    "cast_names": ', '.join([c['name'] for c in cast]),
                                }
                                # Add individual cast members
                                for i, actor in enumerate(cast, 1):
                                    sample_data[f"cast_{i}_name"] = actor.get('name', '')
                                    sample_data[f"cast_{i}_character"] = actor.get('character', '')
                                    profile = f"https://image.tmdb.org/t/p/w185{actor['profile_path']}" if actor.get('profile_path') else ''
                                    sample_data[f"cast_{i}_photo"] = f"[img]{profile}[/img]" if profile else ''
                                    # Card format: inline display for grid layout
                                    if profile:
                                        sample_data[f"cast_{i}_card"] = f"[inline][img]{profile}[/img]\n[color=#eab308]{actor.get('name', '')}[/color][/inline]"
                                    else:
                                        sample_data[f"cast_{i}_card"] = actor.get('name', '')

                                logger.info("✓ Sample movie data fetched (Harry Potter 3)")
                        except Exception as e:
                            logger.warning(f"Could not fetch sample movie data: {e}")

                        return {
                            "service": service,
                            "status": "success",
                            "message": f"Successfully authenticated with TMDB using {credential_type} credentials",
                            "credential_type": credential_type,
                            "sample_data": sample_data
                        }
                    elif response.status_code == 401:
                        logger.warning(f"✗ TMDB authentication failed ({credential_type})")
                        return {
                            "service": service,
                            "status": "error",
                            "message": f"TMDB authentication failed. Please check your {credential_type} credentials.",
                            "credential_type": credential_type,
                            "http_status": response.status_code
                        }
                    elif response.status_code == 404:
                        # This shouldn't happen for the configuration endpoint
                        logger.error("TMDB configuration endpoint returned 404")
                        return {
                            "service": service,
                            "status": "error",
                            "message": "TMDB API endpoint not found. Please check TMDB service status.",
                            "http_status": response.status_code
                        }
                    else:
                        logger.error(f"TMDB API returned HTTP {response.status_code}")
                        return {
                            "service": service,
                            "status": "error",
                            "message": f"TMDB API returned HTTP {response.status_code}. Please try again later.",
                            "http_status": response.status_code
                        }

            except ValueError as e:
                # Invalid credential format
                logger.warning(f"Invalid TMDB credential format: {e}")
                return {
                    "service": service,
                    "status": "error",
                    "message": str(e)
                }
            except httpx.TimeoutException:
                logger.error("TMDB API request timeout")
                return {
                    "service": service,
                    "status": "error",
                    "message": "TMDB API request timeout. Please check your network connection."
                }
            except httpx.ConnectError:
                logger.error("Could not connect to TMDB API")
                return {
                    "service": service,
                    "status": "error",
                    "message": "Could not connect to TMDB API. Please check your network connection."
                }
            except Exception as e:
                logger.error(f"Unexpected error testing TMDB connection: {e}")
                return {
                    "service": service,
                    "status": "error",
                    "message": f"Unexpected error: {str(e)}"
                }
        elif service == "tracker":
            # Use form values if provided, otherwise fall back to database
            test_tracker_url = tracker_url if tracker_url else settings.tracker_url
            test_passkey = passkey if passkey else settings.tracker_passkey

            if not test_tracker_url:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Please configure Tracker URL first"
                }

            if not test_passkey:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Please configure Tracker Passkey first"
                }

            try:
                # Test tracker connection using /api/external/meta endpoint
                # This endpoint returns categories and tags, validating both connection and passkey
                meta_url = f"{test_tracker_url.rstrip('/')}/api/external/meta"

                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(
                        meta_url,
                        params={"passkey": test_passkey}
                    )

                    if response.status_code == 200:
                        # Try to parse response and sync metadata
                        try:
                            data = response.json()
                            categories_count = len(data.get("categories", []))
                            tag_groups_count = len(data.get("tagGroups", []))

                            # Sync metadata to database
                            logger.info("Syncing tracker metadata to database...")
                            from app.services.tracker_sync_service import sync_tracker_metadata
                            sync_result = await sync_tracker_metadata(db)

                            if sync_result.get('success'):
                                logger.info(f"✓ Tracker metadata synced: {sync_result['categories_synced']} categories, {sync_result['tags_synced']} tags")
                                return {
                                    "service": service,
                                    "status": "success",
                                    "message": f"Connected and synced! {sync_result['categories_synced']} categories, {sync_result['tags_synced']} tags saved.",
                                    "tracker_url": test_tracker_url,
                                    "categories_synced": sync_result['categories_synced'],
                                    "tags_synced": sync_result['tags_synced']
                                }
                            else:
                                # Connection OK but sync failed
                                logger.warning(f"Tracker connected but sync failed: {sync_result.get('message')}")
                                return {
                                    "service": service,
                                    "status": "success",
                                    "message": f"Connected (found {categories_count} categories) but sync failed: {sync_result.get('message', 'Unknown')}",
                                    "tracker_url": test_tracker_url
                                }
                        except Exception as e:
                            logger.warning(f"Tracker connected but metadata sync failed: {e}")
                            return {
                                "service": service,
                                "status": "success",
                                "message": f"Connected to tracker (sync failed: {str(e)[:50]})",
                                "tracker_url": test_tracker_url
                            }
                    elif response.status_code == 401:
                        return {
                            "service": service,
                            "status": "error",
                            "message": "Invalid passkey. Please check your tracker passkey.",
                            "tracker_url": test_tracker_url
                        }
                    elif response.status_code == 403:
                        return {
                            "service": service,
                            "status": "error",
                            "message": "Access denied. Your passkey may be invalid or your account may be restricted.",
                            "tracker_url": test_tracker_url
                        }
                    else:
                        return {
                            "service": service,
                            "status": "error",
                            "message": f"Tracker returned HTTP {response.status_code}",
                            "tracker_url": test_tracker_url
                        }

            except httpx.TimeoutException:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Connection timeout. The tracker may be slow or unreachable."
                }
            except httpx.ConnectError:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Cannot connect to tracker. Check URL and network."
                }
            except Exception as e:
                logger.error(f"Tracker connection test error: {e}")
                return {
                    "service": service,
                    "status": "error",
                    "message": f"Connection error: {str(e)}"
                }
        elif service == "prowlarr":
            # Use form values if provided, otherwise fall back to database
            prowlarr_url = url if url else settings.prowlarr_url
            prowlarr_api_key = api_key if api_key else settings.prowlarr_api_key

            if not prowlarr_url:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Please configure Prowlarr URL first"
                }

            if not prowlarr_api_key:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Please configure Prowlarr API key first"
                }

            try:
                from app.services.prowlarr_client import ProwlarrClient

                client = ProwlarrClient(
                    base_url=prowlarr_url,
                    api_key=prowlarr_api_key
                )

                health = await client.health_check()

                if health.get('healthy'):
                    # Get indexer count
                    try:
                        indexers = await client.get_indexers()
                        indexer_count = len(indexers)
                        enabled_count = len([i for i in indexers if i.get('enable')])

                        logger.info(f"Prowlarr connection test successful: {indexer_count} indexers")
                        return {
                            "service": service,
                            "status": "success",
                            "message": f"Connected to Prowlarr v{health.get('version')} ({enabled_count}/{indexer_count} indexers enabled)",
                            "url": prowlarr_url,
                            "version": health.get('version'),
                            "indexer_count": indexer_count,
                            "enabled_count": enabled_count
                        }
                    except Exception as e:
                        logger.warning(f"Connected to Prowlarr but couldn't fetch indexers: {e}")
                        return {
                            "service": service,
                            "status": "success",
                            "message": f"Connected to Prowlarr v{health.get('version')}",
                            "url": prowlarr_url,
                            "version": health.get('version')
                        }
                else:
                    return {
                        "service": service,
                        "status": "error",
                        "message": health.get('error', 'Connection failed'),
                        "url": prowlarr_url
                    }

            except httpx.TimeoutException:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Connection timeout. Check if Prowlarr is running."
                }
            except httpx.ConnectError:
                return {
                    "service": service,
                    "status": "error",
                    "message": "Cannot connect to Prowlarr. Check URL and if service is running."
                }
            except Exception as e:
                logger.error(f"Prowlarr connection test error: {e}")
                return {
                    "service": service,
                    "status": "error",
                    "message": f"Connection error: {str(e)}"
                }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown service: {service}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error testing {service} connection: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error testing connection: {str(e)}"
        )


# ============================================================================
# Settings Form Submission Handler for HTMX - Returns HTML Response
# ============================================================================

# Note: sanitize_path is now imported from app.models.validators


@router.put("/api/settings/save", response_class=HTMLResponse)
async def save_settings_html(
    request: Request,
    tracker_url: Optional[str] = Form(None),
    tracker_passkey: Optional[str] = Form(None),
    flaresolverr_url: Optional[str] = Form(None),
    qbittorrent_host: Optional[str] = Form(None),
    qbittorrent_username: Optional[str] = Form(None),
    qbittorrent_password: Optional[str] = Form(None),
    tmdb_api_key: Optional[str] = Form(None),
    prowlarr_url: Optional[str] = Form(None),
    prowlarr_api_key: Optional[str] = Form(None),
    input_media_path: Optional[str] = Form(None),
    output_dir: Optional[str] = Form(None),
    log_level: Optional[str] = Form(None),
    tmdb_cache_ttl_days: Optional[int] = Form(None),
    tag_sync_interval_hours: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Save settings from form submission.

    HTML-returning version of the PUT /api/settings endpoint for HTMX integration.
    Returns HTML alert message instead of JSON.

    Args:
        request: FastAPI request object
        All form fields as optional parameters
        db: Database session

    Returns:
        HTML fragment with success or error message
    """
    try:
        # Sanitize path inputs to remove invisible Unicode characters
        input_media_path = sanitize_path(input_media_path)
        output_dir = sanitize_path(output_dir)

        # Build update data from form fields
        update_data = {}

        if tracker_url is not None:
            update_data['tracker_url'] = tracker_url
        if tracker_passkey is not None:
            update_data['tracker_passkey'] = tracker_passkey
        if flaresolverr_url is not None:
            update_data['flaresolverr_url'] = flaresolverr_url
        if qbittorrent_host is not None:
            update_data['qbittorrent_host'] = qbittorrent_host
        if qbittorrent_username is not None:
            update_data['qbittorrent_username'] = qbittorrent_username
        if qbittorrent_password is not None:
            update_data['qbittorrent_password'] = qbittorrent_password
        if tmdb_api_key is not None:
            update_data['tmdb_api_key'] = tmdb_api_key
        if prowlarr_url is not None:
            update_data['prowlarr_url'] = prowlarr_url
        if prowlarr_api_key is not None:
            update_data['prowlarr_api_key'] = prowlarr_api_key
        if input_media_path is not None:
            update_data['input_media_path'] = input_media_path
        if output_dir is not None:
            update_data['output_dir'] = output_dir
        if log_level is not None:
            update_data['log_level'] = log_level
        if tmdb_cache_ttl_days is not None:
            update_data['tmdb_cache_ttl_days'] = tmdb_cache_ttl_days
        if tag_sync_interval_hours is not None:
            update_data['tag_sync_interval_hours'] = tag_sync_interval_hours

        # Validate log level if provided
        if 'log_level' in update_data:
            if update_data['log_level'] not in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
                return """
                <div class="alert alert-error">
                    <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path>
                    </svg>
                    <span>Invalid log level. Must be DEBUG, INFO, WARNING, or ERROR</span>
                </div>
                """

        # Validate numeric fields
        if 'tmdb_cache_ttl_days' in update_data and update_data['tmdb_cache_ttl_days'] < 1:
            return """
            <div class="alert alert-error">
                <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path>
                </svg>
                <span>TMDB cache TTL must be at least 1 day</span>
            </div>
            """

        if 'tag_sync_interval_hours' in update_data and update_data['tag_sync_interval_hours'] < 1:
            return """
            <div class="alert alert-error">
                <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path>
                </svg>
                <span>Tag sync interval must be at least 1 hour</span>
            </div>
            """

        # Update settings in database
        settings = Settings.update_settings(db, **update_data)

        logger.info(f"Settings saved via form: {list(update_data.keys())}")

        return """
        <div class="alert alert-success">
            <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path>
            </svg>
            <span>Settings saved successfully!</span>
        </div>
        """

    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        return f"""
        <div class="alert alert-error">
            <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path>
            </svg>
            <span>Error saving settings: {str(e)}</span>
        </div>
        """


# ============================================================================
# Configuration Endpoints - Export and download configuration data
# ============================================================================


@router.get("/api/configuration/export")
async def export_configuration(db: Session = Depends(get_db)):
    """
    Export current application configuration as a text file.

    Returns configuration data including settings, environment info, and
    system configuration in a formatted text format suitable for download.

    Args:
        db: Database session

    Returns:
        Text file content with configuration data
    """
    try:
        settings = Settings.get_settings(db)
        settings_dict = settings.to_dict(mask_secrets=True)

        # Build configuration export
        config_export = "# Seedarr v2.0 - Configuration Export\n"
        config_export += f"# Generated: {datetime.now(timezone.utc).isoformat()}\n\n"

        # Settings section
        config_export += "## Application Settings\n"
        config_export += "---\n"

        if settings_dict.get('tracker_url'):
            config_export += f"Tracker URL: {settings_dict['tracker_url']}\n"
        if settings_dict.get('announce_url'):
            config_export += f"Announce URL: {settings_dict['announce_url']}\n"
        if settings_dict.get('flaresolverr_url'):
            config_export += f"FlareSolverr URL: {settings_dict['flaresolverr_url']}\n"
        if settings_dict.get('qbittorrent_host'):
            config_export += f"qBittorrent Host: {settings_dict['qbittorrent_host']}\n"
        if settings_dict.get('input_media_path'):
            config_export += f"Input Media Path: {settings_dict['input_media_path']}\n"
        if settings_dict.get('output_dir'):
            config_export += f"Output Directory: {settings_dict['output_dir']}\n"
        if settings_dict.get('log_level'):
            config_export += f"Log Level: {settings_dict['log_level']}\n"
        if settings_dict.get('tmdb_cache_ttl_days'):
            config_export += f"TMDB Cache TTL: {settings_dict['tmdb_cache_ttl_days']} days\n"
        if settings_dict.get('tag_sync_interval_hours'):
            config_export += f"Tag Sync Interval: {settings_dict['tag_sync_interval_hours']} hours\n"

        config_export += f"\nLast Updated: {settings_dict.get('updated_at', 'Unknown')}\n"
        config_export += f"Created: {settings_dict.get('created_at', 'Unknown')}\n"

        logger.info("Configuration exported successfully")

        return {
            "status": "success",
            "content": config_export,
            "filename": "app-configuration.txt"
        }

    except Exception as e:
        logger.error(f"Error exporting configuration: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error exporting configuration: {str(e)}"
        )


# ============================================================================
# Tag Diagnostics and Sync Endpoints
# ============================================================================


@router.get("/api/tags/list")
async def list_tags(db: Session = Depends(get_db)):
    """
    List all tags currently stored in the database.

    Returns a list of all tags with their IDs, labels, and groups.
    Useful for debugging tag mapping issues.

    Args:
        db: Database session

    Returns:
        JSON object with tags list and count
    """
    from app.models.tags import Tags

    try:
        all_tags = Tags.get_all(db)

        # Group tags by category/group for easier reading
        tags_by_group = {}
        for tag in all_tags:
            group = tag.group or "Ungrouped"
            if group not in tags_by_group:
                tags_by_group[group] = []
            tags_by_group[group].append({
                "tag_id": tag.tag_id,
                "label": tag.label,
                "description": tag.description
            })

        # Also return flat list
        all_tags_list = [
            {
                "tag_id": tag.tag_id,
                "label": tag.label,
                "group": tag.group,
                "description": tag.description
            }
            for tag in all_tags
        ]

        return {
            "status": "success",
            "total_count": len(all_tags),
            "tags_by_group": tags_by_group,
            "all_tags": all_tags_list
        }

    except Exception as e:
        logger.error(f"Error listing tags: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error listing tags: {str(e)}"
        )


@router.post("/api/tags/sync")
async def sync_tags(db: Session = Depends(get_db)):
    """
    Fetch and sync tags and categories from the tracker API.

    Triggers a fresh metadata sync from the tracker's External API.
    This will update the local database with the latest tags and categories.

    Args:
        db: Database session

    Returns:
        JSON object with sync result
    """
    from app.services.tracker_sync_service import sync_tracker_metadata

    try:
        result = await sync_tracker_metadata(db)

        if result.get('success'):
            return {
                "status": "success",
                "message": f"Synced {result['categories_synced']} categories and {result['tags_synced']} tags",
                "categories_synced": result['categories_synced'],
                "tags_synced": result['tags_synced']
            }
        else:
            return {
                "status": "error",
                "message": result.get('message', 'Sync failed. Check logs for details.')
            }

    except Exception as e:
        logger.error(f"Error syncing metadata: {e}")
        return {
            "status": "error",
            "message": f"Error syncing metadata: {str(e)}"
        }


@router.get("/api/tags/debug-tracker")
async def debug_tracker_tags(db: Session = Depends(get_db)):
    """
    Fetch raw tag data from tracker API for debugging.

    This endpoint directly fetches the metadata from the tracker API
    and returns it without processing, useful for debugging what the
    tracker actually returns.

    Args:
        db: Database session

    Returns:
        Raw JSON response from tracker API
    """
    try:
        settings = Settings.get_settings(db)

        if not settings.tracker_url or not settings.tracker_passkey:
            return {
                "status": "error",
                "message": "Tracker URL and passkey must be configured first"
            }

        # Direct API call to tracker
        meta_url = f"{settings.tracker_url.rstrip('/')}/api/external/meta"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                meta_url,
                params={"passkey": settings.tracker_passkey}
            )

            if response.status_code == 200:
                data = response.json()

                # Analyze the response
                categories = data.get("categories", [])
                tag_groups = data.get("tagGroups", [])
                standalone_tags = data.get("tags", [])

                # Count tags per group
                group_summary = []
                for group in tag_groups:
                    if group:
                        group_name = group.get("name", "Unknown")
                        tags_in_group = len(group.get("tags", []))
                        group_summary.append({
                            "name": group_name,
                            "tag_count": tags_in_group,
                            "tags": [t.get("name") for t in group.get("tags", []) if t][:10]  # First 10 tags
                        })

                return {
                    "status": "success",
                    "summary": {
                        "categories_count": len(categories),
                        "tag_groups_count": len(tag_groups),
                        "standalone_tags_count": len(standalone_tags),
                        "categories": [{"id": c.get("id"), "name": c.get("name")} for c in categories if c],
                        "tag_groups": group_summary
                    },
                    "raw_response": data
                }
            else:
                return {
                    "status": "error",
                    "message": f"Tracker returned HTTP {response.status_code}",
                    "response_text": response.text[:500]
                }

    except Exception as e:
        logger.error(f"Error debugging tracker tags: {e}")
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }
