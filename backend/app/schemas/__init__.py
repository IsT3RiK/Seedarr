"""
API Schemas Package

Contains Pydantic models for API requests and responses.
These schemas are used for OpenAPI documentation and validation.
"""

from app.schemas.responses import (
    SuccessResponse,
    ErrorResponse,
    PaginatedResponse,
    HealthResponse,
    ServiceHealthResponse,
)

from app.schemas.requests import (
    FileEntryCreate,
    FileEntryUpdate,
    BatchCreateRequest,
    SettingsUpdateRequest,
)

__all__ = [
    # Responses
    'SuccessResponse',
    'ErrorResponse',
    'PaginatedResponse',
    'HealthResponse',
    'ServiceHealthResponse',
    # Requests
    'FileEntryCreate',
    'FileEntryUpdate',
    'BatchCreateRequest',
    'SettingsUpdateRequest',
]
