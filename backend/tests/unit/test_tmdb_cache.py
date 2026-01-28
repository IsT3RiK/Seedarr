"""
Unit Tests for TMDB Cache Service

This test suite validates the TMDB cache service functionality including
cache-first lookup, API integration, and cache management.

Test Coverage:
    - TMDBCacheService initialization
    - Cache-first lookup strategy (hit/miss)
    - TMDB API fetching and response parsing
    - Cache storage and TTL management
    - Error handling (API errors, network failures, rate limiting)
    - Cache invalidation and cleanup
    - Cache statistics

Requirements:
    - pytest
    - pytest-asyncio (for async test support)
    - sqlalchemy (for database mocking)
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from backend.app.services.tmdb_cache_service import TMDBCacheService
from backend.app.services.exceptions import TrackerAPIError, NetworkRetryableError
from backend.app.models.tmdb_cache import TMDBCache
from backend.app.models.settings import Settings


class TestTMDBCacheServiceInitialization:
    """Test TMDBCacheService initialization."""

    def test_initialization(self):
        """Test TMDBCacheService initializes with database session."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        assert service.db == db
        assert isinstance(service, TMDBCacheService)
        assert service._api_key is None  # Not loaded until needed


class TestAPIKeyConfiguration:
    """Test TMDB API key configuration from Settings or environment."""

    @patch.object(Settings, 'get_settings')
    def test_api_key_from_settings(self, mock_get_settings):
        """Test API key loaded from Settings model."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock Settings with API key
        mock_settings = Mock(spec=Settings)
        mock_settings.tmdb_api_key = "test_api_key_from_settings"
        mock_get_settings.return_value = mock_settings

        api_key = service._get_api_key()

        assert api_key == "test_api_key_from_settings"
        assert service._api_key == "test_api_key_from_settings"  # Cached
        mock_get_settings.assert_called_once_with(db)

    @patch.object(Settings, 'get_settings')
    @patch.dict('os.environ', {'TMDB_API_KEY': 'test_env_api_key'})
    def test_api_key_from_environment(self, mock_get_settings):
        """Test API key fallback to environment variable."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock Settings returning None
        mock_get_settings.return_value = None

        api_key = service._get_api_key()

        assert api_key == "test_env_api_key"
        assert service._api_key == "test_env_api_key"

    @patch.object(Settings, 'get_settings')
    @patch.dict('os.environ', {}, clear=True)
    def test_api_key_not_configured(self, mock_get_settings):
        """Test error raised when API key not configured."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock Settings returning None
        mock_get_settings.return_value = None

        with pytest.raises(TrackerAPIError) as exc_info:
            service._get_api_key()

        assert "TMDB API key not configured" in str(exc_info.value)

    def test_api_key_cached_after_first_load(self):
        """Test API key is cached after first retrieval."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)
        service._api_key = "cached_key"

        # Should return cached key without calling Settings
        api_key = service._get_api_key()

        assert api_key == "cached_key"


class TestCacheTTLConfiguration:
    """Test cache TTL configuration from Settings."""

    @patch.object(Settings, 'get_settings')
    def test_ttl_from_settings(self, mock_get_settings):
        """Test TTL loaded from Settings model."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock Settings with custom TTL
        mock_settings = Mock(spec=Settings)
        mock_settings.tmdb_cache_ttl_days = 60
        mock_get_settings.return_value = mock_settings

        ttl = service._get_cache_ttl_days()

        assert ttl == 60
        mock_get_settings.assert_called_once_with(db)

    @patch.object(Settings, 'get_settings')
    def test_ttl_default_when_not_configured(self, mock_get_settings):
        """Test TTL defaults to 30 days when not configured."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock Settings returning None
        mock_get_settings.return_value = None

        ttl = service._get_cache_ttl_days()

        assert ttl == 30  # Default TTL

    @patch.object(Settings, 'get_settings')
    def test_ttl_default_when_settings_has_no_ttl(self, mock_get_settings):
        """Test TTL defaults to 30 when Settings has None for TTL."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock Settings with None TTL
        mock_settings = Mock(spec=Settings)
        mock_settings.tmdb_cache_ttl_days = None
        mock_get_settings.return_value = mock_settings

        ttl = service._get_cache_ttl_days()

        assert ttl == 30  # Default TTL


class TestCacheFirstLookup:
    """Test cache-first lookup strategy."""

    @pytest.mark.asyncio
    @patch.object(TMDBCache, 'get_cached')
    async def test_cache_hit_returns_cached_data(self, mock_get_cached):
        """Test cache hit returns cached data without API call."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock cached entry
        mock_cache_entry = Mock(spec=TMDBCache)
        mock_cache_entry.to_dict.return_value = {
            'tmdb_id': '550',
            'title': 'Fight Club',
            'year': 1999,
            'plot': 'An insomniac office worker...',
            'cast': [{'name': 'Brad Pitt', 'character': 'Tyler Durden'}],
            'ratings': {'vote_average': 8.8, 'vote_count': 25000},
            'cached_at': '2024-01-01T00:00:00',
            'expires_at': '2024-02-01T00:00:00'
        }
        mock_get_cached.return_value = mock_cache_entry

        # Execute
        metadata = await service.get_metadata("550")

        # Verify cache hit
        assert metadata['title'] == 'Fight Club'
        assert metadata['year'] == 1999
        mock_get_cached.assert_called_once_with(db, "550")
        mock_cache_entry.to_dict.assert_called_once()

    @pytest.mark.asyncio
    @patch.object(TMDBCache, 'get_cached')
    @patch.object(TMDBCacheService, '_fetch_from_api')
    @patch.object(TMDBCache, 'upsert')
    async def test_cache_miss_fetches_from_api(
        self, mock_upsert, mock_fetch_api, mock_get_cached
    ):
        """Test cache miss triggers API fetch and caching."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock cache miss
        mock_get_cached.return_value = None

        # Mock API response
        api_response = {
            'tmdb_id': '550',
            'title': 'Fight Club',
            'year': 1999,
            'plot': 'An insomniac office worker...',
            'cast': [{'name': 'Brad Pitt', 'character': 'Tyler Durden'}],
            'ratings': {'vote_average': 8.8, 'vote_count': 25000},
            'extra_data': {}
        }
        mock_fetch_api.return_value = api_response

        # Mock cache entry after upsert
        mock_cache_entry = Mock(spec=TMDBCache)
        mock_cache_entry.to_dict.return_value = {**api_response, 'cached_at': '2024-01-01T00:00:00', 'expires_at': '2024-02-01T00:00:00'}
        mock_upsert.return_value = mock_cache_entry

        # Execute
        metadata = await service.get_metadata("550")

        # Verify cache miss workflow
        mock_get_cached.assert_called_once_with(db, "550")
        mock_fetch_api.assert_called_once_with("550")
        mock_upsert.assert_called_once()
        assert metadata['title'] == 'Fight Club'

    @pytest.mark.asyncio
    @patch.object(TMDBCache, 'get_cached')
    @patch.object(TMDBCacheService, '_fetch_from_api')
    @patch.object(TMDBCache, 'upsert')
    async def test_force_refresh_bypasses_cache(
        self, mock_upsert, mock_fetch_api, mock_get_cached
    ):
        """Test force_refresh bypasses cache even if data exists."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock API response
        api_response = {
            'tmdb_id': '550',
            'title': 'Fight Club (Updated)',
            'year': 1999,
            'plot': 'Updated plot...',
            'cast': [],
            'ratings': {},
            'extra_data': {}
        }
        mock_fetch_api.return_value = api_response

        # Mock cache entry after upsert
        mock_cache_entry = Mock(spec=TMDBCache)
        mock_cache_entry.to_dict.return_value = {**api_response, 'cached_at': '2024-01-01T00:00:00', 'expires_at': '2024-02-01T00:00:00'}
        mock_upsert.return_value = mock_cache_entry

        # Execute with force_refresh=True
        metadata = await service.get_metadata("550", force_refresh=True)

        # Verify cache was NOT checked
        mock_get_cached.assert_not_called()
        # Verify API was called
        mock_fetch_api.assert_called_once_with("550")
        # Verify cache was updated
        mock_upsert.assert_called_once()
        assert metadata['title'] == 'Fight Club (Updated)'


class TestAPIFetching:
    """Test TMDB API fetching and response parsing."""

    @pytest.mark.asyncio
    @patch('backend.app.services.tmdb_cache_service.requests.get')
    @patch.object(TMDBCacheService, '_get_api_key')
    async def test_successful_api_fetch(self, mock_get_api_key, mock_requests_get):
        """Test successful API fetch and response parsing."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        mock_get_api_key.return_value = "test_api_key"

        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'id': 550,
            'title': 'Fight Club',
            'release_date': '1999-10-15',
            'overview': 'An insomniac office worker...',
            'vote_average': 8.8,
            'vote_count': 25000,
            'original_title': 'Fight Club',
            'original_language': 'en',
            'runtime': 139,
            'genres': [{'name': 'Drama'}],
            'credits': {
                'cast': [
                    {'name': 'Brad Pitt', 'character': 'Tyler Durden'},
                    {'name': 'Edward Norton', 'character': 'The Narrator'}
                ]
            }
        }
        mock_requests_get.return_value = mock_response

        # Execute
        metadata = await service._fetch_from_api("550")

        # Verify API call
        mock_requests_get.assert_called_once()
        call_args = mock_requests_get.call_args
        assert 'https://api.themoviedb.org/3/movie/550' in call_args[0]
        assert call_args[1]['params']['api_key'] == 'test_api_key'
        assert call_args[1]['timeout'] == 10

        # Verify response parsing
        assert metadata['tmdb_id'] == '550'
        assert metadata['title'] == 'Fight Club'
        assert metadata['year'] == 1999
        assert metadata['plot'] == 'An insomniac office worker...'
        assert len(metadata['cast']) == 2
        assert metadata['cast'][0]['name'] == 'Brad Pitt'
        assert metadata['ratings']['vote_average'] == 8.8
        assert metadata['extra_data']['runtime'] == 139

    @pytest.mark.asyncio
    @patch('backend.app.services.tmdb_cache_service.requests.get')
    @patch.object(TMDBCacheService, '_get_api_key')
    async def test_api_fetch_movie_not_found(self, mock_get_api_key, mock_requests_get):
        """Test API fetch with 404 (movie not found)."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        mock_get_api_key.return_value = "test_api_key"

        # Mock 404 response
        mock_response = Mock()
        mock_response.status_code = 404
        mock_requests_get.return_value = mock_response

        # Execute and expect TrackerAPIError
        with pytest.raises(TrackerAPIError) as exc_info:
            await service._fetch_from_api("999999")

        assert "not found" in str(exc_info.value).lower()
        assert "999999" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('backend.app.services.tmdb_cache_service.requests.get')
    @patch.object(TMDBCacheService, '_get_api_key')
    async def test_api_fetch_authentication_error(self, mock_get_api_key, mock_requests_get):
        """Test API fetch with 401 (authentication failed)."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        mock_get_api_key.return_value = "invalid_api_key"

        # Mock 401 response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_requests_get.return_value = mock_response

        # Execute and expect TrackerAPIError
        with pytest.raises(TrackerAPIError) as exc_info:
            await service._fetch_from_api("550")

        assert "authentication failed" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    @patch('backend.app.services.tmdb_cache_service.requests.get')
    @patch.object(TMDBCacheService, '_get_api_key')
    async def test_api_fetch_rate_limit(self, mock_get_api_key, mock_requests_get):
        """Test API fetch with 429 (rate limit) - retryable."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        mock_get_api_key.return_value = "test_api_key"

        # Mock 429 response
        mock_response = Mock()
        mock_response.status_code = 429
        mock_requests_get.return_value = mock_response

        # Execute and expect NetworkRetryableError
        with pytest.raises(NetworkRetryableError) as exc_info:
            await service._fetch_from_api("550")

        assert "rate limit" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    @patch('backend.app.services.tmdb_cache_service.requests.get')
    @patch.object(TMDBCacheService, '_get_api_key')
    async def test_api_fetch_server_error(self, mock_get_api_key, mock_requests_get):
        """Test API fetch with 500 (server error) - retryable."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        mock_get_api_key.return_value = "test_api_key"

        # Mock 500 response
        mock_response = Mock()
        mock_response.status_code = 500
        mock_requests_get.return_value = mock_response

        # Execute and expect NetworkRetryableError
        with pytest.raises(NetworkRetryableError) as exc_info:
            await service._fetch_from_api("550")

        assert "server error" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    @patch('backend.app.services.tmdb_cache_service.requests.get')
    @patch.object(TMDBCacheService, '_get_api_key')
    async def test_api_fetch_timeout(self, mock_get_api_key, mock_requests_get):
        """Test API fetch with timeout error - retryable."""
        import requests
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        mock_get_api_key.return_value = "test_api_key"

        # Mock timeout exception
        mock_requests_get.side_effect = requests.exceptions.Timeout("Connection timeout")

        # Execute and expect NetworkRetryableError
        with pytest.raises(NetworkRetryableError) as exc_info:
            await service._fetch_from_api("550")

        assert "timeout" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    @patch('backend.app.services.tmdb_cache_service.requests.get')
    @patch.object(TMDBCacheService, '_get_api_key')
    async def test_api_fetch_connection_error(self, mock_get_api_key, mock_requests_get):
        """Test API fetch with connection error - retryable."""
        import requests
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        mock_get_api_key.return_value = "test_api_key"

        # Mock connection exception
        mock_requests_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        # Execute and expect NetworkRetryableError
        with pytest.raises(NetworkRetryableError) as exc_info:
            await service._fetch_from_api("550")

        assert "connection error" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    @patch('backend.app.services.tmdb_cache_service.requests.get')
    @patch.object(TMDBCacheService, '_get_api_key')
    async def test_api_fetch_unexpected_error(self, mock_get_api_key, mock_requests_get):
        """Test API fetch with unexpected error."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        mock_get_api_key.return_value = "test_api_key"

        # Mock unexpected exception
        mock_requests_get.side_effect = ValueError("Unexpected error")

        # Execute and expect TrackerAPIError
        with pytest.raises(TrackerAPIError) as exc_info:
            await service._fetch_from_api("550")

        assert "unexpected error" in str(exc_info.value).lower()


class TestCacheInvalidation:
    """Test cache invalidation functionality."""

    def test_invalidate_existing_cache(self):
        """Test invalidation of existing cache entry."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock cache entry exists
        mock_cache_entry = Mock(spec=TMDBCache)
        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = mock_cache_entry
        db.query.return_value = mock_query

        # Execute
        result = service.invalidate_cache("550")

        # Verify deletion
        assert result is True
        db.delete.assert_called_once_with(mock_cache_entry)
        db.commit.assert_called_once()

    def test_invalidate_nonexistent_cache(self):
        """Test invalidation when cache entry doesn't exist."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock no cache entry
        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = None
        db.query.return_value = mock_query

        # Execute
        result = service.invalidate_cache("550")

        # Verify no deletion
        assert result is False
        db.delete.assert_not_called()
        db.commit.assert_not_called()


class TestCacheCleanup:
    """Test cache cleanup functionality."""

    @patch.object(TMDBCache, 'cleanup_expired')
    def test_cleanup_expired_entries(self, mock_cleanup):
        """Test cleanup of expired cache entries."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock cleanup returns count
        mock_cleanup.return_value = 42

        # Execute
        deleted_count = service.cleanup_expired()

        # Verify
        assert deleted_count == 42
        mock_cleanup.assert_called_once_with(db)


class TestCacheStatistics:
    """Test cache statistics functionality."""

    def test_get_cache_stats(self):
        """Test retrieval of cache statistics."""
        db = Mock(spec=Session)
        service = TMDBCacheService(db)

        # Mock total count
        mock_query_total = Mock()
        mock_query_total.count.return_value = 100

        # Mock expired count
        mock_query_expired = Mock()
        mock_query_expired.filter.return_value.count.return_value = 10

        # Setup query mock to return different results
        db.query.side_effect = [mock_query_total, mock_query_expired]

        # Execute
        stats = service.get_cache_stats()

        # Verify
        assert stats['total_entries'] == 100
        assert stats['expired_entries'] == 10
        assert stats['valid_entries'] == 90


class TestMediaAnalyzerIntegration:
    """Test MediaAnalyzer integration with TMDB cache."""

    @pytest.mark.asyncio
    @patch.object(TMDBCacheService, 'get_metadata')
    async def test_validate_tmdb_metadata_success(self, mock_get_metadata):
        """Test MediaAnalyzer.validate_tmdb_metadata delegates to cache service."""
        from backend.app.services.media_analyzer import MediaAnalyzer

        db = Mock(spec=Session)
        analyzer = MediaAnalyzer(db)

        # Mock cache service response
        mock_metadata = {
            'tmdb_id': '550',
            'title': 'Fight Club',
            'year': 1999,
            'plot': 'An insomniac office worker...'
        }
        mock_get_metadata.return_value = mock_metadata

        # Execute
        metadata = await analyzer.validate_tmdb_metadata("550")

        # Verify delegation
        assert metadata == mock_metadata
        mock_get_metadata.assert_called_once_with("550", False)

    @pytest.mark.asyncio
    @patch.object(TMDBCacheService, 'get_metadata')
    async def test_validate_tmdb_metadata_with_force_refresh(self, mock_get_metadata):
        """Test MediaAnalyzer.validate_tmdb_metadata with force_refresh."""
        from backend.app.services.media_analyzer import MediaAnalyzer

        db = Mock(spec=Session)
        analyzer = MediaAnalyzer(db)

        # Mock cache service response
        mock_metadata = {'tmdb_id': '550', 'title': 'Fight Club'}
        mock_get_metadata.return_value = mock_metadata

        # Execute with force_refresh
        metadata = await analyzer.validate_tmdb_metadata("550", force_refresh=True)

        # Verify force_refresh passed through
        assert metadata == mock_metadata
        mock_get_metadata.assert_called_once_with("550", True)

    @pytest.mark.asyncio
    @patch.object(TMDBCacheService, 'get_metadata')
    async def test_validate_tmdb_metadata_error_handling(self, mock_get_metadata):
        """Test MediaAnalyzer.validate_tmdb_metadata error handling."""
        from backend.app.services.media_analyzer import MediaAnalyzer

        db = Mock(spec=Session)
        analyzer = MediaAnalyzer(db)

        # Mock cache service raises error
        mock_get_metadata.side_effect = TrackerAPIError("TMDB API failed")

        # Execute and expect error
        with pytest.raises(TrackerAPIError):
            await analyzer.validate_tmdb_metadata("550")
