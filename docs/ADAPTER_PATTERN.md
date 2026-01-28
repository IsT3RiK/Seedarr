# Tracker Adapter Pattern - Implementation Guide

## Table of Contents

- [Overview](#overview)
- [TrackerAdapter Interface](#trackeradapter-interface)
- [Implementing a New Adapter](#implementing-a-new-adapter)
- [La Cale Adapter Reference](#la-cale-adapter-reference)
- [Critical Implementation Patterns](#critical-implementation-patterns)
- [Testing Your Adapter](#testing-your-adapter)
- [Configuration and Deployment](#configuration-and-deployment)
- [Troubleshooting](#troubleshooting)

## Overview

The **Tracker Adapter Pattern** enables Seedarr v2.0 to support multiple torrent trackers through a common interface. The pipeline depends only on the `TrackerAdapter` abstract base class, making it trivial to add new tracker support.

### Architecture

```
┌─────────────────────────────────────────────┐
│         ProcessingPipeline                  │
│                                             │
│  Depends on TrackerAdapter interface ONLY  │
└─────────────┬───────────────────────────────┘
              │
              │ Uses interface methods:
              │ - authenticate()
              │ - upload_torrent()
              │ - validate_credentials()
              │ - get_tags()
              │ - get_categories()
              │ - health_check()
              │
              ▼
    ┌─────────────────────┐
    │  TrackerAdapter     │  ◄─── Abstract Base Class (ABC)
    │  (Interface)        │
    └─────────────────────┘
              ▲
              │
              │ Implements interface
              │
    ┌─────────┴──────────┬──────────────────┐
    │                    │                  │
    ▼                    ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ LaCaleAdapter│  │ OtherAdapter │  │ YourAdapter  │
│ (Concrete)   │  │ (Concrete)   │  │ (Concrete)   │
└──────────────┘  └──────────────┘  └──────────────┘
```

### Benefits

1. **Tracker-Agnostic Pipeline**: Pipeline code doesn't know or care which tracker is being used
2. **Easy Testing**: Mock adapters for unit tests
3. **Configuration-Driven**: Adapter selection via database setting
4. **Isolated Logic**: Each tracker's quirks isolated in its own adapter
5. **Maintainability**: Changes to one tracker don't affect others

## TrackerAdapter Interface

### Abstract Base Class

**File**: `backend/app/adapters/tracker_adapter.py`

```python
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from pathlib import Path

class TrackerAdapter(ABC):
    """
    Abstract base class defining the contract for tracker adapters.

    All tracker implementations must inherit from this class and implement
    all abstract methods.
    """

    @abstractmethod
    async def authenticate(self) -> bool:
        """
        Authenticate with the tracker and establish a session.

        Returns:
            True if authentication successful, False otherwise

        Raises:
            CloudflareBypassError: If Cloudflare bypass fails (retryable)
            TrackerAPIError: If authentication fails (non-retryable)
            NetworkRetryableError: If network issues occur (retryable)
        """
        pass

    @abstractmethod
    async def upload_torrent(
        self,
        torrent_data: bytes,
        release_name: str,
        category_id: str,
        tag_ids: List[str],
        description: Optional[str] = None,
        nfo_content: Optional[str] = None,
        mediainfo: Optional[str] = None,
        screenshots: Optional[List[Path]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Upload a .torrent file with metadata to the tracker.

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
            TrackerAPIError: If upload fails (non-retryable)
            NetworkRetryableError: If network issues occur (retryable)
        """
        pass

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """
        Validate tracker credentials without full authentication.

        Returns:
            True if credentials are valid, False otherwise
        """
        pass

    @abstractmethod
    async def get_tags(self) -> List[Dict[str, Any]]:
        """
        Fetch available tags from tracker API.

        Returns:
            List of tag dictionaries:
            [
                {
                    'tag_id': str,
                    'label': str,
                    'category': str,
                    'description': str
                },
                ...
            ]
        """
        pass

    @abstractmethod
    async def get_categories(self) -> List[Dict[str, Any]]:
        """
        Fetch available categories from tracker API.

        Returns:
            List of category dictionaries:
            [
                {
                    'category_id': str,
                    'name': str,
                    'description': str
                },
                ...
            ]
        """
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """
        Perform health check on tracker and dependencies.

        Returns:
            Dictionary with health status:
            {
                'healthy': bool,
                'tracker_api': bool,
                'authenticated': bool,
                'circuit_breaker': str,
                'last_error': str
            }
        """
        pass

    @abstractmethod
    def get_adapter_info(self) -> Dict[str, str]:
        """
        Get information about this tracker adapter.

        Returns:
            {
                'name': str,
                'tracker_name': str,
                'tracker_url': str,
                'version': str,
                'features': List[str]
            }
        """
        pass
```

### Method Requirements

| Method | Required | Async | Returns | Purpose |
|--------|----------|-------|---------|---------|
| `authenticate()` | ✓ | ✓ | `bool` | Establish authenticated session |
| `upload_torrent()` | ✓ | ✓ | `dict` | Upload torrent with metadata |
| `validate_credentials()` | ✓ | ✓ | `bool` | Verify credentials are valid |
| `get_tags()` | ✓ | ✓ | `List[dict]` | Fetch available tags |
| `get_categories()` | ✓ | ✓ | `List[dict]` | Fetch available categories |
| `health_check()` | ✓ | ✓ | `dict` | Check adapter health |
| `get_adapter_info()` | ✓ | ✗ | `dict` | Return adapter metadata |

## Implementing a New Adapter

### Step-by-Step Guide

#### Step 1: Create Adapter File

Create a new file: `backend/app/adapters/your_tracker_adapter.py`

```python
"""
YourTrackerAdapter - TrackerAdapter Implementation for [Tracker Name]

This module implements the TrackerAdapter interface for [Tracker Name].

Key Features:
    - [Authentication method]
    - [Upload format]
    - [Special requirements]
"""

import logging
from typing import Dict, List, Optional, Any
from pathlib import Path

from .tracker_adapter import TrackerAdapter
from ..services.exceptions import TrackerAPIError, NetworkRetryableError

logger = logging.getLogger(__name__)


class YourTrackerAdapter(TrackerAdapter):
    """
    [Tracker Name] tracker adapter implementing TrackerAdapter interface.

    Attributes:
        tracker_url: Tracker base URL
        api_key: User's API key for authentication
    """

    def __init__(self, tracker_url: str, api_key: str):
        """
        Initialize YourTrackerAdapter.

        Args:
            tracker_url: Tracker base URL
            api_key: User's API key
        """
        self.tracker_url = tracker_url
        self.api_key = api_key
        self.session = None

        logger.info(f"YourTrackerAdapter initialized for: {tracker_url}")

    # Implement all abstract methods here...
```

#### Step 2: Implement Authentication

```python
async def authenticate(self) -> bool:
    """
    Authenticate with [Tracker Name] and establish a session.

    Implementation depends on tracker authentication method:
    - API key in headers
    - Session cookies
    - OAuth tokens
    - Cloudflare bypass (see LaCaleAdapter for reference)

    Returns:
        True if authentication successful, False otherwise

    Raises:
        TrackerAPIError: If authentication fails
        NetworkRetryableError: If network issues occur
    """
    logger.info(f"Authenticating with {self.tracker_url}")

    try:
        # Example: API key authentication
        headers = {'Authorization': f'Bearer {self.api_key}'}

        response = await self._make_request(
            'GET',
            f'{self.tracker_url}/api/auth',
            headers=headers
        )

        if response.status_code == 200:
            self.session = response.cookies
            logger.info("Successfully authenticated")
            return True
        else:
            error_msg = f"Authentication failed: HTTP {response.status_code}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg, status_code=response.status_code)

    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise
```

#### Step 3: Implement Upload

```python
async def upload_torrent(
    self,
    torrent_data: bytes,
    release_name: str,
    category_id: str,
    tag_ids: List[str],
    description: Optional[str] = None,
    nfo_content: Optional[str] = None,
    mediainfo: Optional[str] = None,
    screenshots: Optional[List[Path]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Upload a .torrent file with metadata to [Tracker Name].

    CRITICAL: Review tracker's upload API documentation for:
    - Required vs optional fields
    - Field name conventions (camelCase vs snake_case)
    - Special formatting requirements (e.g., repeated fields for tags)
    - File upload format (multipart/form-data vs JSON)

    Returns:
        Upload result dictionary
    """
    logger.info(f"Uploading torrent: {release_name}")

    # Ensure authenticated
    if not self.session:
        await self.authenticate()

    try:
        # Prepare multipart form data
        files = {
            'torrent': (f'{release_name}.torrent', torrent_data, 'application/x-bittorrent')
        }

        data = {
            'name': release_name,
            'category': category_id,
            'description': description or '',
        }

        # Handle tags based on tracker API format
        # Example 1: JSON array (most trackers)
        data['tags'] = tag_ids

        # Example 2: Repeated fields (La Cale)
        # data = [
        #     ('name', release_name),
        #     ('category', category_id),
        # ]
        # for tag_id in tag_ids:
        #     data.append(('tags', tag_id))

        # Optional fields
        if nfo_content:
            files['nfo'] = ('nfo.txt', nfo_content.encode(), 'text/plain')
        if mediainfo:
            data['mediainfo'] = mediainfo

        # Upload
        response = await self._make_request(
            'POST',
            f'{self.tracker_url}/api/upload',
            files=files,
            data=data,
            cookies=self.session
        )

        # Parse response
        result = response.json()

        return {
            'success': True,
            'torrent_id': result['id'],
            'torrent_url': f"{self.tracker_url}/torrent/{result['id']}",
            'message': 'Upload successful',
            'response_data': result
        }

    except Exception as e:
        error_msg = f"Upload failed: {e}"
        logger.error(error_msg)
        raise TrackerAPIError(error_msg)
```

#### Step 4: Implement Credential Validation

```python
async def validate_credentials(self) -> bool:
    """
    Validate tracker credentials.

    Lightweight check to verify credentials are valid without
    performing full authentication.

    Returns:
        True if credentials are valid, False otherwise
    """
    # Basic format validation
    if not self.api_key or len(self.api_key) < 20:
        logger.warning("Invalid API key format")
        return False

    # Optional: Ping tracker API
    try:
        await self.authenticate()
        return True
    except TrackerAPIError:
        return False
```

#### Step 5: Implement Tag and Category Fetching

```python
async def get_tags(self) -> List[Dict[str, Any]]:
    """Fetch available tags from tracker."""
    logger.info("Fetching tags")

    if not self.session:
        await self.authenticate()

    response = await self._make_request(
        'GET',
        f'{self.tracker_url}/api/tags',
        cookies=self.session
    )

    tags = response.json()

    # Normalize to standard format
    return [
        {
            'tag_id': str(tag['id']),
            'label': tag['name'],
            'category': tag.get('category', 'General'),
            'description': tag.get('description', '')
        }
        for tag in tags
    ]

async def get_categories(self) -> List[Dict[str, Any]]:
    """Fetch available categories from tracker."""
    logger.info("Fetching categories")

    if not self.session:
        await self.authenticate()

    response = await self._make_request(
        'GET',
        f'{self.tracker_url}/api/categories',
        cookies=self.session
    )

    categories = response.json()

    # Normalize to standard format
    return [
        {
            'category_id': str(cat['id']),
            'name': cat['name'],
            'description': cat.get('description', '')
        }
        for cat in categories
    ]
```

#### Step 6: Implement Health Check

```python
async def health_check(self) -> Dict[str, Any]:
    """Perform comprehensive health check."""
    logger.info("Performing health check")

    health_status = {
        'healthy': True,
        'tracker_api': False,
        'authenticated': False,
        'circuit_breaker': 'closed',
        'last_error': None
    }

    # Check tracker API availability
    try:
        response = await self._make_request(
            'GET',
            f'{self.tracker_url}/api/health'
        )
        health_status['tracker_api'] = response.status_code == 200
    except Exception as e:
        health_status['healthy'] = False
        health_status['last_error'] = str(e)

    # Check authentication
    try:
        valid = await self.validate_credentials()
        health_status['authenticated'] = valid
        if not valid:
            health_status['healthy'] = False
    except Exception as e:
        health_status['healthy'] = False
        health_status['last_error'] = str(e)

    return health_status
```

#### Step 7: Implement Adapter Info

```python
def get_adapter_info(self) -> Dict[str, str]:
    """Return adapter information."""
    return {
        'name': 'Your Tracker Adapter',
        'tracker_name': '[Tracker Name]',
        'tracker_url': self.tracker_url,
        'version': '1.0.0',
        'features': ['nfo', 'mediainfo', 'screenshots']
    }
```

#### Step 8: Add Helper Methods

```python
async def _make_request(
    self,
    method: str,
    url: str,
    **kwargs
) -> Any:
    """
    Make HTTP request with error handling.

    Helper method to centralize request logic and error handling.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(method, url, **kwargs)

            # Check for HTTP errors
            if response.status_code >= 400:
                if response.status_code in (502, 503, 504):
                    # Server errors - retryable
                    raise NetworkRetryableError(
                        f"HTTP {response.status_code}: {response.text}",
                        status_code=response.status_code
                    )
                else:
                    # Client errors - non-retryable
                    raise TrackerAPIError(
                        f"HTTP {response.status_code}: {response.text}",
                        status_code=response.status_code
                    )

            return response

    except httpx.TimeoutException as e:
        raise NetworkRetryableError("Request timeout", original_exception=e)
    except httpx.ConnectError as e:
        raise NetworkRetryableError("Connection failed", original_exception=e)
```

## La Cale Adapter Reference

The `LaCaleAdapter` is the reference implementation for trackers behind Cloudflare protection.

### Key Components

```
LaCaleAdapter
    ├── CloudflareSessionManager (FlareSolverr integration)
    │   ├── Circuit breaker pattern
    │   ├── Cookie extraction
    │   └── Session management
    │
    └── LaCaleClient (Tracker API business logic)
        ├── Multipart form preparation
        ├── Repeated tags fields (CRITICAL)
        └── Upload execution
```

### Cloudflare Bypass Pattern

If your tracker uses Cloudflare, follow this pattern:

```python
from ..services.cloudflare_session_manager import CloudflareSessionManager

class YourTrackerAdapter(TrackerAdapter):
    def __init__(self, flaresolverr_url, tracker_url, ...):
        # Initialize CloudflareSessionManager
        self.session_manager = CloudflareSessionManager(
            flaresolverr_url=flaresolverr_url,
            max_timeout=60000
        )

    async def authenticate(self) -> bool:
        # Get session with Cloudflare bypass
        session = await self.session_manager.get_session(
            tracker_url=self.tracker_url
        )
        self.authenticated_session = session

        # Validate authentication
        # ... (tracker-specific validation)

        return True
```

### Repeated Fields Pattern (CRITICAL)

**Problem**: Some trackers require tags as repeated form fields, NOT JSON arrays.

**La Cale Example**:

```python
# CORRECT - Repeated fields for tags
data = [
    ('name', 'Movie.2024.1080p.BluRay'),
    ('category_id', '1'),
    ('tags', '10'),  # Repeated field
    ('tags', '15'),  # Repeated field
    ('tags', '20'),  # Repeated field
]

# INCORRECT - JSON array (causes 500 error on La Cale)
data = {
    'name': 'Movie.2024.1080p.BluRay',
    'category_id': '1',
    'tags': ['10', '15', '20']  # DON'T DO THIS
}
```

**When to Use**:
- Check tracker API documentation
- Test with tracker's API
- If tags don't work as JSON array, use repeated fields

## Critical Implementation Patterns

### 1. Torrent Source Flag (CRITICAL)

**Problem**: Without source flag, torrent clients re-download all content.

**Solution**: Always include source flag in .torrent creation.

```python
import torf

# CORRECT - Include source flag
torrent = torf.Torrent(
    path=file_path,
    trackers=[announce_url],
    source="tracker_name",  # CRITICAL
    private=True
)

# This prevents torrent clients from re-downloading content
# when the torrent is added from the tracker
```

**Tracker-Specific Values**:
- La Cale: `source="lacale"`
- Other trackers: Use tracker's short name (lowercase, no spaces)

### 2. Error Classification

**Retryable vs Non-Retryable Errors**:

```python
# Non-retryable (fail fast)
if response.status_code == 401:
    raise TrackerAPIError("Invalid API key", status_code=401)
if response.status_code == 400:
    raise TrackerAPIError("Invalid request", status_code=400)

# Retryable (will auto-retry with backoff)
if response.status_code == 503:
    raise NetworkRetryableError("Service unavailable", status_code=503)
if response.status_code == 429:
    raise NetworkRetryableError("Rate limited", status_code=429)
```

### 3. Logging Best Practices

```python
# INFO level: Authentication, uploads, major operations
logger.info(f"Uploading torrent: {release_name}")
logger.info(f"Successfully uploaded torrent ID: {torrent_id}")

# DEBUG level: API calls, request/response details
logger.debug(f"Request URL: {url}")
logger.debug(f"Request data: {data}")
logger.debug(f"Response: {response.json()}")

# ERROR level: Failures with context
logger.error(f"Upload failed: {error_msg}", exc_info=True)

# WARNING level: Recoverable issues
logger.warning(f"Retry attempt {attempt}/5 after {delay}s")
```

### 4. Session Management

```python
class YourTrackerAdapter:
    def __init__(self, ...):
        self.authenticated_session = None

    async def authenticate(self):
        # Create session
        self.authenticated_session = await self._get_session()
        return True

    async def upload_torrent(self, ...):
        # Ensure authenticated before upload
        if not self.authenticated_session:
            await self.authenticate()

        # Use authenticated session
        response = self.authenticated_session.post(...)
```

### 5. Async/Await Patterns

```python
# CORRECT - Async method with await
async def upload_torrent(self, ...):
    response = await self._make_request(...)
    return response.json()

# INCORRECT - Sync blocking call in async method
async def upload_torrent(self, ...):
    response = requests.post(...)  # Blocks event loop!
    return response.json()

# Fix: Use async HTTP client or asyncio.to_thread()
async def upload_torrent(self, ...):
    response = await asyncio.to_thread(
        requests.post, url, data=data
    )
    return response.json()
```

## Testing Your Adapter

### Unit Tests

Create `tests/unit/test_your_tracker_adapter.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from backend.app.adapters.your_tracker_adapter import YourTrackerAdapter

class TestYourTrackerAdapter:
    @pytest.fixture
    def adapter(self):
        return YourTrackerAdapter(
            tracker_url="https://tracker.example.com",
            api_key="test_api_key"
        )

    @pytest.mark.asyncio
    async def test_authenticate_success(self, adapter):
        # Mock successful authentication
        adapter._make_request = AsyncMock(
            return_value=MagicMock(
                status_code=200,
                cookies={'session': 'test_session'}
            )
        )

        result = await adapter.authenticate()
        assert result is True
        assert adapter.session is not None

    @pytest.mark.asyncio
    async def test_upload_torrent_success(self, adapter):
        # Mock successful upload
        adapter.session = {'session': 'test_session'}
        adapter._make_request = AsyncMock(
            return_value=MagicMock(
                status_code=200,
                json=lambda: {'id': '12345', 'name': 'Test Torrent'}
            )
        )

        result = await adapter.upload_torrent(
            torrent_data=b'test torrent data',
            release_name='Test.Release.2024',
            category_id='1',
            tag_ids=['10', '15']
        )

        assert result['success'] is True
        assert result['torrent_id'] == '12345'

    @pytest.mark.asyncio
    async def test_validate_credentials_invalid(self, adapter):
        adapter.api_key = "short"  # Invalid format
        result = await adapter.validate_credentials()
        assert result is False
```

### Integration Tests

Create `tests/integration/test_your_tracker_integration.py`:

```python
import pytest
from backend.app.adapters.your_tracker_adapter import YourTrackerAdapter

@pytest.mark.integration
class TestYourTrackerIntegration:
    @pytest.fixture
    def adapter(self):
        return YourTrackerAdapter(
            tracker_url="https://tracker-test.example.com",
            api_key="real_test_api_key"  # From env var
        )

    @pytest.mark.asyncio
    async def test_full_upload_flow(self, adapter, test_torrent_data):
        # Authenticate
        authenticated = await adapter.authenticate()
        assert authenticated

        # Upload torrent
        result = await adapter.upload_torrent(
            torrent_data=test_torrent_data,
            release_name='Test.Upload.2024',
            category_id='1',
            tag_ids=['10']
        )

        assert result['success']
        assert 'torrent_id' in result

        # Cleanup: Delete test upload
        # ... (if tracker API supports deletion)
```

## Configuration and Deployment

### 1. Register Adapter in Dependency Injection

Edit `backend/app/dependencies.py`:

```python
from backend.app.adapters.your_tracker_adapter import YourTrackerAdapter

def get_tracker_adapter() -> TrackerAdapter:
    """
    Dependency injection provider for TrackerAdapter.

    Reads configuration from database Settings and returns
    the appropriate adapter instance.
    """
    settings = get_settings()
    tracker_type = settings.get('tracker_type', 'lacale')

    if tracker_type == 'lacale':
        return LaCaleAdapter(
            flaresolverr_url=settings.flaresolverr_url,
            tracker_url=settings.tracker_url,
            passkey=settings.passkey
        )
    elif tracker_type == 'yourtracker':
        return YourTrackerAdapter(
            tracker_url=settings.tracker_url,
            api_key=settings.api_key
        )
    else:
        raise ValueError(f"Unknown tracker type: {tracker_type}")
```

### 2. Add Settings Migration

Create Alembic migration for new settings:

```python
# backend/alembic/versions/XXX_add_yourtracker_settings.py

def upgrade():
    # Add settings for your tracker
    op.execute("""
        INSERT INTO settings (key, value, value_type, description)
        VALUES
        ('tracker_type', 'yourtracker', 'string', 'Active tracker adapter'),
        ('yourtracker_api_key', '', 'string', 'Your Tracker API key');
    """)

def downgrade():
    op.execute("""
        DELETE FROM settings
        WHERE key IN ('tracker_type', 'yourtracker_api_key');
    """)
```

### 3. Update Settings UI

Add UI fields in `backend/app/api/settings_routes.py`:

```python
@router.get("/settings")
async def get_settings():
    settings = {
        'tracker_type': get_setting('tracker_type'),
        'yourtracker_api_key': get_setting('yourtracker_api_key'),
        # ... other settings
    }
    return settings

@router.post("/settings")
async def update_settings(data: dict):
    if 'yourtracker_api_key' in data:
        update_setting('yourtracker_api_key', data['yourtracker_api_key'])
    # ... other updates
```

## Troubleshooting

### Common Issues

#### 1. Upload Returns 500 Error

**Symptoms**: Tracker returns HTTP 500 on upload.

**Possible Causes**:
- Incorrect multipart format
- Missing required fields
- Tags formatted incorrectly (JSON array vs repeated fields)
- Invalid file format

**Debugging**:
```python
# Enable debug logging
logger.debug(f"Upload data: {data}")
logger.debug(f"Upload files: {files}")

# Capture full response
logger.error(f"Upload failed: {response.text}")
```

#### 2. Authentication Fails

**Symptoms**: `TrackerAPIError: Invalid credentials`

**Possible Causes**:
- Invalid API key/passkey format
- Cloudflare blocking requests
- Session cookies expired
- Tracker API endpoint changed

**Debugging**:
```python
# Check authentication response
logger.debug(f"Auth response: {response.json()}")
logger.debug(f"Auth cookies: {response.cookies}")

# Verify Cloudflare bypass
if using_flaresolverr:
    status = session_manager.get_status()
    logger.debug(f"Circuit breaker state: {status['state']}")
```

#### 3. Tags Not Applied

**Symptoms**: Upload succeeds but tags missing on tracker.

**Possible Causes**:
- Tags formatted as JSON array instead of repeated fields
- Invalid tag IDs
- Tags not synchronized from tracker

**Fix**:
```python
# Try repeated fields format
data = []
for tag_id in tag_ids:
    data.append(('tags', tag_id))

# Verify tag IDs are current
tags = await adapter.get_tags()
logger.info(f"Available tags: {tags}")
```

### Debug Checklist

When implementing a new adapter, verify:

- [ ] All abstract methods implemented
- [ ] Async/await used consistently
- [ ] Error handling with typed exceptions
- [ ] Logging at appropriate levels
- [ ] Session management (authenticated_session)
- [ ] Tag format matches tracker API (JSON vs repeated fields)
- [ ] Torrent source flag included
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Health check returns accurate status
- [ ] Credentials validation works
- [ ] Full upload flow tested end-to-end

---

## Next Steps

- **Review system architecture**: See [ARCHITECTURE.md](./ARCHITECTURE.md)
- **Migration from v1.1**: See [MIGRATION_GUIDE.md](./MIGRATION_GUIDE.md)
- **Reference implementation**: Study `backend/app/adapters/lacale_adapter.py`

---

**Document Version**: 1.0
**Last Updated**: 2024-01-10
**Author**: Claude Sonnet 4.5
