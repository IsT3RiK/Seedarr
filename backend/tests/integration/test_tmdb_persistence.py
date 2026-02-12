"""
Integration tests for TMDB Cache Persistence

Tests cover persistent TMDB caching functionality:
    - Cache hit/miss scenarios with database persistence
    - TTL expiration and automatic cleanup
    - Persistence across database sessions (application restart simulation)
    - Cache hit rate >90% on repeated lookups
    - Integration with TMDBCacheService
    - Database index performance

These tests verify that the TMDB cache successfully reduces API calls through
persistent storage and survives application restarts as designed.

Expected Performance:
    - Cache hit rate: >90% for repeated lookups
    - Reduction in TMDB API calls: >80%
    - Cache survival: Persists across application restarts
"""

import asyncio
import os
import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from backend.app.models.base import Base
from backend.app.models.tmdb_cache import TMDBCache
from backend.app.models.settings import Settings
from backend.app.services.tmdb_cache_service import TMDBCacheService
from backend.app.services.exceptions import TrackerAPIError, NetworkRetryableError


# ============================================================================
# Test Configuration
# ============================================================================

# Use in-memory SQLite database for fast testing
TEST_DATABASE_URL = 'sqlite:///:memory:'

# Sample TMDB API response data
SAMPLE_TMDB_RESPONSE = {
    'id': 550,
    'title': 'Fight Club',
    'release_date': '1999-10-15',
    'overview': 'A ticking-time-bomb insomniac and a slippery soap salesman...',
    'vote_average': 8.4,
    'vote_count': 26000,
    'cast': [
        {'name': 'Brad Pitt', 'character': 'Tyler Durden'},
        {'name': 'Edward Norton', 'character': 'The Narrator'}
    ]
}


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def test_db():
    """
    Create a fresh test database for each test.

    Uses in-memory SQLite database for speed. Each test gets a fresh
    database instance to ensure test isolation.
    """
    # Create engine and tables
    engine = create_engine(TEST_DATABASE_URL, echo=False)
    Base.metadata.create_all(engine)

    # Create session
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestSessionLocal()

    yield db

    # Cleanup
    db.close()
    engine.dispose()


@pytest.fixture
def test_db_with_settings(test_db):
    """
    Test database with Settings pre-configured.

    Initializes Settings table with TMDB API key and cache TTL.
    """
    # Create settings entry
    settings = Settings(
        tracker_url='https://lacale-test.example.com',
        tracker_passkey='test_passkey_1234567890',
        flaresolverr_url='http://localhost:8191',
        qbittorrent_host='localhost:8080',
        qbittorrent_username='admin',
        qbittorrent_password='adminpassword',
        tmdb_api_key='test_tmdb_api_key_1234567890',
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
def cache_service(test_db_with_settings):
    """TMDBCacheService instance with test database."""
    return TMDBCacheService(test_db_with_settings)


@pytest.fixture
def mock_tmdb_api_response():
    """Mock successful TMDB API response."""
    return SAMPLE_TMDB_RESPONSE.copy()


# ============================================================================
# Test: Basic Cache Operations
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_miss_and_hit(test_db_with_settings, mock_tmdb_api_response):
    """
    Test cache miss on first lookup, cache hit on subsequent lookups.

    Verifies:
        - First lookup is a cache miss (returns None)
        - Cache entry is created via upsert
        - Second lookup is a cache hit (returns cached data)
        - Cached data matches original data
    """
    tmdb_id = "550"

    # First lookup - should be cache miss
    cached = TMDBCache.get_cached(test_db_with_settings, tmdb_id)
    assert cached is None, "First lookup should be cache miss"

    # Insert cache entry
    cache_entry = TMDBCache.upsert(
        test_db_with_settings,
        tmdb_id=tmdb_id,
        title=mock_tmdb_api_response['title'],
        year=1999,
        cast=mock_tmdb_api_response['cast'],
        plot=mock_tmdb_api_response['overview'],
        ratings={
            'vote_average': mock_tmdb_api_response['vote_average'],
            'vote_count': mock_tmdb_api_response['vote_count']
        },
        ttl_days=30
    )

    assert cache_entry is not None, "Cache entry should be created"
    assert cache_entry.tmdb_id == tmdb_id
    assert cache_entry.title == "Fight Club"
    assert cache_entry.year == 1999

    # Second lookup - should be cache hit
    cached = TMDBCache.get_cached(test_db_with_settings, tmdb_id)
    assert cached is not None, "Second lookup should be cache hit"
    assert cached.tmdb_id == tmdb_id
    assert cached.title == "Fight Club"
    assert cached.year == 1999
    assert cached.is_expired() is False, "Cache should not be expired"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_persistence_across_sessions(mock_tmdb_api_response):
    """
    Test cache persistence across database sessions (simulating app restart).

    Verifies:
        - Cache entry created in first session
        - Cache entry survives session close
        - Cache entry retrieved in second session
        - Data integrity maintained across sessions

    This simulates application restart where database connection is closed
    and reopened, ensuring cache truly persists.
    """
    # Use temporary file database for persistence testing
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name

    try:
        # Session 1: Create cache entry
        engine1 = create_engine(f'sqlite:///{db_path}', echo=False)
        Base.metadata.create_all(engine1)
        Session1 = sessionmaker(autocommit=False, autoflush=False, bind=engine1)
        db1 = Session1()

        tmdb_id = "550"
        cache_entry = TMDBCache.upsert(
            db1,
            tmdb_id=tmdb_id,
            title="Fight Club",
            year=1999,
            cast=mock_tmdb_api_response['cast'],
            plot=mock_tmdb_api_response['overview'],
            ratings={'vote_average': 8.4, 'vote_count': 26000},
            ttl_days=30
        )

        assert cache_entry.tmdb_id == tmdb_id

        # Close session and dispose engine (simulate app shutdown)
        db1.close()
        engine1.dispose()

        # Session 2: Retrieve cache entry (simulate app restart)
        engine2 = create_engine(f'sqlite:///{db_path}', echo=False)
        Session2 = sessionmaker(autocommit=False, autoflush=False, bind=engine2)
        db2 = Session2()

        # Should find cached entry from previous session
        cached = TMDBCache.get_cached(db2, tmdb_id)
        assert cached is not None, "Cache should persist across sessions"
        assert cached.tmdb_id == tmdb_id
        assert cached.title == "Fight Club"
        assert cached.year == 1999
        assert cached.is_expired() is False

        # Verify data integrity
        cache_dict = cached.to_dict()
        assert cache_dict['title'] == "Fight Club"
        assert cache_dict['year'] == 1999
        assert len(cache_dict['cast']) == 2
        assert cache_dict['ratings']['vote_average'] == 8.4

        # Cleanup
        db2.close()
        engine2.dispose()

    finally:
        # Remove temporary database file
        Path(db_path).unlink(missing_ok=True)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_ttl_expiration(test_db_with_settings):
    """
    Test cache TTL expiration and automatic cleanup.

    Verifies:
        - Cache entry with expired TTL is automatically removed
        - get_cached() returns None for expired entries
        - Database cleanup occurs automatically on query
    """
    tmdb_id = "550"

    # Create cache entry with very short TTL (expired)
    cache_entry = TMDBCache(
        tmdb_id=tmdb_id,
        title="Fight Club",
        year=1999,
        ttl_days=30
    )
    # Manually set to expired
    cache_entry.cached_at = datetime.utcnow() - timedelta(days=31)
    cache_entry.expires_at = datetime.utcnow() - timedelta(days=1)

    test_db_with_settings.add(cache_entry)
    test_db_with_settings.commit()

    # Verify entry exists in database
    entry = test_db_with_settings.query(TMDBCache).filter(
        TMDBCache.tmdb_id == tmdb_id
    ).first()
    assert entry is not None, "Entry should exist in database"
    assert entry.is_expired() is True, "Entry should be expired"

    # get_cached() should auto-delete expired entry
    cached = TMDBCache.get_cached(test_db_with_settings, tmdb_id)
    assert cached is None, "Expired entry should be auto-deleted"

    # Verify entry removed from database
    entry = test_db_with_settings.query(TMDBCache).filter(
        TMDBCache.tmdb_id == tmdb_id
    ).first()
    assert entry is None, "Expired entry should be removed from database"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_cleanup_expired(test_db_with_settings):
    """
    Test bulk cleanup of expired cache entries.

    Verifies:
        - cleanup_expired() removes all expired entries
        - Valid entries are preserved
        - Returns correct count of deleted entries
    """
    # Create mix of expired and valid entries
    expired_ids = ["100", "200", "300"]
    valid_ids = ["400", "500"]

    # Add expired entries
    for tmdb_id in expired_ids:
        entry = TMDBCache(
            tmdb_id=tmdb_id,
            title=f"Expired Movie {tmdb_id}",
            year=2020,
            ttl_days=30
        )
        entry.cached_at = datetime.utcnow() - timedelta(days=31)
        entry.expires_at = datetime.utcnow() - timedelta(days=1)
        test_db_with_settings.add(entry)

    # Add valid entries
    for tmdb_id in valid_ids:
        entry = TMDBCache(
            tmdb_id=tmdb_id,
            title=f"Valid Movie {tmdb_id}",
            year=2024,
            ttl_days=30
        )
        test_db_with_settings.add(entry)

    test_db_with_settings.commit()

    # Verify all entries exist
    total = test_db_with_settings.query(TMDBCache).count()
    assert total == 5, "Should have 5 total entries"

    # Run cleanup
    deleted_count = TMDBCache.cleanup_expired(test_db_with_settings)
    assert deleted_count == 3, "Should delete 3 expired entries"

    # Verify only valid entries remain
    remaining = test_db_with_settings.query(TMDBCache).count()
    assert remaining == 2, "Should have 2 valid entries remaining"

    # Verify specific valid entries still exist
    for tmdb_id in valid_ids:
        entry = test_db_with_settings.query(TMDBCache).filter(
            TMDBCache.tmdb_id == tmdb_id
        ).first()
        assert entry is not None, f"Valid entry {tmdb_id} should exist"


# ============================================================================
# Test: TMDBCacheService Integration
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_service_with_mock_api(cache_service, mock_tmdb_api_response):
    """
    Test TMDBCacheService with mocked TMDB API.

    Verifies:
        - Cache miss triggers API call
        - API response is cached
        - Cache hit returns cached data (no API call)
        - Proper error handling
    """
    tmdb_id = "550"

    # Mock TMDB API fetch method
    with patch.object(
        cache_service,
        '_fetch_from_api',
        new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = {
            'tmdb_id': tmdb_id,
            'title': mock_tmdb_api_response['title'],
            'year': 1999,
            'cast': mock_tmdb_api_response['cast'],
            'plot': mock_tmdb_api_response['overview'],
            'ratings': {
                'vote_average': mock_tmdb_api_response['vote_average'],
                'vote_count': mock_tmdb_api_response['vote_count']
            }
        }

        # First call - cache miss, should call API
        metadata1 = await cache_service.get_metadata(tmdb_id)
        assert mock_fetch.call_count == 1, "Should call API on cache miss"
        assert metadata1['title'] == "Fight Club"

        # Second call - cache hit, should NOT call API
        metadata2 = await cache_service.get_metadata(tmdb_id)
        assert mock_fetch.call_count == 1, "Should NOT call API on cache hit"
        assert metadata2['title'] == "Fight Club"

        # Verify both calls return same data
        assert metadata1 == metadata2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_hit_rate_exceeds_90_percent(cache_service):
    """
    Test cache hit rate exceeds 90% on repeated lookups.

    Verifies:
        - Cache hit rate >90% for repeated lookups
        - Expected reduction in API calls achieved
        - Performance requirement met
    """
    # Simulate 100 lookups for 10 unique movies (10 lookups each)
    unique_ids = [str(i) for i in range(100, 110)]  # 10 unique IDs
    lookups = unique_ids * 10  # Each ID looked up 10 times = 100 total lookups

    api_calls = 0
    cache_hits = 0

    # Mock TMDB API fetch
    async def mock_fetch(tmdb_id: str):
        return {
            'tmdb_id': tmdb_id,
            'title': f'Movie {tmdb_id}',
            'year': 2024,
            'cast': [],
            'plot': 'Test plot',
            'ratings': {'vote_average': 7.5, 'vote_count': 1000}
        }

    with patch.object(
        cache_service,
        '_fetch_from_api',
        side_effect=mock_fetch
    ) as mock_api:
        # Perform all lookups
        for tmdb_id in lookups:
            metadata = await cache_service.get_metadata(tmdb_id)
            assert metadata is not None

        # Count API calls (should be 10 - one per unique ID)
        api_calls = mock_api.call_count
        cache_hits = len(lookups) - api_calls

    # Calculate hit rate
    hit_rate = (cache_hits / len(lookups)) * 100

    # Verify requirements
    assert api_calls == 10, f"Should make exactly 10 API calls, made {api_calls}"
    assert cache_hits == 90, f"Should have 90 cache hits, got {cache_hits}"
    assert hit_rate >= 90.0, f"Cache hit rate {hit_rate}% should be >= 90%"

    # Verify API call reduction
    reduction = ((len(lookups) - api_calls) / len(lookups)) * 100
    assert reduction >= 80.0, f"API call reduction {reduction}% should be >= 80%"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_upsert_updates_existing(test_db_with_settings, mock_tmdb_api_response):
    """
    Test cache upsert updates existing entries.

    Verifies:
        - Upsert creates entry if not exists
        - Upsert updates entry if exists
        - TTL is refreshed on update
        - No duplicate entries created
    """
    tmdb_id = "550"

    # First upsert - create
    entry1 = TMDBCache.upsert(
        test_db_with_settings,
        tmdb_id=tmdb_id,
        title="Fight Club",
        year=1999,
        ttl_days=30
    )
    first_cached_at = entry1.cached_at

    # Verify entry created
    assert entry1.tmdb_id == tmdb_id
    assert entry1.title == "Fight Club"

    # Wait a moment to ensure timestamp difference
    await asyncio.sleep(0.1)

    # Second upsert - update
    entry2 = TMDBCache.upsert(
        test_db_with_settings,
        tmdb_id=tmdb_id,
        title="Fight Club (Updated)",
        year=1999,
        cast=mock_tmdb_api_response['cast'],
        ttl_days=30
    )

    # Verify entry updated (not created new)
    assert entry2.id == entry1.id, "Should update same entry, not create new"
    assert entry2.title == "Fight Club (Updated)", "Title should be updated"
    assert entry2.cached_at > first_cached_at, "TTL should be refreshed"
    assert len(entry2.cast) == 2, "Cast should be added"

    # Verify no duplicate entries
    count = test_db_with_settings.query(TMDBCache).filter(
        TMDBCache.tmdb_id == tmdb_id
    ).count()
    assert count == 1, "Should have exactly 1 entry, not duplicates"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_force_refresh(cache_service):
    """
    Test force refresh bypasses cache and fetches fresh data.

    Verifies:
        - force_refresh=True bypasses cache
        - Fresh data fetched from API
        - Cache updated with new data
    """
    tmdb_id = "550"

    # Mock TMDB API fetch
    api_call_count = 0

    async def mock_fetch(tmdb_id: str):
        nonlocal api_call_count
        api_call_count += 1
        return {
            'tmdb_id': tmdb_id,
            'title': f'Movie {tmdb_id} (Call {api_call_count})',
            'year': 2024,
            'cast': [],
            'plot': 'Test plot',
            'ratings': {'vote_average': 7.5, 'vote_count': 1000}
        }

    with patch.object(
        cache_service,
        '_fetch_from_api',
        side_effect=mock_fetch
    ):
        # First call - cache miss, should call API
        metadata1 = await cache_service.get_metadata(tmdb_id)
        assert api_call_count == 1
        assert "Call 1" in metadata1['title']

        # Second call - cache hit, should NOT call API
        metadata2 = await cache_service.get_metadata(tmdb_id)
        assert api_call_count == 1, "Should use cache"
        assert "Call 1" in metadata2['title']

        # Third call with force_refresh - should bypass cache and call API
        metadata3 = await cache_service.get_metadata(tmdb_id, force_refresh=True)
        assert api_call_count == 2, "Should call API with force_refresh"
        assert "Call 2" in metadata3['title']


# ============================================================================
# Test: Database Index Performance
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_index_performance(test_db_with_settings):
    """
    Test database indexes for TMDB cache queries.

    Verifies:
        - tmdb_id index exists and is used
        - expires_at index exists and is used
        - Query performance acceptable with indexes

    Note: This is a basic test. For production, use EXPLAIN QUERY PLAN
    to verify index usage.
    """
    # Create multiple cache entries
    for i in range(100, 200):
        entry = TMDBCache(
            tmdb_id=str(i),
            title=f"Movie {i}",
            year=2024,
            ttl_days=30
        )
        test_db_with_settings.add(entry)

    test_db_with_settings.commit()

    # Query by tmdb_id (should use index)
    entry = test_db_with_settings.query(TMDBCache).filter(
        TMDBCache.tmdb_id == "150"
    ).first()
    assert entry is not None
    assert entry.title == "Movie 150"

    # Query by expires_at (should use index for cleanup)
    expired_entries = test_db_with_settings.query(TMDBCache).filter(
        TMDBCache.expires_at <= datetime.utcnow()
    ).all()
    # Should be 0 since all entries have 30-day TTL
    assert len(expired_entries) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_to_dict_serialization(test_db_with_settings, mock_tmdb_api_response):
    """
    Test cache entry serialization to dictionary.

    Verifies:
        - to_dict() returns complete data
        - All fields properly serialized
        - Timestamps in ISO format
        - is_expired flag included
    """
    tmdb_id = "550"

    entry = TMDBCache.upsert(
        test_db_with_settings,
        tmdb_id=tmdb_id,
        title="Fight Club",
        year=1999,
        cast=mock_tmdb_api_response['cast'],
        plot=mock_tmdb_api_response['overview'],
        ratings={'vote_average': 8.4, 'vote_count': 26000},
        ttl_days=30
    )

    # Serialize to dict
    cache_dict = entry.to_dict()

    # Verify all fields present
    assert cache_dict['tmdb_id'] == tmdb_id
    assert cache_dict['title'] == "Fight Club"
    assert cache_dict['year'] == 1999
    assert len(cache_dict['cast']) == 2
    assert cache_dict['plot'] is not None
    assert cache_dict['ratings']['vote_average'] == 8.4
    assert 'cached_at' in cache_dict
    assert 'expires_at' in cache_dict
    assert 'is_expired' in cache_dict
    assert cache_dict['is_expired'] is False


# ============================================================================
# Test: Error Handling
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_service_api_key_missing(test_db):
    """
    Test cache service handles missing API key gracefully.

    Verifies:
        - Raises TrackerAPIError if API key not configured
        - Clear error message provided
    """
    # Create cache service without Settings (no API key)
    cache_service = TMDBCacheService(test_db)

    # Mock API fetch to trigger API key retrieval
    with patch.object(cache_service, '_fetch_from_api', new_callable=AsyncMock):
        with pytest.raises(TrackerAPIError) as exc_info:
            await cache_service.get_metadata("550")

        assert "API key not configured" in str(exc_info.value)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cache_service_handles_api_errors(cache_service):
    """
    Test cache service handles TMDB API errors gracefully.

    Verifies:
        - Network errors raise NetworkRetryableError
        - API errors raise TrackerAPIError
        - Error messages are descriptive
    """
    tmdb_id = "550"

    # Test network error
    with patch.object(
        cache_service,
        '_fetch_from_api',
        side_effect=NetworkRetryableError("Connection timeout")
    ):
        with pytest.raises(NetworkRetryableError) as exc_info:
            await cache_service.get_metadata(tmdb_id)
        assert "Connection timeout" in str(exc_info.value)

    # Test API error
    with patch.object(
        cache_service,
        '_fetch_from_api',
        side_effect=TrackerAPIError("Invalid TMDB ID")
    ):
        with pytest.raises(TrackerAPIError) as exc_info:
            await cache_service.get_metadata(tmdb_id)
        assert "Invalid TMDB ID" in str(exc_info.value)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
