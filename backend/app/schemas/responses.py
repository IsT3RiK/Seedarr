"""
API Response Schemas

Pydantic models for standardized API responses.
Used for OpenAPI documentation and response validation.
"""

from typing import Optional, List, Dict, Any, Generic, TypeVar
from pydantic import BaseModel, Field
from datetime import datetime

T = TypeVar('T')


# ============================================================================
# Base Response Models
# ============================================================================

class SuccessResponse(BaseModel):
    """Standard success response."""
    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Success message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "success": True,
                "message": "Operation completed successfully"
            }
        }
    }


class ErrorResponse(BaseModel):
    """Standard error response."""
    success: bool = Field(False, description="Operation success status")
    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Detailed error information")
    error_code: Optional[str] = Field(None, description="Error code for programmatic handling")

    model_config = {
        "json_schema_extra": {
            "example": {
                "success": False,
                "error": "Resource not found",
                "detail": "File entry with ID 123 does not exist",
                "error_code": "NOT_FOUND"
            }
        }
    }


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response."""
    items: List[T] = Field(..., description="List of items")
    total: int = Field(..., description="Total number of items")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Items per page")
    total_pages: int = Field(..., description="Total number of pages")


# ============================================================================
# Health Check Responses
# ============================================================================

class ServiceHealthResponse(BaseModel):
    """Health status for a single service."""
    service: str = Field(..., description="Service name")
    status: str = Field(..., description="Health status (healthy, unhealthy, degraded)")
    message: Optional[str] = Field(None, description="Status message")
    latency_ms: Optional[float] = Field(None, description="Response latency in milliseconds")
    version: Optional[str] = Field(None, description="Service version if available")
    last_checked: Optional[str] = Field(None, description="Last check timestamp")

    model_config = {
        "json_schema_extra": {
            "example": {
                "service": "database",
                "status": "healthy",
                "message": "Database connection successful",
                "latency_ms": 5.23,
                "version": None,
                "last_checked": "2026-01-24T12:00:00Z"
            }
        }
    }


class HealthResponse(BaseModel):
    """Overall health check response."""
    status: str = Field(..., description="Overall status (healthy, unhealthy, degraded)")
    services: Dict[str, ServiceHealthResponse] = Field(..., description="Individual service statuses")
    timestamp: str = Field(..., description="Check timestamp")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "healthy",
                "services": {
                    "database": {
                        "service": "database",
                        "status": "healthy",
                        "message": "Connected",
                        "latency_ms": 2.5
                    },
                    "flaresolverr": {
                        "service": "flaresolverr",
                        "status": "healthy",
                        "message": "Service running",
                        "latency_ms": 150.0
                    }
                },
                "timestamp": "2026-01-24T12:00:00Z"
            }
        }
    }


# ============================================================================
# File Entry Responses
# ============================================================================

class FileEntryResponse(BaseModel):
    """Response model for a file entry."""
    id: int = Field(..., description="Unique identifier")
    file_path: str = Field(..., description="Path to the media file")
    status: str = Field(..., description="Processing status")
    media_type: Optional[str] = Field(None, description="Type: movie, tv, anime")
    original_name: Optional[str] = Field(None, description="Original filename")
    formatted_name: Optional[str] = Field(None, description="Formatted release name")
    tmdb_id: Optional[int] = Field(None, description="TMDB ID if matched")
    imdb_id: Optional[str] = Field(None, description="IMDB ID if available")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    updated_at: Optional[str] = Field(None, description="Last update timestamp")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": 1,
                "file_path": "/media/movies/Movie.Title.2024.1080p.BluRay.x264-GROUP.mkv",
                "status": "analyzed",
                "media_type": "movie",
                "original_name": "Movie.Title.2024.1080p.BluRay.x264-GROUP.mkv",
                "formatted_name": "Movie Title (2024) 1080p BluRay x264-GROUP",
                "tmdb_id": 12345,
                "imdb_id": "tt1234567",
                "error_message": None,
                "created_at": "2026-01-24T10:00:00Z",
                "updated_at": "2026-01-24T10:05:00Z"
            }
        }
    }


class FileEntryListResponse(BaseModel):
    """Response model for file entry list."""
    entries: List[FileEntryResponse] = Field(..., description="List of file entries")
    total: int = Field(..., description="Total count")


# ============================================================================
# Batch Processing Responses
# ============================================================================

class BatchResponse(BaseModel):
    """Response model for batch operations."""
    id: int = Field(..., description="Batch job ID")
    name: Optional[str] = Field(None, description="Batch name")
    status: str = Field(..., description="Batch status")
    total_count: int = Field(..., description="Total files in batch")
    processed_count: int = Field(..., description="Files processed")
    success_count: int = Field(..., description="Successful uploads")
    failed_count: int = Field(..., description="Failed uploads")
    progress_percent: float = Field(..., description="Progress percentage")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    started_at: Optional[str] = Field(None, description="Start timestamp")
    completed_at: Optional[str] = Field(None, description="Completion timestamp")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": 1,
                "name": "Batch 20260124_120000",
                "status": "processing",
                "total_count": 10,
                "processed_count": 5,
                "success_count": 4,
                "failed_count": 1,
                "progress_percent": 50.0,
                "created_at": "2026-01-24T12:00:00Z",
                "started_at": "2026-01-24T12:00:01Z",
                "completed_at": None
            }
        }
    }


class BatchListResponse(BaseModel):
    """Response model for batch list."""
    batches: List[BatchResponse] = Field(..., description="List of batches")
    count: int = Field(..., description="Total count")


# ============================================================================
# Statistics Responses
# ============================================================================

class StatisticsSummaryResponse(BaseModel):
    """Response model for statistics summary."""
    period_days: int = Field(..., description="Number of days in period")
    total_uploads: int = Field(..., description="Total upload attempts")
    successful_uploads: int = Field(..., description="Successful uploads")
    failed_uploads: int = Field(..., description="Failed uploads")
    success_rate: float = Field(..., description="Success rate percentage")
    avg_processing_time: Optional[float] = Field(None, description="Average processing time in seconds")
    total_bytes_processed: int = Field(..., description="Total bytes processed")

    model_config = {
        "json_schema_extra": {
            "example": {
                "period_days": 30,
                "total_uploads": 150,
                "successful_uploads": 142,
                "failed_uploads": 8,
                "success_rate": 94.7,
                "avg_processing_time": 45.3,
                "total_bytes_processed": 5368709120
            }
        }
    }


class TrackerStatisticsResponse(BaseModel):
    """Response model for per-tracker statistics."""
    tracker_name: str = Field(..., description="Tracker name")
    total_uploads: int = Field(..., description="Total uploads")
    successful_uploads: int = Field(..., description="Successful uploads")
    failed_uploads: int = Field(..., description="Failed uploads")
    success_rate: float = Field(..., description="Success rate percentage")
    avg_processing_time: Optional[float] = Field(None, description="Average processing time")

    model_config = {
        "json_schema_extra": {
            "example": {
                "tracker_name": "La Cale",
                "total_uploads": 100,
                "successful_uploads": 95,
                "failed_uploads": 5,
                "success_rate": 95.0,
                "avg_processing_time": 42.5
            }
        }
    }


# ============================================================================
# Settings Responses
# ============================================================================

class SettingsResponse(BaseModel):
    """Response model for application settings."""
    id: int
    tracker_url: Optional[str] = Field(None, description="Tracker base URL")
    tracker_passkey: Optional[str] = Field(None, description="Tracker passkey (masked)")
    announce_url: Optional[str] = Field(None, description="Computed announce URL")
    flaresolverr_url: Optional[str] = Field(None, description="FlareSolverr URL")
    qbittorrent_host: Optional[str] = Field(None, description="qBittorrent host:port")
    qbittorrent_username: Optional[str] = Field(None, description="qBittorrent username")
    qbittorrent_password: Optional[str] = Field(None, description="qBittorrent password (masked)")
    tmdb_api_key: Optional[str] = Field(None, description="TMDB API key (masked)")
    prowlarr_url: Optional[str] = Field(None, description="Prowlarr URL")
    prowlarr_api_key: Optional[str] = Field(None, description="Prowlarr API key (masked)")
    input_media_path: Optional[str] = Field(None, description="Input media directory")
    output_dir: Optional[str] = Field(None, description="Output directory")
    log_level: Optional[str] = Field(None, description="Log level")
    tmdb_cache_ttl_days: Optional[int] = Field(None, description="TMDB cache TTL in days")
    tag_sync_interval_hours: Optional[int] = Field(None, description="Tag sync interval in hours")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    updated_at: Optional[str] = Field(None, description="Last update timestamp")


class SettingsExportResponse(BaseModel):
    """Response model for settings export."""
    version: str = Field(..., description="Export format version")
    exported_at: str = Field(..., description="Export timestamp")
    settings: Dict[str, Any] = Field(..., description="Settings data")


class SettingsImportResponse(BaseModel):
    """Response model for settings import."""
    success: bool = Field(..., description="Import success status")
    message: str = Field(..., description="Result message")
    imported_count: int = Field(..., description="Number of fields imported")
    imported_fields: List[str] = Field(..., description="List of imported field names")


# ============================================================================
# Connection Test Responses
# ============================================================================

class ConnectionTestResponse(BaseModel):
    """Response model for connection test."""
    service: str = Field(..., description="Service name tested")
    status: str = Field(..., description="Test result: success, error, warning")
    message: str = Field(..., description="Result message")
    url: Optional[str] = Field(None, description="Service URL tested")
    version: Optional[str] = Field(None, description="Service version if available")

    model_config = {
        "json_schema_extra": {
            "example": {
                "service": "flaresolverr",
                "status": "success",
                "message": "FlareSolverr is running and healthy",
                "url": "http://localhost:8191",
                "version": None
            }
        }
    }


# ============================================================================
# Notification Responses
# ============================================================================

class NotificationResponse(BaseModel):
    """Response model for notification."""
    id: int = Field(..., description="Notification ID")
    channel: str = Field(..., description="Notification channel")
    event_type: str = Field(..., description="Event type")
    success: bool = Field(..., description="Delivery success")
    message: Optional[str] = Field(None, description="Notification message")
    error_message: Optional[str] = Field(None, description="Error if failed")
    created_at: str = Field(..., description="Creation timestamp")


# ============================================================================
# Queue Responses
# ============================================================================

class QueueItemResponse(BaseModel):
    """Response model for queue item."""
    id: int = Field(..., description="Queue item ID")
    file_entry_id: int = Field(..., description="Associated file entry ID")
    status: str = Field(..., description="Queue status")
    priority: str = Field(..., description="Processing priority")
    attempts: int = Field(..., description="Number of attempts")
    max_attempts: int = Field(..., description="Maximum attempts allowed")
    added_at: str = Field(..., description="When added to queue")
    started_at: Optional[str] = Field(None, description="Processing start time")
    error_message: Optional[str] = Field(None, description="Error if failed")


class QueueStatusResponse(BaseModel):
    """Response model for queue status."""
    pending: int = Field(..., description="Pending items")
    processing: int = Field(..., description="Currently processing")
    completed: int = Field(..., description="Completed items")
    failed: int = Field(..., description="Failed items")
    total: int = Field(..., description="Total items")
