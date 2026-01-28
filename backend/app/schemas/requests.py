"""
API Request Schemas

Pydantic models for API requests.
Used for OpenAPI documentation and request validation.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


# ============================================================================
# File Entry Requests
# ============================================================================

class FileEntryCreate(BaseModel):
    """Request model for creating a file entry."""
    file_path: str = Field(
        ...,
        description="Path to the media file",
        min_length=1,
        max_length=1024,
        examples=["/media/movies/Movie.Title.2024.1080p.BluRay.x264-GROUP.mkv"]
    )
    media_type: Optional[str] = Field(
        None,
        description="Media type (movie, tv, anime). Auto-detected if not provided.",
        pattern="^(movie|tv|anime)$",
        examples=["movie"]
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "file_path": "/media/movies/Movie.Title.2024.1080p.BluRay.x264-GROUP.mkv",
                "media_type": "movie"
            }
        }
    }


class FileEntryUpdate(BaseModel):
    """Request model for updating a file entry."""
    formatted_name: Optional[str] = Field(
        None,
        description="Override the formatted release name",
        max_length=500,
        examples=["Movie Title (2024) 1080p BluRay x264-GROUP"]
    )
    tmdb_id: Optional[int] = Field(
        None,
        description="Override the TMDB ID",
        ge=1,
        examples=[12345]
    )
    imdb_id: Optional[str] = Field(
        None,
        description="Override the IMDB ID",
        pattern="^tt\\d{7,}$",
        examples=["tt1234567"]
    )
    status: Optional[str] = Field(
        None,
        description="Manually set status",
        pattern="^(pending|scanned|analyzed|renamed|metadata_generated|uploaded|failed)$"
    )


class FileEntryProcessRequest(BaseModel):
    """Request model for processing a file entry."""
    skip_approval: bool = Field(
        False,
        description="Skip the approval step and upload directly"
    )
    force_reprocess: bool = Field(
        False,
        description="Force reprocessing even if already processed"
    )


# ============================================================================
# Batch Processing Requests
# ============================================================================

class BatchCreateRequest(BaseModel):
    """Request model for creating a batch job."""
    file_entry_ids: List[int] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="List of file entry IDs to process"
    )
    name: Optional[str] = Field(
        None,
        max_length=255,
        description="Optional batch name for identification"
    )
    priority: str = Field(
        "normal",
        pattern="^(high|normal|low)$",
        description="Processing priority"
    )
    skip_approval: bool = Field(
        False,
        description="Skip approval step for all files"
    )
    max_concurrent: int = Field(
        2,
        ge=1,
        le=10,
        description="Maximum concurrent file processing"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "file_entry_ids": [1, 2, 3, 4, 5],
                "name": "Weekend Upload Batch",
                "priority": "normal",
                "skip_approval": False,
                "max_concurrent": 2
            }
        }
    }


# ============================================================================
# Settings Requests
# ============================================================================

class SettingsUpdateRequest(BaseModel):
    """Request model for updating application settings."""
    tracker_url: Optional[str] = Field(
        None,
        description="Tracker base URL (must be http/https)",
        examples=["https://tracker.example.com"]
    )
    tracker_passkey: Optional[str] = Field(
        None,
        min_length=16,
        description="Tracker passkey for authentication"
    )
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
    qbittorrent_username: Optional[str] = Field(
        None,
        description="qBittorrent username"
    )
    qbittorrent_password: Optional[str] = Field(
        None,
        description="qBittorrent password"
    )
    tmdb_api_key: Optional[str] = Field(
        None,
        min_length=16,
        description="TMDB API key or Bearer token"
    )
    prowlarr_url: Optional[str] = Field(
        None,
        description="Prowlarr instance URL",
        examples=["http://localhost:9696"]
    )
    prowlarr_api_key: Optional[str] = Field(
        None,
        min_length=16,
        description="Prowlarr API key"
    )
    input_media_path: Optional[str] = Field(
        None,
        description="Input media directory path"
    )
    output_dir: Optional[str] = Field(
        None,
        description="Output directory for generated files"
    )
    log_level: Optional[str] = Field(
        None,
        pattern="^(DEBUG|INFO|WARNING|ERROR)$",
        description="Application log level"
    )
    tmdb_cache_ttl_days: Optional[int] = Field(
        None,
        ge=1,
        le=365,
        description="TMDB cache TTL in days"
    )
    tag_sync_interval_hours: Optional[int] = Field(
        None,
        ge=1,
        le=168,
        description="Tag sync interval in hours"
    )

    @field_validator('tracker_url', 'flaresolverr_url', 'prowlarr_url', mode='before')
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        """Validate URL format."""
        if v and not (v.startswith('http://') or v.startswith('https://')):
            raise ValueError('URL must start with http:// or https://')
        return v

    model_config = {
        "json_schema_extra": {
            "example": {
                "tracker_url": "https://tracker.example.com",
                "flaresolverr_url": "http://localhost:8191",
                "qbittorrent_host": "localhost:8080",
                "log_level": "INFO",
                "tmdb_cache_ttl_days": 30
            }
        }
    }


# ============================================================================
# Tracker Requests
# ============================================================================

class TrackerCreateRequest(BaseModel):
    """Request model for creating a tracker configuration."""
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Tracker display name"
    )
    tracker_type: str = Field(
        ...,
        pattern="^(lacale|c411|generic)$",
        description="Tracker type for adapter selection"
    )
    base_url: str = Field(
        ...,
        description="Tracker base URL"
    )
    passkey: str = Field(
        ...,
        min_length=16,
        description="Authentication passkey"
    )
    enabled: bool = Field(
        True,
        description="Whether tracker is enabled for uploads"
    )
    priority: int = Field(
        1,
        ge=1,
        le=100,
        description="Upload priority (lower = higher priority)"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "My Tracker",
                "tracker_type": "lacale",
                "base_url": "https://tracker.example.com",
                "passkey": "abc123def456ghi789",
                "enabled": True,
                "priority": 1
            }
        }
    }


class TrackerUpdateRequest(BaseModel):
    """Request model for updating a tracker configuration."""
    name: Optional[str] = Field(None, max_length=100)
    base_url: Optional[str] = Field(None)
    passkey: Optional[str] = Field(None, min_length=16)
    enabled: Optional[bool] = Field(None)
    priority: Optional[int] = Field(None, ge=1, le=100)


# ============================================================================
# Notification Requests
# ============================================================================

class NotificationTestRequest(BaseModel):
    """Request model for testing notifications."""
    channel: str = Field(
        ...,
        pattern="^(discord|email)$",
        description="Notification channel to test"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "channel": "discord"
            }
        }
    }


# ============================================================================
# Search Requests
# ============================================================================

class DuplicateCheckRequest(BaseModel):
    """Request model for duplicate checking."""
    tmdb_id: Optional[int] = Field(
        None,
        description="TMDB ID to search for"
    )
    imdb_id: Optional[str] = Field(
        None,
        pattern="^tt\\d{7,}$",
        description="IMDB ID to search for"
    )
    release_name: Optional[str] = Field(
        None,
        min_length=3,
        description="Release name to search for"
    )
    trackers: Optional[List[str]] = Field(
        None,
        description="Specific trackers to check (default: all enabled)"
    )

    @field_validator('release_name', 'tmdb_id', 'imdb_id', mode='after')
    @classmethod
    def check_at_least_one(cls, v, info):
        """Ensure at least one search parameter is provided."""
        # This is validated at the endpoint level
        return v

    model_config = {
        "json_schema_extra": {
            "example": {
                "tmdb_id": 12345,
                "imdb_id": "tt1234567",
                "release_name": None,
                "trackers": ["La Cale", "C411"]
            }
        }
    }
