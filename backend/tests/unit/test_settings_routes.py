"""
Unit Tests for Settings Routes - Health Check Functionality

This test suite validates the health check functionality for external services
in the Settings API routes, including FlareSolverr, qBittorrent, and TMDB.

Test Coverage:
    - FlareSolverr health check (success, timeout, connection errors, invalid URL)
    - qBittorrent health check (success with version, auth failure, timeout, connection errors)
    - TMDB health check (success, invalid API key, timeout, connection errors)
    - Configuration validation (empty/missing settings)
    - Error handling and HTTPException responses
    - Unknown service handling

Requirements:
    - pytest
    - fastapi (for HTTPException)
    - sqlalchemy (for database mocking)
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi import HTTPException
from sqlalchemy.orm import Session
import requests

from backend.app.api.settings_routes import test_connection
from backend.app.models.settings import Settings


class TestFlareSolverrHealthCheck:
    """Test FlareSolverr health check functionality."""

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_flaresolverr_success_http_200(self, mock_get_settings, mock_requests_get):
        """Test FlareSolverr health check succeeds with HTTP 200."""
        db = Mock(spec=Session)

        # Mock settings with FlareSolverr URL
        mock_settings = Mock(spec=Settings)
        mock_settings.flaresolverr_url = "http://localhost:8191"
        mock_get_settings.return_value = mock_settings

        # Mock successful HTTP response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests_get.return_value = mock_response

        # Execute
        result = await test_connection(service="flaresolverr", db=db)

        # Verify
        assert result['service'] == 'flaresolverr'
        assert result['status'] == 'success'
        assert 'accessible' in result['message'].lower()
        assert result['url'] == 'http://localhost:8191'
        mock_requests_get.assert_called_once_with('http://localhost:8191', timeout=5)

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_flaresolverr_success_http_404(self, mock_get_settings, mock_requests_get):
        """Test FlareSolverr health check succeeds with HTTP 404 (service running)."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.flaresolverr_url = "http://localhost:8191/"
        mock_get_settings.return_value = mock_settings

        # Mock 404 response (service is running, endpoint not found)
        mock_response = Mock()
        mock_response.status_code = 404
        mock_requests_get.return_value = mock_response

        # Execute
        result = await test_connection(service="flaresolverr", db=db)

        # Verify
        assert result['service'] == 'flaresolverr'
        assert result['status'] == 'success'
        assert 'accessible' in result['message'].lower()

    @pytest.mark.asyncio
    @patch.object(Settings, 'get_settings')
    async def test_flaresolverr_empty_config(self, mock_get_settings):
        """Test FlareSolverr health check fails with empty configuration."""
        db = Mock(spec=Session)

        # Mock settings with no FlareSolverr URL
        mock_settings = Mock(spec=Settings)
        mock_settings.flaresolverr_url = None
        mock_get_settings.return_value = mock_settings

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="flaresolverr", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'configure' in exc_info.value.detail.lower()
        assert 'flaresolverr' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_flaresolverr_timeout(self, mock_get_settings, mock_requests_get):
        """Test FlareSolverr health check handles timeout."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.flaresolverr_url = "http://localhost:8191"
        mock_get_settings.return_value = mock_settings

        # Mock timeout exception
        mock_requests_get.side_effect = requests.exceptions.Timeout("Connection timeout")

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="flaresolverr", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'timeout' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_flaresolverr_connection_error(self, mock_get_settings, mock_requests_get):
        """Test FlareSolverr health check handles connection error."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.flaresolverr_url = "http://localhost:8191"
        mock_get_settings.return_value = mock_settings

        # Mock connection error
        mock_requests_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="flaresolverr", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'cannot connect' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_flaresolverr_invalid_url(self, mock_get_settings, mock_requests_get):
        """Test FlareSolverr health check handles invalid URL."""
        db = Mock(spec=Session)

        # Mock settings with malformed URL
        mock_settings = Mock(spec=Settings)
        mock_settings.flaresolverr_url = "not-a-valid-url"
        mock_get_settings.return_value = mock_settings

        # Mock invalid URL exception
        mock_requests_get.side_effect = requests.exceptions.InvalidURL("Invalid URL")

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="flaresolverr", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'invalid url' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_flaresolverr_unexpected_status(self, mock_get_settings, mock_requests_get):
        """Test FlareSolverr health check handles unexpected status code."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.flaresolverr_url = "http://localhost:8191"
        mock_get_settings.return_value = mock_settings

        # Mock unexpected status code
        mock_response = Mock()
        mock_response.status_code = 500
        mock_requests_get.return_value = mock_response

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="flaresolverr", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'unexpected status' in exc_info.value.detail.lower()
        assert '500' in exc_info.value.detail


class TestQBittorrentHealthCheck:
    """Test qBittorrent health check functionality."""

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.post')
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_qbittorrent_success(self, mock_get_settings, mock_requests_get, mock_requests_post):
        """Test qBittorrent health check succeeds with valid credentials."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.qbittorrent_host = "localhost:8080"
        mock_settings.qbittorrent_username = "admin"
        mock_settings.qbittorrent_password = "adminpass"
        mock_get_settings.return_value = mock_settings

        # Mock successful authentication
        mock_auth_response = Mock()
        mock_auth_response.status_code = 200
        mock_auth_response.text = "Ok."
        mock_auth_response.cookies = {'SID': 'test_session_id'}
        mock_requests_post.return_value = mock_auth_response

        # Mock successful version request
        mock_version_response = Mock()
        mock_version_response.status_code = 200
        mock_version_response.text = "v4.5.0"
        mock_requests_get.return_value = mock_version_response

        # Execute
        result = await test_connection(service="qbittorrent", db=db)

        # Verify
        assert result['service'] == 'qbittorrent'
        assert result['status'] == 'success'
        assert 'authenticated successfully' in result['message'].lower()
        assert result['version'] == 'v4.5.0'
        assert result['host'] == 'localhost:8080'

        # Verify API calls
        mock_requests_post.assert_called_once()
        auth_call = mock_requests_post.call_args
        assert 'http://localhost:8080/api/v2/auth/login' in auth_call[0]
        assert auth_call[1]['data']['username'] == 'admin'
        assert auth_call[1]['data']['password'] == 'adminpass'

        mock_requests_get.assert_called_once()
        version_call = mock_requests_get.call_args
        assert 'http://localhost:8080/api/v2/app/version' in version_call[0]
        assert version_call[1]['cookies'] == {'SID': 'test_session_id'}

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.post')
    @patch.object(Settings, 'get_settings')
    async def test_qbittorrent_auth_failure(self, mock_get_settings, mock_requests_post):
        """Test qBittorrent health check fails with invalid credentials."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.qbittorrent_host = "localhost:8080"
        mock_settings.qbittorrent_username = "admin"
        mock_settings.qbittorrent_password = "wrongpassword"
        mock_get_settings.return_value = mock_settings

        # Mock authentication failure
        mock_auth_response = Mock()
        mock_auth_response.status_code = 200
        mock_auth_response.text = "Fails."
        mock_requests_post.return_value = mock_auth_response

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'authentication failed' in exc_info.value.detail.lower()
        assert 'invalid username or password' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch.object(Settings, 'get_settings')
    async def test_qbittorrent_empty_config(self, mock_get_settings):
        """Test qBittorrent health check fails with empty configuration."""
        db = Mock(spec=Session)

        # Mock settings with no qBittorrent host
        mock_settings = Mock(spec=Settings)
        mock_settings.qbittorrent_host = None
        mock_settings.qbittorrent_username = "admin"
        mock_settings.qbittorrent_password = "pass"
        mock_get_settings.return_value = mock_settings

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'configure' in exc_info.value.detail.lower()
        assert 'qbittorrent' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch.object(Settings, 'get_settings')
    async def test_qbittorrent_missing_credentials(self, mock_get_settings):
        """Test qBittorrent health check fails with missing credentials."""
        db = Mock(spec=Session)

        # Mock settings with host but no credentials
        mock_settings = Mock(spec=Settings)
        mock_settings.qbittorrent_host = "localhost:8080"
        mock_settings.qbittorrent_username = None
        mock_settings.qbittorrent_password = None
        mock_get_settings.return_value = mock_settings

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'username and password' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.post')
    @patch.object(Settings, 'get_settings')
    async def test_qbittorrent_timeout(self, mock_get_settings, mock_requests_post):
        """Test qBittorrent health check handles timeout."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.qbittorrent_host = "localhost:8080"
        mock_settings.qbittorrent_username = "admin"
        mock_settings.qbittorrent_password = "pass"
        mock_get_settings.return_value = mock_settings

        # Mock timeout exception
        mock_requests_post.side_effect = requests.exceptions.Timeout("Connection timeout")

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'timeout' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.post')
    @patch.object(Settings, 'get_settings')
    async def test_qbittorrent_connection_error(self, mock_get_settings, mock_requests_post):
        """Test qBittorrent health check handles connection error."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.qbittorrent_host = "localhost:8080"
        mock_settings.qbittorrent_username = "admin"
        mock_settings.qbittorrent_password = "pass"
        mock_get_settings.return_value = mock_settings

        # Mock connection error
        mock_requests_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'cannot connect' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.post')
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_qbittorrent_host_without_protocol(self, mock_get_settings, mock_requests_get, mock_requests_post):
        """Test qBittorrent health check adds http:// prefix to host without protocol."""
        db = Mock(spec=Session)

        # Mock settings with host without protocol
        mock_settings = Mock(spec=Settings)
        mock_settings.qbittorrent_host = "localhost:8080"
        mock_settings.qbittorrent_username = "admin"
        mock_settings.qbittorrent_password = "pass"
        mock_get_settings.return_value = mock_settings

        # Mock successful responses
        mock_auth_response = Mock()
        mock_auth_response.status_code = 200
        mock_auth_response.text = "Ok."
        mock_auth_response.cookies = {'SID': 'test_session'}
        mock_requests_post.return_value = mock_auth_response

        mock_version_response = Mock()
        mock_version_response.status_code = 200
        mock_version_response.text = "v4.5.0"
        mock_requests_get.return_value = mock_version_response

        # Execute
        result = await test_connection(service="qbittorrent", db=db)

        # Verify http:// was added
        auth_call = mock_requests_post.call_args
        assert auth_call[0][0].startswith('http://')


class TestTMDBHealthCheck:
    """Test TMDB API health check functionality."""

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_tmdb_success(self, mock_get_settings, mock_requests_get):
        """Test TMDB health check succeeds with valid API key."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.tmdb_api_key = "valid_api_key_12345"
        mock_get_settings.return_value = mock_settings

        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'images': {'base_url': 'http://image.tmdb.org/t/p/'},
            'change_keys': ['adult', 'air_date']
        }
        mock_requests_get.return_value = mock_response

        # Execute
        result = await test_connection(service="tmdb", db=db)

        # Verify
        assert result['service'] == 'tmdb'
        assert result['status'] == 'success'
        assert 'valid' in result['message'].lower()

        # Verify API call
        mock_requests_get.assert_called_once()
        call_args = mock_requests_get.call_args
        assert 'https://api.themoviedb.org/3/authentication' in call_args[0]
        assert call_args[1]['headers']['Authorization'] == 'valid_api_key_12345'
        assert call_args[1]['headers']['accept'] == 'application/json'
        assert call_args[1]['timeout'] == 5

    @pytest.mark.asyncio
    @patch.object(Settings, 'get_settings')
    async def test_tmdb_empty_config(self, mock_get_settings):
        """Test TMDB health check fails with empty API key."""
        db = Mock(spec=Session)

        # Mock settings with no API key
        mock_settings = Mock(spec=Settings)
        mock_settings.tmdb_api_key = None
        mock_get_settings.return_value = mock_settings

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'configure' in exc_info.value.detail.lower()
        assert 'tmdb' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_tmdb_invalid_api_key(self, mock_get_settings, mock_requests_get):
        """Test TMDB health check fails with invalid API key (401)."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.tmdb_api_key = "invalid_key"
        mock_get_settings.return_value = mock_settings

        # Mock 401 Unauthorized response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_requests_get.return_value = mock_response

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'invalid' in exc_info.value.detail.lower()
        assert 'authentication failed' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_tmdb_timeout(self, mock_get_settings, mock_requests_get):
        """Test TMDB health check handles timeout."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.tmdb_api_key = "test_key"
        mock_get_settings.return_value = mock_settings

        # Mock timeout exception
        mock_requests_get.side_effect = requests.exceptions.Timeout("Connection timeout")

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'timeout' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_tmdb_connection_error(self, mock_get_settings, mock_requests_get):
        """Test TMDB health check handles connection error."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.tmdb_api_key = "test_key"
        mock_get_settings.return_value = mock_settings

        # Mock connection error
        mock_requests_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'cannot connect' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_tmdb_endpoint_not_found(self, mock_get_settings, mock_requests_get):
        """Test TMDB health check handles 404 endpoint not found."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.tmdb_api_key = "test_key"
        mock_get_settings.return_value = mock_settings

        # Mock 404 response
        mock_response = Mock()
        mock_response.status_code = 404
        mock_requests_get.return_value = mock_response

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'not found' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_tmdb_unexpected_status(self, mock_get_settings, mock_requests_get):
        """Test TMDB health check handles unexpected status code."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.tmdb_api_key = "test_key"
        mock_get_settings.return_value = mock_settings

        # Mock unexpected status code
        mock_response = Mock()
        mock_response.status_code = 503
        mock_requests_get.return_value = mock_response

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="tmdb", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'unexpected status' in exc_info.value.detail.lower()
        assert '503' in exc_info.value.detail


class TestUnknownService:
    """Test unknown service handling."""

    @pytest.mark.asyncio
    @patch.object(Settings, 'get_settings')
    async def test_unknown_service(self, mock_get_settings):
        """Test health check with unknown service name."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_get_settings.return_value = mock_settings

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="unknown_service", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'unknown service' in exc_info.value.detail.lower()
        assert 'unknown_service' in exc_info.value.detail


class TestHealthCheckEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_flaresolverr_trailing_slash_handling(self, mock_get_settings, mock_requests_get):
        """Test FlareSolverr URL trailing slash is properly handled."""
        db = Mock(spec=Session)

        # Mock settings with trailing slash
        mock_settings = Mock(spec=Settings)
        mock_settings.flaresolverr_url = "http://localhost:8191/"
        mock_get_settings.return_value = mock_settings

        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests_get.return_value = mock_response

        # Execute
        await test_connection(service="flaresolverr", db=db)

        # Verify trailing slash is removed
        call_args = mock_requests_get.call_args
        assert call_args[0][0] == 'http://localhost:8191'

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.post')
    @patch.object(Settings, 'get_settings')
    async def test_qbittorrent_unexpected_auth_response(self, mock_get_settings, mock_requests_post):
        """Test qBittorrent handles unexpected authentication response."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.qbittorrent_host = "localhost:8080"
        mock_settings.qbittorrent_username = "admin"
        mock_settings.qbittorrent_password = "pass"
        mock_get_settings.return_value = mock_settings

        # Mock unexpected authentication response
        mock_auth_response = Mock()
        mock_auth_response.status_code = 200
        mock_auth_response.text = "Unexpected response"
        mock_requests_post.return_value = mock_auth_response

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="qbittorrent", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'unexpected' in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch('backend.app.api.settings_routes.requests.get')
    @patch.object(Settings, 'get_settings')
    async def test_request_exception_generic_handling(self, mock_get_settings, mock_requests_get):
        """Test generic RequestException handling."""
        db = Mock(spec=Session)

        # Mock settings
        mock_settings = Mock(spec=Settings)
        mock_settings.flaresolverr_url = "http://localhost:8191"
        mock_get_settings.return_value = mock_settings

        # Mock generic request exception
        mock_requests_get.side_effect = requests.exceptions.RequestException("Generic error")

        # Execute and expect HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await test_connection(service="flaresolverr", db=db)

        # Verify
        assert exc_info.value.status_code == 400
        assert 'connection failed' in exc_info.value.detail.lower()
