"""
Integration tests for Health Check Endpoints

Tests cover health check functionality for external services:
    - FlareSolverr connection verification
    - qBittorrent authentication and version check
    - TMDB API key validation
    - Error handling for missing configurations
    - Timeout and connection error scenarios

These tests can run against mocked services or real services if available.
Set environment variables to test against real services:
    - USE_REAL_SERVICES=true (default: false)
    - FLARESOLVERR_URL (default: http://localhost:8191)
    - QBITTORRENT_HOST (default: localhost:8080)
    - QBITTORRENT_USERNAME (default: admin)
    - QBITTORRENT_PASSWORD (default: adminpassword)
    - TMDB_API_KEY (your real TMDB API key)
"""

import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import requests

from backend.app.models.base import Base
from backend.app.models.settings import Settings
from backend.app.api.settings_routes import test_connection
from backend.app.database import get_db


# ============================================================================
# Test Configuration
# ============================================================================

# Check if real services are available for integration testing
USE_REAL_SERVICES = os.getenv('USE_REAL_SERVICES', 'false').lower() == 'true'
FLARESOLVERR_URL = os.getenv('FLARESOLVERR_URL', 'http://localhost:8191')
QBITTORRENT_HOST = os.getenv('QBITTORRENT_HOST', 'localhost:8080')
QBITTORRENT_USERNAME = os.getenv('QBITTORRENT_USERNAME', 'admin')
QBITTORRENT_PASSWORD = os.getenv('QBITTORRENT_PASSWORD', 'adminpassword')
TMDB_API_KEY = os.getenv('TMDB_API_KEY', 'test_api_key')


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def test_db():
    """Create temporary test database with Settings table."""
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def test_db_with_settings(test_db):
    """Test database with pre-configured Settings."""
    settings = Settings(
        tracker_url='https://lacale-test.example.com',
        tracker_passkey='test_passkey_1234567890',
        flaresolverr_url=FLARESOLVERR_URL,
        qbittorrent_host=QBITTORRENT_HOST,
        qbittorrent_username=QBITTORRENT_USERNAME,
        qbittorrent_password=QBITTORRENT_PASSWORD,
        tmdb_api_key=TMDB_API_KEY,
        input_media_path='/input',
        output_dir='/output',
        log_level='INFO',
        tmdb_cache_ttl_days=30,
        tag_sync_interval_hours=24
    )
    test_db.add(settings)
    test_db.commit()
    yield test_db


@pytest.fixture
def test_db_empty_config(test_db):
    """Test database with empty/null configuration values."""
    settings = Settings(
        flaresolverr_url=None,
        qbittorrent_host=None,
        qbittorrent_username=None,
        qbittorrent_password=None,
        tmdb_api_key=None,
        log_level='INFO',
        tmdb_cache_ttl_days=30,
        tag_sync_interval_hours=24
    )
    test_db.add(settings)
    test_db.commit()
    yield test_db


@pytest.fixture
def mock_requests_get_success():
    """Mock successful requests.get() response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    return mock_response


@pytest.fixture
def mock_requests_post_qbittorrent_success():
    """Mock successful qBittorrent authentication response."""
    mock_login_response = Mock()
    mock_login_response.status_code = 200
    mock_login_response.text = "Ok."
    mock_login_response.cookies = {'SID': 'test_session_id'}

    mock_version_response = Mock()
    mock_version_response.status_code = 200
    mock_version_response.text = "v4.5.2"

    return mock_login_response, mock_version_response


@pytest.fixture
def mock_requests_get_tmdb_success():
    """Mock successful TMDB API response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'images': {
            'base_url': 'http://image.tmdb.org/t/p/',
            'secure_base_url': 'https://image.tmdb.org/t/p/'
        }
    }
    return mock_response


# ============================================================================
# Integration Tests - FlareSolverr Health Check
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_flaresolverr_health_check_success(test_db_with_settings, mock_requests_get_success):
    """
    Test successful FlareSolverr health check.

    Verifies:
        - Connection to FlareSolverr succeeds
        - Returns success response with service details
        - Response format matches expected structure
    """
    with patch('backend.app.api.settings_routes.requests.get', return_value=mock_requests_get_success):
        result = await test_connection(service="flaresolverr", db=test_db_with_settings)

        assert result['service'] == 'flaresolverr'
        assert result['status'] == 'success'
        assert 'FlareSolverr is accessible' in result['message']
        assert result['url'] == FLARESOLVERR_URL


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flaresolverr_health_check_404_acceptable(test_db_with_settings):
    """
    Test FlareSolverr health check accepts 404 as healthy.

    Verifies:
        - 404 response is considered healthy (service running but endpoint not found)
        - Returns success response
    """
    mock_response = Mock()
    mock_response.status_code = 404

    with patch('backend.app.api.settings_routes.requests.get', return_value=mock_response):
        result = await test_connection(service="flaresolverr", db=test_db_with_settings)

        assert result['service'] == 'flaresolverr'
        assert result['status'] == 'success'


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flaresolverr_health_check_empty_config(test_db_empty_config):
    """
    Test FlareSolverr health check with empty configuration.

    Verifies:
        - Raises HTTPException when URL is not configured
        - Error message guides user to configure settings
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await test_connection(service="flaresolverr", db=test_db_empty_config)

    assert exc_info.value.status_code == 400
    assert "Please configure FlareSolverr settings first" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flaresolverr_health_check_timeout(test_db_with_settings):
    """
    Test FlareSolverr health check handles timeout.

    Verifies:
        - Timeout exception is caught and handled
        - Returns user-friendly error message
        - HTTPException raised with appropriate status code
    """
    from fastapi import HTTPException

    with patch('backend.app.api.settings_routes.requests.get', side_effect=requests.exceptions.Timeout):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="flaresolverr", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Connection timeout" in str(exc_info.value.detail)
        assert "timeout after 5s" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flaresolverr_health_check_connection_error(test_db_with_settings):
    """
    Test FlareSolverr health check handles connection error.

    Verifies:
        - Connection error is caught and handled
        - Error message suggests service may not be running
    """
    from fastapi import HTTPException

    with patch('backend.app.api.settings_routes.requests.get', side_effect=requests.exceptions.ConnectionError):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="flaresolverr", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Cannot connect to FlareSolverr" in str(exc_info.value.detail)
        assert "is it running?" in str(exc_info.value.detail).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flaresolverr_health_check_invalid_url(test_db_with_settings):
    """
    Test FlareSolverr health check handles invalid URL format.

    Verifies:
        - Invalid URL exception is caught
        - Error message suggests checking URL configuration
    """
    from fastapi import HTTPException

    with patch('backend.app.api.settings_routes.requests.get', side_effect=requests.exceptions.InvalidURL):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="flaresolverr", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Invalid URL format" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flaresolverr_health_check_unexpected_status(test_db_with_settings):
    """
    Test FlareSolverr health check handles unexpected HTTP status.

    Verifies:
        - Non-200/404 status codes are treated as errors
        - Error message includes status code
    """
    from fastapi import HTTPException

    mock_response = Mock()
    mock_response.status_code = 500

    with patch('backend.app.api.settings_routes.requests.get', return_value=mock_response):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="flaresolverr", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "unexpected status" in str(exc_info.value.detail).lower()
        assert "500" in str(exc_info.value.detail)


# ============================================================================
# Integration Tests - qBittorrent Health Check
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_qbittorrent_health_check_success(test_db_with_settings, mock_requests_post_qbittorrent_success):
    """
    Test successful qBittorrent health check.

    Verifies:
        - Authentication succeeds
        - Version is retrieved
        - Returns success response with version info
    """
    mock_login_response, mock_version_response = mock_requests_post_qbittorrent_success

    with patch('backend.app.api.settings_routes.requests.post', return_value=mock_login_response), \
         patch('backend.app.api.settings_routes.requests.get', return_value=mock_version_response):

        result = await test_connection(service="qbittorrent", db=test_db_with_settings)

        assert result['service'] == 'qbittorrent'
        assert result['status'] == 'success'
        assert 'authenticated successfully' in result['message'].lower()
        assert 'version' in result
        assert result['version'] == 'v4.5.2'
        assert result['host'] == QBITTORRENT_HOST


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qbittorrent_health_check_empty_host(test_db_empty_config):
    """
    Test qBittorrent health check with empty host configuration.

    Verifies:
        - Raises HTTPException when host is not configured
        - Error message guides user to configure settings
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await test_connection(service="qbittorrent", db=test_db_empty_config)

    assert exc_info.value.status_code == 400
    assert "Please configure qBittorrent settings first" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qbittorrent_health_check_missing_credentials(test_db):
    """
    Test qBittorrent health check with missing username/password.

    Verifies:
        - Raises HTTPException when credentials are not configured
        - Error message mentions username and password
    """
    from fastapi import HTTPException

    # Create settings with host but no credentials
    settings = Settings(
        qbittorrent_host='localhost:8080',
        qbittorrent_username=None,
        qbittorrent_password=None,
        log_level='INFO'
    )
    test_db.add(settings)
    test_db.commit()

    with pytest.raises(HTTPException) as exc_info:
        await test_connection(service="qbittorrent", db=test_db)

    assert exc_info.value.status_code == 400
    assert "username and password" in str(exc_info.value.detail).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qbittorrent_health_check_auth_failure(test_db_with_settings):
    """
    Test qBittorrent health check with invalid credentials.

    Verifies:
        - Authentication failure is detected
        - Error message indicates invalid credentials
    """
    from fastapi import HTTPException

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = "Fails."

    with patch('backend.app.api.settings_routes.requests.post', return_value=mock_response):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Authentication failed" in str(exc_info.value.detail)
        assert "invalid username or password" in str(exc_info.value.detail).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qbittorrent_health_check_timeout(test_db_with_settings):
    """
    Test qBittorrent health check handles timeout.

    Verifies:
        - Timeout exception is caught and handled
        - Returns user-friendly error message
    """
    from fastapi import HTTPException

    with patch('backend.app.api.settings_routes.requests.post', side_effect=requests.exceptions.Timeout):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Connection timeout" in str(exc_info.value.detail)
        assert "timeout after 5s" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qbittorrent_health_check_connection_error(test_db_with_settings):
    """
    Test qBittorrent health check handles connection error.

    Verifies:
        - Connection error is caught and handled
        - Error message suggests service may not be running
    """
    from fastapi import HTTPException

    with patch('backend.app.api.settings_routes.requests.post', side_effect=requests.exceptions.ConnectionError):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Cannot connect to qBittorrent" in str(exc_info.value.detail)
        assert "is it running?" in str(exc_info.value.detail).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qbittorrent_health_check_invalid_url(test_db_with_settings):
    """
    Test qBittorrent health check handles invalid URL format.

    Verifies:
        - Invalid URL exception is caught
        - Error message suggests checking host configuration
    """
    from fastapi import HTTPException

    with patch('backend.app.api.settings_routes.requests.post', side_effect=requests.exceptions.InvalidURL):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Invalid URL format" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qbittorrent_health_check_version_endpoint_failure(test_db_with_settings):
    """
    Test qBittorrent health check handles version endpoint failure.

    Verifies:
        - Authentication succeeds but version retrieval fails
        - Error message indicates version endpoint issue
    """
    from fastapi import HTTPException

    mock_login_response = Mock()
    mock_login_response.status_code = 200
    mock_login_response.text = "Ok."
    mock_login_response.cookies = {'SID': 'test_session_id'}

    mock_version_response = Mock()
    mock_version_response.status_code = 403

    with patch('backend.app.api.settings_routes.requests.post', return_value=mock_login_response), \
         patch('backend.app.api.settings_routes.requests.get', return_value=mock_version_response):

        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Version endpoint returned" in str(exc_info.value.detail)
        assert "403" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qbittorrent_health_check_adds_http_prefix(test_db):
    """
    Test qBittorrent health check adds http:// prefix to host without protocol.

    Verifies:
        - Host without protocol gets http:// prefix added
        - Request is made to correct URL with prefix
    """
    # Create settings with host without protocol
    settings = Settings(
        qbittorrent_host='localhost:8080',  # No http:// prefix
        qbittorrent_username='admin',
        qbittorrent_password='adminpassword',
        log_level='INFO'
    )
    test_db.add(settings)
    test_db.commit()

    mock_login_response = Mock()
    mock_login_response.status_code = 200
    mock_login_response.text = "Ok."
    mock_login_response.cookies = {'SID': 'test_session_id'}

    mock_version_response = Mock()
    mock_version_response.status_code = 200
    mock_version_response.text = "v4.5.2"

    with patch('backend.app.api.settings_routes.requests.post', return_value=mock_login_response) as mock_post, \
         patch('backend.app.api.settings_routes.requests.get', return_value=mock_version_response):

        result = await test_connection(service="qbittorrent", db=test_db)

        # Verify http:// prefix was added to the URL
        call_args = mock_post.call_args
        assert call_args[0][0].startswith('http://')
        assert result['status'] == 'success'


# ============================================================================
# Integration Tests - TMDB Health Check
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_tmdb_health_check_success(test_db_with_settings, mock_requests_get_tmdb_success):
    """
    Test successful TMDB API key validation.

    Verifies:
        - API key is validated successfully
        - Returns success response
        - Response format matches expected structure
    """
    with patch('backend.app.api.settings_routes.requests.get', return_value=mock_requests_get_tmdb_success):
        result = await test_connection(service="tmdb", db=test_db_with_settings)

        assert result['service'] == 'tmdb'
        assert result['status'] == 'success'
        assert 'TMDB API key is valid' in result['message']


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tmdb_health_check_empty_api_key(test_db_empty_config):
    """
    Test TMDB health check with empty API key.

    Verifies:
        - Raises HTTPException when API key is not configured
        - Error message guides user to configure API key
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await test_connection(service="tmdb", db=test_db_empty_config)

    assert exc_info.value.status_code == 400
    assert "Please configure TMDB API key first" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tmdb_health_check_invalid_api_key(test_db_with_settings):
    """
    Test TMDB health check with invalid API key.

    Verifies:
        - 401 Unauthorized response indicates invalid API key
        - Error message clearly states authentication failure
    """
    from fastapi import HTTPException

    mock_response = Mock()
    mock_response.status_code = 401

    with patch('backend.app.api.settings_routes.requests.get', return_value=mock_response):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Invalid TMDB API key" in str(exc_info.value.detail)
        assert "authentication failed" in str(exc_info.value.detail).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tmdb_health_check_endpoint_not_found(test_db_with_settings):
    """
    Test TMDB health check handles 404 endpoint not found.

    Verifies:
        - 404 response indicates endpoint issue
        - Error message suggests checking configuration
    """
    from fastapi import HTTPException

    mock_response = Mock()
    mock_response.status_code = 404

    with patch('backend.app.api.settings_routes.requests.get', return_value=mock_response):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "endpoint not found" in str(exc_info.value.detail).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tmdb_health_check_timeout(test_db_with_settings):
    """
    Test TMDB health check handles timeout.

    Verifies:
        - Timeout exception is caught and handled
        - Returns user-friendly error message
    """
    from fastapi import HTTPException

    with patch('backend.app.api.settings_routes.requests.get', side_effect=requests.exceptions.Timeout):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Connection timeout" in str(exc_info.value.detail)
        assert "timeout after 5s" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tmdb_health_check_connection_error(test_db_with_settings):
    """
    Test TMDB health check handles connection error.

    Verifies:
        - Connection error is caught and handled
        - Error message suggests checking internet connection
    """
    from fastapi import HTTPException

    with patch('backend.app.api.settings_routes.requests.get', side_effect=requests.exceptions.ConnectionError):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Cannot connect to TMDB API" in str(exc_info.value.detail)
        assert "internet connection" in str(exc_info.value.detail).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tmdb_health_check_invalid_url(test_db_with_settings):
    """
    Test TMDB health check handles invalid URL format.

    Verifies:
        - Invalid URL exception is caught (unlikely for hardcoded TMDB URL)
        - Error message suggests checking configuration
    """
    from fastapi import HTTPException

    with patch('backend.app.api.settings_routes.requests.get', side_effect=requests.exceptions.InvalidURL):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "Invalid URL format" in str(exc_info.value.detail)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tmdb_health_check_unexpected_status(test_db_with_settings):
    """
    Test TMDB health check handles unexpected HTTP status.

    Verifies:
        - Non-standard status codes are treated as errors
        - Error message includes status code
    """
    from fastapi import HTTPException

    mock_response = Mock()
    mock_response.status_code = 503

    with patch('backend.app.api.settings_routes.requests.get', return_value=mock_response):
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=test_db_with_settings)

        assert exc_info.value.status_code == 400
        assert "unexpected status" in str(exc_info.value.detail).lower()
        assert "503" in str(exc_info.value.detail)


# ============================================================================
# Integration Tests - Unknown Service
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_check_unknown_service(test_db_with_settings):
    """
    Test health check with unknown service name.

    Verifies:
        - Unknown service raises HTTPException
        - Error message indicates unknown service
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await test_connection(service="unknown_service", db=test_db_with_settings)

    assert exc_info.value.status_code == 400
    assert "Unknown service" in str(exc_info.value.detail)


# ============================================================================
# Integration Tests - Real Services (Optional)
# ============================================================================

@pytest.mark.skipif(not USE_REAL_SERVICES, reason="Real services not available (USE_REAL_SERVICES=false)")
@pytest.mark.integration
@pytest.mark.asyncio
async def test_flaresolverr_real_service(test_db_with_settings):
    """
    Test FlareSolverr health check with real service.

    REQUIRES: FlareSolverr running at configured URL
    Set USE_REAL_SERVICES=true to enable this test.
    """
    result = await test_connection(service="flaresolverr", db=test_db_with_settings)

    assert result['service'] == 'flaresolverr'
    assert result['status'] == 'success'
    assert 'url' in result


@pytest.mark.skipif(not USE_REAL_SERVICES, reason="Real services not available (USE_REAL_SERVICES=false)")
@pytest.mark.integration
@pytest.mark.asyncio
async def test_qbittorrent_real_service(test_db_with_settings):
    """
    Test qBittorrent health check with real service.

    REQUIRES: qBittorrent running at configured host with valid credentials
    Set USE_REAL_SERVICES=true to enable this test.
    """
    result = await test_connection(service="qbittorrent", db=test_db_with_settings)

    assert result['service'] == 'qbittorrent'
    assert result['status'] == 'success'
    assert 'version' in result
    assert 'host' in result


@pytest.mark.skipif(not USE_REAL_SERVICES or not TMDB_API_KEY or TMDB_API_KEY == 'test_api_key',
                    reason="Real TMDB API key not available")
@pytest.mark.integration
@pytest.mark.asyncio
async def test_tmdb_real_service(test_db_with_settings):
    """
    Test TMDB health check with real service.

    REQUIRES: Valid TMDB API key configured
    Set USE_REAL_SERVICES=true and TMDB_API_KEY to enable this test.
    """
    result = await test_connection(service="tmdb", db=test_db_with_settings)

    assert result['service'] == 'tmdb'
    assert result['status'] == 'success'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
