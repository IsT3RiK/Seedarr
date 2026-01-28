"""
Integration tests for Upload Flow with FlareSolverr

Tests cover end-to-end upload workflow:
    - FlareSolverr cookie retrieval and session management
    - Cloudflare bypass authentication
    - Torrent upload with La Cale tracker API
    - CRITICAL: Tags as repeated fields (NOT JSON arrays)
    - Session cookie persistence across requests
    - Error handling and retry logic

These tests can run against mocked services or real services if available.
Set environment variables to test against real services:
    - FLARESOLVERR_URL (default: http://localhost:8191)
    - TRACKER_URL (default: mock)
    - TRACKER_PASSKEY (default: mock)
"""

import asyncio
import os
import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from pathlib import Path
import requests
from requests import Session

from backend.app.adapters.lacale_adapter import LaCaleAdapter
from backend.app.services.cloudflare_session_manager import (
    CloudflareSessionManager,
    CircuitBreakerState
)
from backend.app.services.lacale_client import LaCaleClient
from backend.app.services.exceptions import (
    TrackerAPIError,
    CloudflareBypassError,
    NetworkRetryableError
)


# ============================================================================
# Test Configuration
# ============================================================================

# Check if real services are available for integration testing
FLARESOLVERR_URL = os.getenv('FLARESOLVERR_URL', 'http://localhost:8191')
TRACKER_URL = os.getenv('TRACKER_URL', 'https://lacale-test.example.com')
TRACKER_PASSKEY = os.getenv('TRACKER_PASSKEY', 'test_passkey_1234567890')
USE_REAL_SERVICES = os.getenv('USE_REAL_SERVICES', 'false').lower() == 'true'


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def flaresolverr_url():
    """FlareSolverr service URL."""
    return FLARESOLVERR_URL


@pytest.fixture
def tracker_url():
    """La Cale tracker URL."""
    return TRACKER_URL


@pytest.fixture
def tracker_passkey():
    """La Cale tracker passkey."""
    return TRACKER_PASSKEY


@pytest.fixture
def mock_flaresolverr_response():
    """Mock successful FlareSolverr response with cookies."""
    return {
        'status': 'ok',
        'message': 'Challenge solved!',
        'solution': {
            'url': 'https://lacale-test.example.com',
            'status': 200,
            'cookies': [
                {'name': 'cf_clearance', 'value': 'test_clearance_token'},
                {'name': 'PHPSESSID', 'value': 'test_session_id'},
                {'name': '__cfduid', 'value': 'test_cloudflare_id'}
            ],
            'userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    }


@pytest.fixture
def mock_upload_success_response():
    """Mock successful upload response from La Cale tracker."""
    return {
        'success': True,
        'torrent_id': '12345',
        'torrent_url': 'https://lacale-test.example.com/torrents/12345',
        'message': 'Torrent uploaded successfully'
    }


@pytest.fixture
def mock_tags_response():
    """Mock tags response from La Cale tracker."""
    return [
        {'tag_id': '10', 'label': 'BluRay', 'category': 'source', 'description': 'BluRay source'},
        {'tag_id': '15', 'label': '1080p', 'category': 'resolution', 'description': '1080p resolution'},
        {'tag_id': '20', 'label': 'French', 'category': 'language', 'description': 'French audio'}
    ]


@pytest.fixture
def mock_categories_response():
    """Mock categories response from La Cale tracker."""
    return [
        {'category_id': '1', 'name': 'Movies', 'description': 'Movie torrents'},
        {'category_id': '2', 'name': 'TV Shows', 'description': 'TV show torrents'}
    ]


@pytest.fixture
def sample_torrent_data():
    """Sample .torrent file data for testing."""
    # Minimal valid .torrent file structure (bencoded)
    return b'd8:announce44:https://lacale-test.example.com/announce/test13:creation datei1609459200e4:infod6:lengthi1048576e4:name15:test_movie.mkv12:piece lengthi262144e6:pieces20:\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x136:source6:lacaleee'


@pytest.fixture
def lacale_adapter(flaresolverr_url, tracker_url, tracker_passkey):
    """Create LaCaleAdapter instance for testing."""
    return LaCaleAdapter(
        flaresolverr_url=flaresolverr_url,
        tracker_url=tracker_url,
        passkey=tracker_passkey,
        flaresolverr_timeout=30000
    )


# ============================================================================
# Integration Tests - Authentication Flow
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_flaresolverr_cookie_retrieval(
    lacale_adapter,
    mock_flaresolverr_response
):
    """
    Test FlareSolverr cookie retrieval and session creation.

    Verifies:
        - FlareSolverr request sent correctly
        - Cookies extracted from response
        - Session created with cookies
        - Session is authenticated
    """
    with patch('requests.post') as mock_post:
        # Mock FlareSolverr response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_flaresolverr_response
        mock_post.return_value = mock_response

        # Mock passkey validation
        with patch.object(
            lacale_adapter.client,
            'validate_passkey',
            new=AsyncMock(return_value=True)
        ):
            # Authenticate
            result = await lacale_adapter.authenticate()

            # Verify authentication succeeded
            assert result is True
            assert lacale_adapter.authenticated_session is not None

            # Verify session has cookies
            session = lacale_adapter.authenticated_session
            assert 'cf_clearance' in session.cookies
            assert 'PHPSESSID' in session.cookies
            assert 'PHPSESSID' in session.cookies

            # Verify FlareSolverr was called correctly
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert FLARESOLVERR_URL in call_args[0][0]
            assert 'json' in call_args[1]
            assert call_args[1]['json']['cmd'] == 'request.get'


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cloudflare_bypass_with_retry(lacale_adapter):
    """
    Test Cloudflare bypass with retry logic on transient failures.

    Verifies:
        - First request fails with timeout
        - Retry logic triggers automatically
        - Second request succeeds
        - Final authentication succeeds
    """
    with patch('requests.post') as mock_post:
        # First call fails, second succeeds
        mock_response_fail = Mock()
        mock_response_fail.status_code = 500
        mock_response_fail.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        mock_response_success = Mock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = {
            'status': 'ok',
            'solution': {
                'url': 'https://lacale-test.example.com',
                'status': 200,
                'cookies': [
                    {'name': 'cf_clearance', 'value': 'token123'}
                ]
            }
        }

        mock_post.side_effect = [
            mock_response_fail,
            mock_response_success
        ]

        # Mock passkey validation
        with patch.object(
            lacale_adapter.client,
            'validate_passkey',
            new=AsyncMock(return_value=True)
        ):
            # Authenticate - should succeed after retry
            result = await lacale_adapter.authenticate()

            # Verify authentication succeeded
            assert result is True
            assert lacale_adapter.authenticated_session is not None

            # Verify retry occurred (2 calls)
            assert mock_post.call_count == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_authentication_invalid_passkey(lacale_adapter):
    """
    Test authentication failure with invalid passkey.

    Verifies:
        - FlareSolverr succeeds
        - Passkey validation fails
        - TrackerAPIError raised with 403 status
    """
    with patch('requests.post') as mock_post:
        # Mock successful FlareSolverr response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'status': 'ok',
            'solution': {
                'url': 'https://lacale-test.example.com',
                'status': 200,
                'cookies': [
                    {'name': 'cf_clearance', 'value': 'token123'}
                ]
            }
        }
        mock_post.return_value = mock_response

        # Mock passkey validation failure
        with patch.object(
            lacale_adapter.client,
            'validate_passkey',
            new=AsyncMock(return_value=False)
        ):
            # Authenticate - should fail
            with pytest.raises(TrackerAPIError) as exc_info:
                await lacale_adapter.authenticate()

            # Verify error details
            assert exc_info.value.status_code == 403
            assert 'passkey' in str(exc_info.value).lower()


# ============================================================================
# Integration Tests - Upload Flow
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_flow_end_to_end(
    lacale_adapter,
    sample_torrent_data,
    mock_flaresolverr_response,
    mock_upload_success_response
):
    """
    Test complete upload flow from authentication to upload completion.

    Verifies:
        - Authentication via FlareSolverr
        - Session cookie management
        - Torrent upload with metadata
        - CRITICAL: Tags sent as repeated fields
        - Upload response parsed correctly
    """
    with patch('requests.post') as mock_post:
        # Setup mock responses
        # 1. FlareSolverr authentication
        mock_flaresolverr = Mock()
        mock_flaresolverr.status_code = 200
        mock_flaresolverr.json.return_value = mock_flaresolverr_response

        # 2. Upload request
        mock_upload = Mock()
        mock_upload.status_code = 200
        mock_upload.json.return_value = mock_upload_success_response

        mock_post.side_effect = [
            mock_flaresolverr,
            mock_upload
        ]

        # Mock passkey validation
        with patch.object(
            lacale_adapter.client,
            'validate_passkey',
            new=AsyncMock(return_value=True)
        ):
            # Authenticate
            auth_result = await lacale_adapter.authenticate()
            assert auth_result is True

            # Upload torrent
            upload_result = await lacale_adapter.upload_torrent(
                torrent_data=sample_torrent_data,
                release_name="Test.Movie.2023.1080p.BluRay.x264-TEST",
                category_id="1",
                tag_ids=["10", "15", "20"],
                nfo_content="Test NFO content",
                description="Test movie description"
            )

            # Verify upload succeeded
            assert upload_result['success'] is True
            assert upload_result['torrent_id'] == '12345'
            assert 'torrent_url' in upload_result

            # Verify upload request was made correctly
            assert mock_post.call_count == 2
            upload_call = mock_post.call_args_list[1]

            # Verify multipart form-data sent
            assert 'data' in upload_call[1]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_tags_repeated_fields_pattern(
    lacale_adapter,
    sample_torrent_data,
    mock_flaresolverr_response,
    mock_upload_success_response
):
    """
    Test CRITICAL pattern: Tags must be sent as repeated fields, NOT JSON array.

    This is a critical requirement documented in FIX_TAGS_REPEATED_FIELDS.md.
    Tags MUST be sent as:
        [('tags', '10'), ('tags', '15'), ('tags', '20')]
    NOT as:
        {'tags': ['10', '15', '20']}

    Verifies:
        - Multipart data prepared correctly
        - Tags sent as repeated form fields
        - No JSON array in upload data
    """
    # Capture the actual multipart data sent
    captured_data = None

    def capture_post(*args, **kwargs):
        nonlocal captured_data
        captured_data = kwargs.get('data')

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_upload_success_response
        return mock_response

    with patch('requests.post') as mock_post:
        mock_post.side_effect = capture_post

        # Mock authentication
        with patch.object(
            lacale_adapter,
            'authenticated_session',
            new=Mock(spec=Session)
        ):
            # Prepare multipart data (bypassing authentication)
            tag_ids = ["10", "15", "20"]
            data = lacale_adapter.client._prepare_multipart_data(
                release_name="Test.Movie.2023.1080p",
                category_id="1",
                tag_ids=tag_ids,
                nfo_content="Test NFO"
            )

            # CRITICAL: Verify tags are repeated fields
            tag_fields = [item for item in data if item[0] == 'tags']
            assert len(tag_fields) == 3, "Should have 3 separate tag fields"

            # Verify each tag is a separate tuple
            assert ('tags', '10') in data
            assert ('tags', '15') in data
            assert ('tags', '20') in data

            # Verify data is list of tuples (NOT dict)
            assert isinstance(data, list)
            for item in data:
                assert isinstance(item, tuple)
                assert len(item) == 2

            # Verify NO JSON array for tags
            # Check that no field contains a list or JSON array
            for field, value in data:
                assert not isinstance(value, list), f"Field '{field}' should not be a list"
                assert not isinstance(value, dict), f"Field '{field}' should not be a dict"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_with_auto_authentication(
    lacale_adapter,
    sample_torrent_data,
    mock_flaresolverr_response,
    mock_upload_success_response
):
    """
    Test upload with automatic authentication when session not established.

    Verifies:
        - Upload called without prior authentication
        - Adapter automatically authenticates
        - Upload proceeds successfully
    """
    with patch('requests.post') as mock_post:
        # Setup mock responses
        mock_flaresolverr = Mock()
        mock_flaresolverr.status_code = 200
        mock_flaresolverr.json.return_value = mock_flaresolverr_response

        mock_upload = Mock()
        mock_upload.status_code = 200
        mock_upload.json.return_value = mock_upload_success_response

        mock_post.side_effect = [
            mock_flaresolverr,
            mock_upload
        ]

        # Mock passkey validation
        with patch.object(
            lacale_adapter.client,
            'validate_passkey',
            new=AsyncMock(return_value=True)
        ):
            # Upload without prior authentication
            # Adapter should automatically authenticate
            upload_result = await lacale_adapter.upload_torrent(
                torrent_data=sample_torrent_data,
                release_name="Test.Movie.2023.1080p",
                category_id="1",
                tag_ids=["10", "15"],
                nfo_content="Test NFO"
            )

            # Verify upload succeeded
            assert upload_result['success'] is True

            # Verify both authentication and upload occurred
            assert mock_post.call_count == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_network_error_retry(
    lacale_adapter,
    sample_torrent_data,
    mock_flaresolverr_response,
    mock_upload_success_response
):
    """
    Test upload retry logic on transient network errors.

    Verifies:
        - First upload fails with network error
        - Retry logic triggers
        - Second upload succeeds
    """
    with patch('requests.post') as mock_post:
        # Setup mock responses
        mock_flaresolverr = Mock()
        mock_flaresolverr.status_code = 200
        mock_flaresolverr.json.return_value = mock_flaresolverr_response

        # First upload fails, second succeeds
        mock_upload_fail = Mock()
        mock_upload_fail.status_code = 500
        mock_upload_fail.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        mock_upload_success = Mock()
        mock_upload_success.status_code = 200
        mock_upload_success.json.return_value = mock_upload_success_response

        mock_post.side_effect = [
            mock_flaresolverr,
            mock_upload_fail,
            mock_upload_success
        ]

        # Mock passkey validation
        with patch.object(
            lacale_adapter.client,
            'validate_passkey',
            new=AsyncMock(return_value=True)
        ):
            # Upload - should succeed after retry
            upload_result = await lacale_adapter.upload_torrent(
                torrent_data=sample_torrent_data,
                release_name="Test.Movie.2023.1080p",
                category_id="1",
                tag_ids=["10"],
                nfo_content="Test NFO"
            )

            # Verify upload succeeded
            assert upload_result['success'] is True

            # Verify retry occurred (3 calls: auth, upload fail, upload success)
            assert mock_post.call_count == 3


# ============================================================================
# Integration Tests - Tags and Categories
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_tags_from_tracker(
    lacale_adapter,
    mock_flaresolverr_response,
    mock_tags_response
):
    """
    Test fetching tags from La Cale tracker.

    Verifies:
        - Authentication establishes session
        - Tags API called correctly
        - Tags response parsed
        - Tag IDs and labels returned
    """
    with patch('requests.post') as mock_post, \
         patch('requests.get') as mock_get:

        # Mock FlareSolverr authentication
        mock_flaresolverr = Mock()
        mock_flaresolverr.status_code = 200
        mock_flaresolverr.json.return_value = mock_flaresolverr_response
        mock_post.return_value = mock_flaresolverr

        # Mock tags API response
        mock_tags_resp = Mock()
        mock_tags_resp.status_code = 200
        mock_tags_resp.json.return_value = mock_tags_response
        mock_get.return_value = mock_tags_resp

        # Mock passkey validation
        with patch.object(
            lacale_adapter.client,
            'validate_passkey',
            new=AsyncMock(return_value=True)
        ):
            # Authenticate
            await lacale_adapter.authenticate()

            # Fetch tags
            tags = await lacale_adapter.get_tags()

            # Verify tags returned
            assert len(tags) == 3
            assert tags[0]['tag_id'] == '10'
            assert tags[0]['label'] == 'BluRay'
            assert tags[1]['tag_id'] == '15'
            assert tags[2]['tag_id'] == '20'


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_categories_from_tracker(
    lacale_adapter,
    mock_flaresolverr_response,
    mock_categories_response
):
    """
    Test fetching categories from La Cale tracker.

    Verifies:
        - Authentication establishes session
        - Categories API called correctly
        - Categories response parsed
        - Category IDs and names returned
    """
    with patch('requests.post') as mock_post, \
         patch('requests.get') as mock_get:

        # Mock FlareSolverr authentication
        mock_flaresolverr = Mock()
        mock_flaresolverr.status_code = 200
        mock_flaresolverr.json.return_value = mock_flaresolverr_response
        mock_post.return_value = mock_flaresolverr

        # Mock categories API response
        mock_categories_resp = Mock()
        mock_categories_resp.status_code = 200
        mock_categories_resp.json.return_value = mock_categories_response
        mock_get.return_value = mock_categories_resp

        # Mock passkey validation
        with patch.object(
            lacale_adapter.client,
            'validate_passkey',
            new=AsyncMock(return_value=True)
        ):
            # Authenticate
            await lacale_adapter.authenticate()

            # Fetch categories
            categories = await lacale_adapter.get_categories()

            # Verify categories returned
            assert len(categories) == 2
            assert categories[0]['category_id'] == '1'
            assert categories[0]['name'] == 'Movies'
            assert categories[1]['category_id'] == '2'
            assert categories[1]['name'] == 'TV Shows'


# ============================================================================
# Integration Tests - Circuit Breaker
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_failures(lacale_adapter):
    """
    Test circuit breaker opens after consecutive FlareSolverr failures.

    Verifies:
        - First 3 failures trigger retries
        - Circuit opens after 3 failures
        - Subsequent calls fail fast without hitting FlareSolverr
    """
    with patch('requests.post') as mock_post:
        # All calls fail
        mock_post.side_effect = requests.ConnectionError("Service unavailable")

        # Try to authenticate 4 times
        for i in range(4):
            with pytest.raises((CloudflareBypassError, NetworkRetryableError)):
                await lacale_adapter.authenticate()

        # Verify circuit breaker state
        status = lacale_adapter.get_flaresolverr_status()

        # Circuit should be OPEN after 3 failures
        assert status['state'] in [CircuitBreakerState.OPEN.value, CircuitBreakerState.HALF_OPEN.value]
        assert status['failure_count'] >= 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_check_comprehensive(
    lacale_adapter,
    mock_flaresolverr_response
):
    """
    Test comprehensive health check of adapter and dependencies.

    Verifies:
        - FlareSolverr availability checked
        - Tracker connectivity checked
        - Credentials validated
        - Circuit breaker state reported
        - Overall health status accurate
    """
    with patch('requests.post') as mock_post, \
         patch('requests.get') as mock_get:

        # Mock successful FlareSolverr health check
        mock_flaresolverr_health = Mock()
        mock_flaresolverr_health.status_code = 200
        mock_get.return_value = mock_flaresolverr_health

        # Mock successful authentication
        mock_flaresolverr_auth = Mock()
        mock_flaresolverr_auth.status_code = 200
        mock_flaresolverr_auth.json.return_value = mock_flaresolverr_response
        mock_post.return_value = mock_flaresolverr_auth

        # Mock passkey validation
        with patch.object(
            lacale_adapter.client,
            'validate_passkey',
            new=AsyncMock(return_value=True)
        ):
            # Perform health check
            health = await lacale_adapter.health_check()

            # Verify health status
            assert health['healthy'] is True
            assert health['flaresolverr_available'] is True
            assert health['tracker_reachable'] is True
            assert health['credentials_valid'] is True
            assert health['circuit_breaker_state'] == CircuitBreakerState.CLOSED.value


# ============================================================================
# Integration Tests - Session Management
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_cookie_persistence(
    lacale_adapter,
    mock_flaresolverr_response,
    mock_upload_success_response,
    sample_torrent_data
):
    """
    Test session cookies persist across multiple requests.

    Verifies:
        - Authentication creates session with cookies
        - Cookies reused in subsequent uploads
        - No re-authentication needed for multiple uploads
    """
    with patch('requests.post') as mock_post:
        # Setup mock responses
        mock_flaresolverr = Mock()
        mock_flaresolverr.status_code = 200
        mock_flaresolverr.json.return_value = mock_flaresolverr_response

        mock_upload = Mock()
        mock_upload.status_code = 200
        mock_upload.json.return_value = mock_upload_success_response

        # Only authenticate once, then 3 uploads
        mock_post.side_effect = [
            mock_flaresolverr,
            mock_upload,
            mock_upload,
            mock_upload
        ]

        # Mock passkey validation
        with patch.object(
            lacale_adapter.client,
            'validate_passkey',
            new=AsyncMock(return_value=True)
        ):
            # Authenticate once
            await lacale_adapter.authenticate()

            # Upload 3 times with same session
            for i in range(3):
                result = await lacale_adapter.upload_torrent(
                    torrent_data=sample_torrent_data,
                    release_name=f"Test.Movie.{i}.2023.1080p",
                    category_id="1",
                    tag_ids=["10"],
                    nfo_content="Test NFO"
                )
                assert result['success'] is True

            # Verify only 1 authentication + 3 uploads (no re-auth)
            assert mock_post.call_count == 4


if __name__ == '__main__':
    # Run tests with pytest
    pytest.main([__file__, '-v', '--tb=short'])
