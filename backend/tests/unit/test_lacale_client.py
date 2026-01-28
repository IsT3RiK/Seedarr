"""
Unit tests for LaCaleClient

Tests cover:
    - Initialization and configuration
    - Multipart data preparation with CRITICAL repeated tags fields
    - Torrent upload with validation and error handling
    - Tag and category fetching
    - Passkey validation
    - Retry logic integration with exponential backoff
    - HTTP error classification (retryable vs non-retryable)
    - Timeout and connection error handling
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, mock_open
import requests
from requests import Session

from backend.app.services.lacale_client import LaCaleClient
from backend.app.services.exceptions import (
    TrackerAPIError,
    NetworkRetryableError
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tracker_url():
    """Test tracker URL."""
    return "https://lacale.example.com"


@pytest.fixture
def passkey():
    """Test passkey."""
    return "test_passkey_12345"


@pytest.fixture
def client(tracker_url, passkey):
    """Create LaCaleClient instance for testing."""
    return LaCaleClient(tracker_url=tracker_url, passkey=passkey)


@pytest.fixture
def mock_session():
    """Mock authenticated session."""
    return Mock(spec=Session)


@pytest.fixture
def mock_upload_success_response():
    """Mock successful upload response."""
    return {
        'success': True,
        'torrent_id': '12345',
        'torrent_url': 'https://lacale.example.com/torrents/12345',
        'message': 'Upload successful'
    }


@pytest.fixture
def mock_tags_response():
    """Mock tags API response."""
    return {
        'tags': [
            {
                'id': '10',
                'name': 'BluRay',
                'category': 'Quality',
                'description': 'BluRay source'
            },
            {
                'id': '15',
                'name': '1080p',
                'category': 'Resolution',
                'description': 'Full HD 1080p'
            },
            {
                'id': '20',
                'name': 'x264',
                'category': 'Codec',
                'description': 'H.264/AVC codec'
            }
        ]
    }


@pytest.fixture
def mock_categories_response():
    """Mock categories API response."""
    return {
        'categories': [
            {
                'id': '1',
                'name': 'Movies',
                'description': 'Feature films'
            },
            {
                'id': '2',
                'name': 'TV Shows',
                'description': 'Television series'
            }
        ]
    }


# ============================================================================
# Initialization Tests
# ============================================================================

def test_initialization(tracker_url, passkey):
    """Test LaCaleClient initialization."""
    client = LaCaleClient(tracker_url=tracker_url, passkey=passkey)

    assert client.tracker_url == tracker_url
    assert client.passkey == passkey
    assert client.upload_endpoint == f"{tracker_url}/api/upload"
    assert client.tags_endpoint == f"{tracker_url}/api/tags"
    assert client.categories_endpoint == f"{tracker_url}/api/categories"


def test_initialization_strips_trailing_slash():
    """Test that trailing slash is removed from URL."""
    client = LaCaleClient(
        tracker_url="https://lacale.example.com/",
        passkey="test_passkey"
    )

    assert client.tracker_url == "https://lacale.example.com"
    assert client.upload_endpoint == "https://lacale.example.com/api/upload"


def test_repr(client):
    """Test string representation."""
    repr_str = repr(client)

    assert "LaCaleClient" in repr_str
    assert "https://lacale.example.com" in repr_str
    assert "***2345" in repr_str  # Last 4 chars of passkey
    assert "test_passkey_12345" not in repr_str  # Full passkey not shown


def test_repr_with_none_passkey():
    """Test repr when passkey is None."""
    client = LaCaleClient(tracker_url="https://test.com", passkey="")

    repr_str = repr(client)
    assert "***None" in repr_str


# ============================================================================
# _prepare_multipart_data Tests
# ============================================================================

def test_prepare_multipart_data_basic(client):
    """Test basic multipart data preparation with required fields."""
    data = client._prepare_multipart_data(
        release_name="Movie.2023.1080p.BluRay.x264",
        category_id="1",
        tag_ids=["10", "15", "20"]
    )

    # Verify required fields
    assert ('name', 'Movie.2023.1080p.BluRay.x264') in data
    assert ('category_id', '1') in data
    assert ('passkey', 'test_passkey_12345') in data

    # CRITICAL: Verify tags are REPEATED fields, not JSON array
    assert ('tags', '10') in data
    assert ('tags', '15') in data
    assert ('tags', '20') in data

    # Count tag occurrences
    tag_fields = [item for item in data if item[0] == 'tags']
    assert len(tag_fields) == 3


def test_prepare_multipart_data_with_optional_fields(client):
    """Test multipart data with all optional fields."""
    data = client._prepare_multipart_data(
        release_name="Movie.2023.1080p",
        category_id="1",
        tag_ids=["10"],
        description="Great movie plot summary",
        nfo_content="NFO content here",
        mediainfo="MediaInfo technical details"
    )

    # Verify optional fields are included
    assert ('description', 'Great movie plot summary') in data
    assert ('nfo', 'NFO content here') in data
    assert ('mediainfo', 'MediaInfo technical details') in data


def test_prepare_multipart_data_repeated_tags_pattern(client):
    """Test CRITICAL repeated tags fields pattern."""
    data = client._prepare_multipart_data(
        release_name="Test",
        category_id="1",
        tag_ids=["10", "15", "20", "25"]
    )

    # Verify each tag is a separate tuple
    tags_list = [item for item in data if item[0] == 'tags']
    assert len(tags_list) == 4
    assert tags_list[0] == ('tags', '10')
    assert tags_list[1] == ('tags', '15')
    assert tags_list[2] == ('tags', '20')
    assert tags_list[3] == ('tags', '25')

    # Verify tags are NOT in JSON format
    for field_name, field_value in data:
        if field_name == 'tags':
            assert isinstance(field_value, str)
            assert not field_value.startswith('[')  # Not JSON array


def test_prepare_multipart_data_empty_tags(client):
    """Test multipart data with empty tags list."""
    data = client._prepare_multipart_data(
        release_name="Test",
        category_id="1",
        tag_ids=[]
    )

    # Verify no tags fields are present
    tag_fields = [item for item in data if item[0] == 'tags']
    assert len(tag_fields) == 0


# ============================================================================
# upload_torrent Success Tests
# ============================================================================

@pytest.mark.asyncio
async def test_upload_torrent_success(client, mock_session, mock_upload_success_response):
    """Test successful torrent upload."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_upload_success_response

    torrent_data = b'torrent file content'

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        result = await client.upload_torrent(
            session=mock_session,
            torrent_data=torrent_data,
            release_name="Movie.2023.1080p.BluRay.x264",
            category_id="1",
            tag_ids=["10", "15", "20"],
            nfo_content="NFO content"
        )

        # Verify result
        assert result['success'] is True
        assert result['torrent_id'] == '12345'
        assert result['torrent_url'] == 'https://lacale.example.com/torrents/12345'
        assert result['message'] == 'Upload successful'
        assert 'response_data' in result

        # Verify to_thread was called with correct arguments
        assert mock_to_thread.called
        call_args = mock_to_thread.call_args
        assert call_args[0][0] == mock_session.post
        assert call_args[0][1] == client.upload_endpoint


@pytest.mark.asyncio
async def test_upload_torrent_with_screenshots(client, mock_session, mock_upload_success_response):
    """Test upload with screenshots."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_upload_success_response

    torrent_data = b'torrent file content'
    screenshot_content = b'PNG image data'

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread, \
         patch('pathlib.Path.exists', return_value=True), \
         patch('builtins.open', mock_open(read_data=screenshot_content)):

        mock_to_thread.return_value = mock_response

        result = await client.upload_torrent(
            session=mock_session,
            torrent_data=torrent_data,
            release_name="Movie.2023.1080p",
            category_id="1",
            tag_ids=["10"],
            screenshots=[Path("screenshot1.png"), Path("screenshot2.png")]
        )

        assert result['success'] is True


@pytest.mark.asyncio
async def test_upload_torrent_response_with_id_field(client, mock_session):
    """Test upload response using 'id' field instead of 'torrent_id'."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'id': '99999',  # Some APIs use 'id' instead of 'torrent_id'
        'message': 'Success'
    }

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        result = await client.upload_torrent(
            session=mock_session,
            torrent_data=b'test',
            release_name="Test",
            category_id="1",
            tag_ids=["10"]
        )

        # Verify it extracted 'id' as torrent_id
        assert result['torrent_id'] == '99999'
        assert 'torrents/99999' in result['torrent_url']


# ============================================================================
# upload_torrent Validation Tests
# ============================================================================

@pytest.mark.asyncio
async def test_upload_torrent_empty_torrent_data(client, mock_session):
    """Test validation fails when torrent_data is empty."""
    with pytest.raises(TrackerAPIError) as exc_info:
        await client.upload_torrent(
            session=mock_session,
            torrent_data=b'',  # Empty data
            release_name="Test",
            category_id="1",
            tag_ids=["10"]
        )

    assert "Torrent data is empty" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_torrent_missing_release_name(client, mock_session):
    """Test validation fails when release_name is missing."""
    with pytest.raises(TrackerAPIError) as exc_info:
        await client.upload_torrent(
            session=mock_session,
            torrent_data=b'test',
            release_name="",  # Empty name
            category_id="1",
            tag_ids=["10"]
        )

    assert "Release name is required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_torrent_missing_category_id(client, mock_session):
    """Test validation fails when category_id is missing."""
    with pytest.raises(TrackerAPIError) as exc_info:
        await client.upload_torrent(
            session=mock_session,
            torrent_data=b'test',
            release_name="Test",
            category_id="",  # Empty category
            tag_ids=["10"]
        )

    assert "Category ID is required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_torrent_empty_tag_ids(client, mock_session):
    """Test validation fails when tag_ids is empty."""
    with pytest.raises(TrackerAPIError) as exc_info:
        await client.upload_torrent(
            session=mock_session,
            torrent_data=b'test',
            release_name="Test",
            category_id="1",
            tag_ids=[]  # Empty tags
        )

    assert "At least one tag is required" in str(exc_info.value)


# ============================================================================
# upload_torrent Error Handling Tests
# ============================================================================

@pytest.mark.asyncio
async def test_upload_torrent_http_error_400(client, mock_session):
    """Test handling of HTTP 400 Bad Request (non-retryable)."""
    mock_response = Mock()
    mock_response.status_code = 400
    mock_response.json.return_value = {'message': 'Invalid request'}
    mock_response.text = 'Invalid request'

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with pytest.raises(TrackerAPIError) as exc_info:
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        assert "Upload failed with HTTP 400" in str(exc_info.value)
        assert "Invalid request" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_torrent_http_error_401(client, mock_session):
    """Test handling of HTTP 401 Unauthorized (non-retryable)."""
    mock_response = Mock()
    mock_response.status_code = 401
    mock_response.json.return_value = {'message': 'Invalid passkey'}
    mock_response.text = 'Unauthorized'

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with pytest.raises(TrackerAPIError) as exc_info:
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        assert "401" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_torrent_http_error_429(client, mock_session):
    """Test handling of HTTP 429 Rate Limited (retryable)."""
    mock_response = Mock()
    mock_response.status_code = 429
    mock_response.json.return_value = {'message': 'Rate limited', 'retry_after': 60}
    mock_response.text = 'Too Many Requests'

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with pytest.raises(NetworkRetryableError) as exc_info:
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        assert "Rate limited" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_torrent_http_error_503(client, mock_session):
    """Test handling of HTTP 503 Service Unavailable (retryable)."""
    mock_response = Mock()
    mock_response.status_code = 503
    mock_response.json.return_value = {'message': 'Service unavailable'}
    mock_response.text = 'Service Unavailable'

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with pytest.raises(NetworkRetryableError) as exc_info:
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        assert "Service temporarily unavailable" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_torrent_invalid_json_response(client, mock_session):
    """Test handling of invalid JSON response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.side_effect = ValueError("Invalid JSON")
    mock_response.text = "Not JSON content"

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with pytest.raises(TrackerAPIError) as exc_info:
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        assert "Invalid JSON response" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_torrent_timeout_error(client, mock_session):
    """Test handling of request timeout."""
    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = requests.exceptions.Timeout("Request timeout")

        with pytest.raises(NetworkRetryableError) as exc_info:
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        assert "timeout" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_upload_torrent_connection_error(client, mock_session):
    """Test handling of connection error."""
    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = requests.exceptions.ConnectionError("Connection refused")

        with pytest.raises(NetworkRetryableError) as exc_info:
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        assert "Failed to connect to tracker" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_torrent_generic_request_exception(client, mock_session):
    """Test handling of generic request exception."""
    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = requests.exceptions.RequestException("Generic error")

        with pytest.raises(NetworkRetryableError) as exc_info:
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        assert "Upload request failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_torrent_unexpected_exception(client, mock_session):
    """Test handling of unexpected exception."""
    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = ValueError("Unexpected error")

        with pytest.raises(TrackerAPIError) as exc_info:
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        assert "Unexpected error during upload" in str(exc_info.value)


# ============================================================================
# get_tags Tests
# ============================================================================

@pytest.mark.asyncio
async def test_get_tags_success(client, mock_session, mock_tags_response):
    """Test successful tags retrieval."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_tags_response

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        tags = await client.get_tags(mock_session)

        # Verify tags are normalized
        assert len(tags) == 3
        assert tags[0]['tag_id'] == '10'
        assert tags[0]['label'] == 'BluRay'
        assert tags[0]['category'] == 'Quality'
        assert tags[1]['tag_id'] == '15'
        assert tags[2]['tag_id'] == '20'


@pytest.mark.asyncio
async def test_get_tags_list_response_format(client, mock_session):
    """Test tags API returning list instead of dict."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {'id': '10', 'name': 'Tag1'},
        {'id': '20', 'name': 'Tag2'}
    ]

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        tags = await client.get_tags(mock_session)

        assert len(tags) == 2
        assert tags[0]['tag_id'] == '10'
        assert tags[0]['label'] == 'Tag1'


@pytest.mark.asyncio
async def test_get_tags_unexpected_format(client, mock_session):
    """Test handling of unexpected tags response format."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'unexpected': 'format'}

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with pytest.raises(TrackerAPIError) as exc_info:
            await client.get_tags(mock_session)

        assert "Unexpected tags response format" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_tags_http_error(client, mock_session):
    """Test handling of HTTP error when fetching tags."""
    mock_response = Mock()
    mock_response.status_code = 500

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with pytest.raises(TrackerAPIError) as exc_info:
            await client.get_tags(mock_session)

        assert "Failed to fetch tags" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_tags_connection_error(client, mock_session):
    """Test handling of connection error when fetching tags."""
    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = requests.exceptions.ConnectionError("Connection refused")

        with pytest.raises(NetworkRetryableError) as exc_info:
            await client.get_tags(mock_session)

        assert "Failed to fetch tags" in str(exc_info.value)


# ============================================================================
# get_categories Tests
# ============================================================================

@pytest.mark.asyncio
async def test_get_categories_success(client, mock_session, mock_categories_response):
    """Test successful categories retrieval."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_categories_response

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        categories = await client.get_categories(mock_session)

        # Verify categories are normalized
        assert len(categories) == 2
        assert categories[0]['category_id'] == '1'
        assert categories[0]['name'] == 'Movies'
        assert categories[0]['description'] == 'Feature films'
        assert categories[1]['category_id'] == '2'
        assert categories[1]['name'] == 'TV Shows'


@pytest.mark.asyncio
async def test_get_categories_list_response_format(client, mock_session):
    """Test categories API returning list instead of dict."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {'id': '1', 'name': 'Movies'},
        {'id': '2', 'name': 'TV'}
    ]

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        categories = await client.get_categories(mock_session)

        assert len(categories) == 2
        assert categories[0]['category_id'] == '1'
        assert categories[0]['name'] == 'Movies'


@pytest.mark.asyncio
async def test_get_categories_unexpected_format(client, mock_session):
    """Test handling of unexpected categories response format."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'unexpected': 'format'}

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with pytest.raises(TrackerAPIError) as exc_info:
            await client.get_categories(mock_session)

        assert "Unexpected categories response format" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_categories_http_error(client, mock_session):
    """Test handling of HTTP error when fetching categories."""
    mock_response = Mock()
    mock_response.status_code = 404

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with pytest.raises(TrackerAPIError) as exc_info:
            await client.get_categories(mock_session)

        assert "Failed to fetch categories" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_categories_timeout(client, mock_session):
    """Test handling of timeout when fetching categories."""
    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = requests.exceptions.Timeout("Timeout")

        with pytest.raises(NetworkRetryableError) as exc_info:
            await client.get_categories(mock_session)

        assert "Failed to fetch categories" in str(exc_info.value)


# ============================================================================
# validate_passkey Tests
# ============================================================================

@pytest.mark.asyncio
async def test_validate_passkey_success(client, mock_session, mock_tags_response):
    """Test successful passkey validation."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_tags_response

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        is_valid = await client.validate_passkey(mock_session)

        assert is_valid is True


@pytest.mark.asyncio
async def test_validate_passkey_invalid_401(client, mock_session):
    """Test passkey validation with 401 Unauthorized."""
    mock_response = Mock()
    mock_response.status_code = 401

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with patch.object(client, 'get_tags', side_effect=TrackerAPIError("Unauthorized", status_code=401)):
            is_valid = await client.validate_passkey(mock_session)

        assert is_valid is False


@pytest.mark.asyncio
async def test_validate_passkey_invalid_403(client, mock_session):
    """Test passkey validation with 403 Forbidden."""
    with patch.object(client, 'get_tags', side_effect=TrackerAPIError("Forbidden", status_code=403)):
        is_valid = await client.validate_passkey(mock_session)

    assert is_valid is False


@pytest.mark.asyncio
async def test_validate_passkey_network_error(client, mock_session):
    """Test passkey validation with network error (should re-raise)."""
    with patch.object(client, 'get_tags', side_effect=NetworkRetryableError("Network error")):
        with pytest.raises(NetworkRetryableError):
            await client.validate_passkey(mock_session)


@pytest.mark.asyncio
async def test_validate_passkey_unexpected_error(client, mock_session):
    """Test passkey validation with unexpected error."""
    with patch.object(client, 'get_tags', side_effect=ValueError("Unexpected error")):
        is_valid = await client.validate_passkey(mock_session)

    assert is_valid is False


# ============================================================================
# Retry Logic Integration Tests
# ============================================================================

@pytest.mark.asyncio
async def test_upload_retry_on_network_error(client, mock_session, mock_upload_success_response):
    """Test upload retries on network error and eventually succeeds."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_upload_success_response

    call_count = 0

    async def mock_request_with_retries(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise requests.exceptions.ConnectionError("Connection refused")
        return mock_response

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = mock_request_with_retries

        result = await client.upload_torrent(
            session=mock_session,
            torrent_data=b'test',
            release_name="Test",
            category_id="1",
            tag_ids=["10"]
        )

        # Should succeed after 2 retries
        assert result['success'] is True
        assert call_count == 3


@pytest.mark.asyncio
async def test_upload_retry_exhausts_retries(client, mock_session):
    """Test upload exhausts retries and fails."""
    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = requests.exceptions.ConnectionError("Always fails")

        with pytest.raises(NetworkRetryableError):
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        # Should have tried 4 times (1 initial + 3 retries)
        assert mock_to_thread.call_count == 4


@pytest.mark.asyncio
async def test_upload_no_retry_on_non_retryable_error(client, mock_session):
    """Test upload does not retry on non-retryable TrackerAPIError."""
    mock_response = Mock()
    mock_response.status_code = 400
    mock_response.json.return_value = {'message': 'Bad request'}
    mock_response.text = 'Bad request'

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = mock_response

        with pytest.raises(TrackerAPIError):
            await client.upload_torrent(
                session=mock_session,
                torrent_data=b'test',
                release_name="Test",
                category_id="1",
                tag_ids=["10"]
            )

        # Should only try once (no retries for 400 errors)
        assert mock_to_thread.call_count == 1


# ============================================================================
# Edge Cases
# ============================================================================

@pytest.mark.asyncio
async def test_upload_with_screenshot_file_not_found(client, mock_session, mock_upload_success_response):
    """Test upload skips screenshots that don't exist."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_upload_success_response

    with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread, \
         patch('pathlib.Path.exists', return_value=False):

        mock_to_thread.return_value = mock_response

        result = await client.upload_torrent(
            session=mock_session,
            torrent_data=b'test',
            release_name="Test",
            category_id="1",
            tag_ids=["10"],
            screenshots=[Path("nonexistent.png")]
        )

        # Should still succeed, just skip missing screenshots
        assert result['success'] is True


def test_prepare_multipart_data_converts_tag_ids_to_strings(client):
    """Test that tag IDs are converted to strings."""
    # Pass integers as tag IDs
    data = client._prepare_multipart_data(
        release_name="Test",
        category_id="1",
        tag_ids=[10, 15, 20]  # Integer IDs
    )

    # Verify they are stored as strings
    tag_fields = [value for name, value in data if name == 'tags']
    assert all(isinstance(tag, str) for tag in tag_fields)
    assert '10' in tag_fields
    assert '15' in tag_fields
    assert '20' in tag_fields
