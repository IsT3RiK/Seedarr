"""
Critical Pattern Tests for Seedarr v2.0

This test suite verifies that critical implementation patterns documented in the
specification are not broken during refactoring. These patterns are essential for
correct operation with La Cale tracker and must be preserved.

CRITICAL PATTERNS TESTED:
1. Tags sent as repeated form fields (NOT JSON arrays) - FIX_TAGS_REPEATED_FIELDS.md
2. .torrent files include source='lacale' flag - prevents torrent client re-downloads
3. NFO validation enforced as mandatory pipeline blocker
4. FlareSolverr cookie extraction flow preserved

Test Categories:
    - Pattern 1: Repeated Fields for Tags (CRITICAL)
    - Pattern 2: Torrent Source Flag (CRITICAL)
    - Pattern 3: NFO Mandatory Validation (CRITICAL)
    - Pattern 4: FlareSolverr Cookie Management (CRITICAL)

These tests act as regression prevention for the most critical business logic
that directly impacts tracker uploads and torrent client behavior.
"""

import pytest
import asyncio
import tempfile
from pathlib import Path
from typing import List, Dict, Any
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from requests import Session

# Import components to test
from backend.app.services.lacale_client import LaCaleClient
from backend.app.services.media_analyzer import MediaAnalyzer, _create_torrent_sync
from backend.app.services.nfo_validator import NFOValidator
from backend.app.services.cloudflare_session_manager import CloudflareSessionManager
from backend.app.services.exceptions import TrackerAPIError, CloudflareBypassError


# ============================================================================
# PATTERN 1: Tags as Repeated Form Fields (CRITICAL)
# ============================================================================
# From spec: Tags MUST be sent as repeated fields (("tags", "ID1"), ("tags", "ID2"))
# NOT JSON arrays. This is undocumented La Cale API behavior.

class TestCriticalPattern1_TagsRepeatedFields:
    """
    CRITICAL: Verify tags are sent as repeated form fields, NOT JSON arrays.

    This is the most critical pattern documented in FIX_TAGS_REPEATED_FIELDS.md.
    La Cale API requires tags as repeated multipart form fields. Using JSON arrays
    will cause HTTP 500 errors from the tracker.

    Required Format:
        [('name', 'Release'), ('category_id', '1'),
         ('tags', '10'), ('tags', '15'), ('tags', '20')]

    FORBIDDEN Format:
        {'name': 'Release', 'category_id': '1', 'tags': ['10', '15', '20']}
    """

    def test_tags_prepared_as_repeated_fields(self):
        """Test that tags are prepared as repeated form field tuples."""
        client = LaCaleClient(
            tracker_url="https://tracker.example.com",
            passkey="test_passkey_12345"
        )

        # Prepare multipart data with multiple tags
        tag_ids = ["10", "15", "20"]
        data = client._prepare_multipart_data(
            release_name="Movie.2023.1080p.BluRay.x264",
            category_id="1",
            tag_ids=tag_ids
        )

        # Verify data is a list of tuples (required for multipart/form-data)
        assert isinstance(data, list), "Data must be a list of tuples, not dict"

        # Verify each item is a tuple
        for item in data:
            assert isinstance(item, tuple), f"Each field must be a tuple, got {type(item)}"
            assert len(item) == 2, f"Each tuple must have 2 elements (name, value), got {len(item)}"

        # Extract all tag fields
        tag_fields = [item for item in data if item[0] == 'tags']

        # CRITICAL: Verify tags appear as REPEATED fields
        assert len(tag_fields) == len(tag_ids), \
            f"Expected {len(tag_ids)} repeated tag fields, got {len(tag_fields)}"

        # Verify each tag is a separate ('tags', 'tag_id') tuple
        for i, tag_id in enumerate(tag_ids):
            assert tag_fields[i] == ('tags', tag_id), \
                f"Tag {i} should be ('tags', '{tag_id}'), got {tag_fields[i]}"

        print(f"✓ CRITICAL PATTERN 1 VERIFIED: Tags sent as {len(tag_fields)} repeated form fields")

    def test_tags_not_json_array(self):
        """Test that tags are NOT formatted as JSON array."""
        client = LaCaleClient(
            tracker_url="https://tracker.example.com",
            passkey="test_passkey_12345"
        )

        tag_ids = ["10", "15", "20"]
        data = client._prepare_multipart_data(
            release_name="Movie.2023.1080p.BluRay.x264",
            category_id="1",
            tag_ids=tag_ids
        )

        # Verify data is NOT a dictionary
        assert not isinstance(data, dict), \
            "CRITICAL ERROR: Data formatted as dict. Must be list of tuples for multipart/form-data"

        # Verify no tag field contains a list/array value
        for field_name, field_value in data:
            if field_name == 'tags':
                assert not isinstance(field_value, (list, tuple)), \
                    f"CRITICAL ERROR: Tag value is {type(field_value)}. Must be string."
                assert isinstance(field_value, str), \
                    f"Tag value must be string, got {type(field_value)}"

        print("✓ CRITICAL PATTERN 1 VERIFIED: Tags NOT formatted as JSON array")

    def test_empty_tags_list(self):
        """Test handling of empty tags list."""
        client = LaCaleClient(
            tracker_url="https://tracker.example.com",
            passkey="test_passkey_12345"
        )

        # Test with empty tags list
        data = client._prepare_multipart_data(
            release_name="Movie.2023.1080p.BluRay.x264",
            category_id="1",
            tag_ids=[]
        )

        # Verify no tag fields present
        tag_fields = [item for item in data if item[0] == 'tags']
        assert len(tag_fields) == 0, "No tag fields should be present for empty tags list"

        print("✓ CRITICAL PATTERN 1 VERIFIED: Empty tags list handled correctly")

    def test_single_tag(self):
        """Test single tag is still formatted as repeated field."""
        client = LaCaleClient(
            tracker_url="https://tracker.example.com",
            passkey="test_passkey_12345"
        )

        # Test with single tag
        data = client._prepare_multipart_data(
            release_name="Movie.2023.1080p.BluRay.x264",
            category_id="1",
            tag_ids=["10"]
        )

        # Verify single tag is formatted as tuple
        tag_fields = [item for item in data if item[0] == 'tags']
        assert len(tag_fields) == 1, "Should have exactly 1 tag field"
        assert tag_fields[0] == ('tags', '10'), "Single tag must be tuple ('tags', '10')"

        print("✓ CRITICAL PATTERN 1 VERIFIED: Single tag formatted correctly")

    def test_multipart_data_structure_complete(self):
        """Test complete multipart data structure with all fields."""
        client = LaCaleClient(
            tracker_url="https://tracker.example.com",
            passkey="test_passkey_12345"
        )

        # Prepare data with all optional fields
        data = client._prepare_multipart_data(
            release_name="Movie.2023.1080p.BluRay.x264",
            category_id="1",
            tag_ids=["10", "15", "20"],
            description="Movie plot description",
            nfo_content="NFO file content",
            mediainfo="MediaInfo technical details"
        )

        # Verify data structure
        field_names = [item[0] for item in data]

        # Required fields
        assert 'name' in field_names, "Missing required field: name"
        assert 'category_id' in field_names, "Missing required field: category_id"
        assert 'passkey' in field_names, "Missing required field: passkey"

        # Tags as repeated fields
        assert field_names.count('tags') == 3, "Should have 3 repeated tag fields"

        # Optional fields
        assert 'description' in field_names, "Missing optional field: description"
        assert 'nfo' in field_names, "Missing optional field: nfo"
        assert 'mediainfo' in field_names, "Missing optional field: mediainfo"

        print("✓ CRITICAL PATTERN 1 VERIFIED: Complete multipart data structure correct")


# ============================================================================
# PATTERN 2: Torrent Source Flag (CRITICAL)
# ============================================================================
# From spec: .torrent files MUST include source="lacale" field to prevent
# torrent clients from re-downloading all content.

class TestCriticalPattern2_TorrentSourceFlag:
    """
    CRITICAL: Verify .torrent files include source='lacale' flag.

    Without this flag, torrent clients will re-download all content even if
    files already exist locally. This is a tracker-specific requirement
    documented in the specification.

    The source flag ensures proper torrent identity and prevents wasteful
    re-downloads that would frustrate users and waste bandwidth.
    """

    @pytest.mark.asyncio
    async def test_create_torrent_includes_source_flag(self):
        """Test that create_torrent includes source='lacale' flag."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test file
            test_file = Path(temp_dir) / "test_video.mkv"
            test_file.write_bytes(b"test video content for torrent creation")

            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            # Mock database session
            mock_db = Mock()

            # Create MediaAnalyzer instance
            analyzer = MediaAnalyzer(mock_db)

            # Mock torf.Torrent to verify source flag
            with patch('backend.app.services.media_analyzer.torf.Torrent') as mock_torrent_class:
                mock_torrent_instance = Mock()
                mock_torrent_instance.write = Mock()
                mock_torrent_class.return_value = mock_torrent_instance

                # Create torrent
                torrent_path = await analyzer.create_torrent(
                    file_path=str(test_file),
                    announce_url="https://tracker.example.com/announce",
                    output_dir=str(output_dir)
                )

                # Verify torf.Torrent was called with source='lacale'
                mock_torrent_class.assert_called_once()
                call_kwargs = mock_torrent_class.call_args[1]

                # CRITICAL: Verify source flag is present and set to 'lacale'
                assert 'source' in call_kwargs, \
                    "CRITICAL ERROR: source parameter missing from torrent creation"
                assert call_kwargs['source'] == 'lacale', \
                    f"CRITICAL ERROR: source must be 'lacale', got '{call_kwargs['source']}'"

                # Verify other required parameters
                assert call_kwargs['private'] is True, "Torrent must be marked as private"

                print("✓ CRITICAL PATTERN 2 VERIFIED: Torrent created with source='lacale' flag")

    def test_create_torrent_sync_includes_source_flag(self):
        """Test synchronous torrent creation function includes source flag."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test file
            test_file = Path(temp_dir) / "test_video.mkv"
            test_file.write_bytes(b"test video content for synchronous torrent creation")

            output_path = Path(temp_dir) / "test_video.torrent"

            # Mock torf.Torrent
            with patch('backend.app.services.media_analyzer.torf.Torrent') as mock_torrent_class:
                mock_torrent_instance = Mock()
                mock_torrent_instance.write = Mock()
                mock_torrent_class.return_value = mock_torrent_instance

                # Call synchronous function
                _create_torrent_sync(
                    file_path=str(test_file),
                    announce_url="https://tracker.example.com/announce",
                    output_path=str(output_path),
                    source="lacale"
                )

                # Verify torf.Torrent called with source parameter
                call_kwargs = mock_torrent_class.call_args[1]

                # CRITICAL: Verify source flag
                assert 'source' in call_kwargs, "source parameter missing"
                assert call_kwargs['source'] == 'lacale', \
                    f"source must be 'lacale', got '{call_kwargs['source']}'"

                print("✓ CRITICAL PATTERN 2 VERIFIED: Sync function uses source='lacale' flag")

    @pytest.mark.asyncio
    async def test_source_flag_prevents_reddownload(self):
        """
        Test that source flag is set to prevent re-download.

        This is a documentation test to verify the pattern is understood
        and implemented correctly. The actual prevention happens in torrent
        clients, but we verify the flag is set.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = Path(temp_dir) / "test_video.mkv"
            test_file.write_bytes(b"test content")

            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            mock_db = Mock()
            analyzer = MediaAnalyzer(mock_db)

            with patch('backend.app.services.media_analyzer.torf.Torrent') as mock_torrent_class:
                mock_torrent_instance = Mock()
                mock_torrent_instance.write = Mock()
                mock_torrent_class.return_value = mock_torrent_instance

                await analyzer.create_torrent(
                    file_path=str(test_file),
                    announce_url="https://tracker.example.com/announce",
                    output_dir=str(output_dir)
                )

                # Verify source flag is exactly 'lacale' (tracker-specific)
                call_kwargs = mock_torrent_class.call_args[1]
                assert call_kwargs.get('source') == 'lacale', \
                    "Source must be 'lacale' to prevent client re-download"

                print("✓ CRITICAL PATTERN 2 VERIFIED: Source flag set correctly for re-download prevention")


# ============================================================================
# PATTERN 3: NFO Mandatory Validation (CRITICAL)
# ============================================================================
# From spec: NFO validation must be enforced as mandatory gate before
# distribution stage. Pipeline MUST abort if NFO invalid and cannot be generated.

class TestCriticalPattern3_NFOMandatoryValidation:
    """
    CRITICAL: Verify NFO validation enforced as mandatory pipeline blocker.

    The NFO file must be validated before any upload occurs. If the NFO is
    missing, invalid, or cannot be generated from TMDB data, the pipeline
    MUST abort with a clear error message.

    This ensures all uploads include proper metadata and prevents incomplete
    releases from being uploaded to the tracker.
    """

    def test_nfo_validation_blocks_on_missing_file(self):
        """Test that missing NFO file blocks pipeline."""
        mock_db = Mock()
        validator = NFOValidator(mock_db)

        # Test with non-existent NFO file
        with tempfile.TemporaryDirectory() as temp_dir:
            nfo_path = Path(temp_dir) / "nonexistent.nfo"

            is_valid, error_message = validator.validate_nfo_file(str(nfo_path))

            # CRITICAL: Validation must fail for missing file
            assert is_valid is False, "Validation should fail for missing NFO file"
            assert error_message is not None, "Error message must be provided"
            assert "does not exist" in error_message.lower() or "not found" in error_message.lower(), \
                "Error message should indicate file not found"

            print("✓ CRITICAL PATTERN 3 VERIFIED: Missing NFO file blocks validation")

    def test_nfo_validation_blocks_on_invalid_content(self):
        """Test that invalid NFO content blocks pipeline."""
        mock_db = Mock()
        validator = NFOValidator(mock_db)

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create NFO with missing required fields
            nfo_path = Path(temp_dir) / "invalid.nfo"
            nfo_path.write_text("This is invalid NFO content without required fields")

            is_valid, error_message = validator.validate_nfo_file(str(nfo_path))

            # CRITICAL: Validation must fail for invalid content
            assert is_valid is False, "Validation should fail for invalid NFO content"
            assert error_message is not None, "Error message must be provided"

            print("✓ CRITICAL PATTERN 3 VERIFIED: Invalid NFO content blocks validation")

    def test_nfo_validation_requires_title_year_plot(self):
        """Test that NFO must contain title, year, and plot fields."""
        mock_db = Mock()
        validator = NFOValidator(mock_db)

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create NFO missing year field
            nfo_path = Path(temp_dir) / "missing_year.nfo"
            nfo_content = """
            Title: Test Movie
            Plot: This is the plot description
            """
            nfo_path.write_text(nfo_content)

            is_valid, error_message = validator.validate_nfo_file(str(nfo_path))

            # Should fail due to missing year
            assert is_valid is False, "Validation should fail without year field"

            # Create valid NFO with all required fields
            valid_nfo_path = Path(temp_dir) / "valid.nfo"
            valid_content = """
            Title: Test Movie
            Year: 2023
            Plot: This is the plot description for the movie.
            """
            valid_nfo_path.write_text(valid_content)

            is_valid, error_message = validator.validate_nfo_file(str(valid_nfo_path))

            # Should pass with all required fields
            assert is_valid is True, "Validation should pass with all required fields"
            assert error_message is None, "No error message for valid NFO"

            print("✓ CRITICAL PATTERN 3 VERIFIED: NFO requires title, year, and plot")

    def test_nfo_validation_pipeline_blocker(self):
        """Test that ensure_valid_nfo raises exception to block pipeline."""
        mock_db = Mock()

        # Mock TMDBCache to return None (no cache available)
        with patch('backend.app.services.nfo_validator.TMDBCache') as mock_cache:
            mock_cache.get_cached.return_value = None

            validator = NFOValidator(mock_db)

            with tempfile.TemporaryDirectory() as temp_dir:
                # Test with invalid file and no TMDB cache
                test_file = Path(temp_dir) / "test.mkv"
                test_file.write_text("dummy")

                # CRITICAL: Must raise TrackerAPIError to block pipeline
                with pytest.raises(TrackerAPIError) as exc_info:
                    validator.ensure_valid_nfo(
                        file_path=str(test_file),
                        tmdb_id="12345",
                        release_name="Movie.2023.1080p"
                    )

                # Verify error message is descriptive
                assert "NFO" in str(exc_info.value), \
                    "Error message should mention NFO validation failure"

                print("✓ CRITICAL PATTERN 3 VERIFIED: NFO validation blocks pipeline with TrackerAPIError")

    def test_nfo_generation_from_tmdb_on_invalid(self):
        """Test NFO generation from TMDB cache when invalid."""
        mock_db = Mock()

        # Mock successful TMDB cache lookup
        mock_cache_entry = Mock()
        mock_cache_entry.title = "Test Movie"
        mock_cache_entry.year = 2023
        mock_cache_entry.plot = "This is a great movie plot."
        mock_cache_entry.cast = ["Actor 1", "Actor 2"]
        mock_cache_entry.ratings = {"imdb": "8.5"}

        with patch('backend.app.services.nfo_validator.TMDBCache') as mock_cache:
            mock_cache.get_cached.return_value = mock_cache_entry

            validator = NFOValidator(mock_db)

            with tempfile.TemporaryDirectory() as temp_dir:
                test_file = Path(temp_dir) / "test.mkv"
                test_file.write_text("dummy")

                # Should generate NFO from TMDB cache
                nfo_path = validator.ensure_valid_nfo(
                    file_path=str(test_file),
                    tmdb_id="12345",
                    release_name="Movie.2023.1080p"
                )

                # Verify NFO file was created
                assert Path(nfo_path).exists(), "NFO file should be generated"

                # Verify NFO content
                nfo_content = Path(nfo_path).read_text()
                assert "Test Movie" in nfo_content, "NFO should contain title"
                assert "2023" in nfo_content, "NFO should contain year"
                assert "plot" in nfo_content.lower(), "NFO should contain plot"

                print("✓ CRITICAL PATTERN 3 VERIFIED: NFO generated from TMDB on invalid")


# ============================================================================
# PATTERN 4: FlareSolverr Cookie Management (CRITICAL)
# ============================================================================
# From spec: FlareSolverr cookie extraction flow must be preserved for
# Cloudflare bypass authentication.

class TestCriticalPattern4_FlareSolverrCookieFlow:
    """
    CRITICAL: Verify FlareSolverr cookie extraction flow preserved.

    The FlareSolverr integration is mandatory for bypassing Cloudflare protection.
    Cookies must be correctly extracted from FlareSolverr response and applied
    to the requests.Session for subsequent tracker API calls.

    This pattern is essential for authentication with the tracker.
    """

    @pytest.mark.asyncio
    async def test_flaresolverr_cookie_extraction(self):
        """Test that cookies are correctly extracted from FlareSolverr response."""
        manager = CloudflareSessionManager(
            flaresolverr_url="http://localhost:8191",
            max_timeout=60000
        )

        # Mock FlareSolverr response with cookies
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "solution": {
                "cookies": [
                    {"name": "session_id", "value": "abc123"},
                    {"name": "csrf_token", "value": "xyz789"},
                    {"name": "tracker_auth", "value": "auth123"}
                ]
            }
        }

        with patch('backend.app.services.cloudflare_session_manager.requests.post') as mock_post:
            mock_post.return_value = mock_response

            # Get authenticated session
            session = await manager.get_session("https://tracker.example.com")

            # CRITICAL: Verify session is a requests.Session instance
            assert isinstance(session, Session), \
                "get_session must return requests.Session instance"

            # Verify all cookies were extracted and applied
            session_cookies = {cookie.name: cookie.value for cookie in session.cookies}

            assert "session_id" in session_cookies, "Missing session_id cookie"
            assert "csrf_token" in session_cookies, "Missing csrf_token cookie"
            assert "tracker_auth" in session_cookies, "Missing tracker_auth cookie"

            assert session_cookies["session_id"] == "abc123", "Incorrect session_id value"
            assert session_cookies["csrf_token"] == "xyz789", "Incorrect csrf_token value"
            assert session_cookies["tracker_auth"] == "auth123", "Incorrect tracker_auth value"

            print("✓ CRITICAL PATTERN 4 VERIFIED: FlareSolverr cookies extracted correctly")

    @pytest.mark.asyncio
    async def test_flaresolverr_request_format(self):
        """Test that FlareSolverr request is formatted correctly."""
        manager = CloudflareSessionManager(
            flaresolverr_url="http://localhost:8191",
            max_timeout=60000
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "solution": {
                "cookies": [{"name": "test", "value": "value"}]
            }
        }

        with patch('backend.app.services.cloudflare_session_manager.requests.post') as mock_post:
            mock_post.return_value = mock_response

            await manager.get_session("https://tracker.example.com")

            # Verify FlareSolverr was called with correct format
            mock_post.assert_called_once()

            call_args = mock_post.call_args

            # Verify URL
            assert call_args[0][0] == "http://localhost:8191/v1", \
                "FlareSolverr URL must be {url}/v1"

            # Verify JSON payload
            json_data = call_args[1]['json']
            assert json_data['cmd'] == 'request.get', "Command must be 'request.get'"
            assert json_data['url'] == 'https://tracker.example.com', "URL must match tracker URL"
            assert 'maxTimeout' in json_data, "maxTimeout must be specified"

            print("✓ CRITICAL PATTERN 4 VERIFIED: FlareSolverr request format correct")

    @pytest.mark.asyncio
    async def test_flaresolverr_failure_raises_cloudflare_bypass_error(self):
        """Test that FlareSolverr failures raise CloudflareBypassError."""
        manager = CloudflareSessionManager(
            flaresolverr_url="http://localhost:8191",
            max_timeout=60000
        )

        # Mock FlareSolverr connection error
        with patch('backend.app.services.cloudflare_session_manager.requests.post') as mock_post:
            mock_post.side_effect = Exception("FlareSolverr connection failed")

            # CRITICAL: Must raise CloudflareBypassError
            with pytest.raises(CloudflareBypassError) as exc_info:
                await manager.get_session("https://tracker.example.com")

            assert "FlareSolverr" in str(exc_info.value) or "Cloudflare" in str(exc_info.value), \
                "Error message should mention FlareSolverr or Cloudflare"

            print("✓ CRITICAL PATTERN 4 VERIFIED: FlareSolverr failures raise CloudflareBypassError")

    @pytest.mark.asyncio
    async def test_session_reusable_for_multiple_requests(self):
        """Test that returned session can be used for multiple API calls."""
        manager = CloudflareSessionManager(
            flaresolverr_url="http://localhost:8191",
            max_timeout=60000
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "solution": {
                "cookies": [{"name": "auth", "value": "token123"}]
            }
        }

        with patch('backend.app.services.cloudflare_session_manager.requests.post') as mock_post:
            mock_post.return_value = mock_response

            session = await manager.get_session("https://tracker.example.com")

            # Verify session can be used for multiple requests
            assert hasattr(session, 'get'), "Session must have get method"
            assert hasattr(session, 'post'), "Session must have post method"
            assert hasattr(session, 'cookies'), "Session must have cookies attribute"

            # Verify cookies persist in session
            assert len(list(session.cookies)) > 0, "Session must contain cookies"

            print("✓ CRITICAL PATTERN 4 VERIFIED: Session reusable for multiple requests")


# ============================================================================
# Integration Test: All Critical Patterns Together
# ============================================================================

class TestCriticalPatternsIntegration:
    """
    Integration test verifying all critical patterns work together correctly.

    This test simulates the complete upload flow and verifies:
    1. FlareSolverr cookies extracted
    2. .torrent created with source='lacale'
    3. NFO validated before upload
    4. Tags sent as repeated fields in upload
    """

    @pytest.mark.asyncio
    async def test_all_critical_patterns_together(self):
        """
        Integration test: All critical patterns in complete upload flow.

        This test verifies the end-to-end flow with all critical patterns:
        - FlareSolverr cookie extraction
        - Torrent source='lacale' flag
        - NFO mandatory validation
        - Tags as repeated fields
        """
        # Test setup
        with tempfile.TemporaryDirectory() as temp_dir:
            # 1. Create test files
            test_file = Path(temp_dir) / "test_video.mkv"
            test_file.write_bytes(b"test video content")

            # 2. Test FlareSolverr cookie flow
            manager = CloudflareSessionManager(
                flaresolverr_url="http://localhost:8191",
                max_timeout=60000
            )

            mock_flare_response = Mock()
            mock_flare_response.status_code = 200
            mock_flare_response.json.return_value = {
                "solution": {
                    "cookies": [{"name": "auth", "value": "token"}]
                }
            }

            with patch('backend.app.services.cloudflare_session_manager.requests.post') as mock_post:
                mock_post.return_value = mock_flare_response
                session = await manager.get_session("https://tracker.example.com")

                # Verify cookies extracted
                assert len(list(session.cookies)) > 0, "Pattern 4: Cookies not extracted"
                print("✓ Pattern 4: FlareSolverr cookies extracted")

            # 3. Test torrent creation with source flag
            mock_db = Mock()
            analyzer = MediaAnalyzer(mock_db)

            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            with patch('backend.app.services.media_analyzer.torf.Torrent') as mock_torrent_class:
                mock_torrent = Mock()
                mock_torrent.write = Mock()
                mock_torrent_class.return_value = mock_torrent

                await analyzer.create_torrent(
                    file_path=str(test_file),
                    announce_url="https://tracker.example.com/announce",
                    output_dir=str(output_dir)
                )

                # Verify source='lacale' flag
                call_kwargs = mock_torrent_class.call_args[1]
                assert call_kwargs.get('source') == 'lacale', "Pattern 2: Source flag not set"
                print("✓ Pattern 2: Torrent created with source='lacale'")

            # 4. Test NFO validation
            validator = NFOValidator(mock_db)
            valid_nfo = Path(temp_dir) / "valid.nfo"
            valid_nfo.write_text("Title: Test\nYear: 2023\nPlot: Test plot")

            is_valid, _ = validator.validate_nfo_file(str(valid_nfo))
            assert is_valid is True, "Pattern 3: NFO validation failed"
            print("✓ Pattern 3: NFO validation enforced")

            # 5. Test tags as repeated fields
            client = LaCaleClient(
                tracker_url="https://tracker.example.com",
                passkey="test_passkey"
            )

            data = client._prepare_multipart_data(
                release_name="Movie.2023.1080p",
                category_id="1",
                tag_ids=["10", "15"]
            )

            tag_fields = [item for item in data if item[0] == 'tags']
            assert len(tag_fields) == 2, "Pattern 1: Tags not as repeated fields"
            assert all(isinstance(item, tuple) for item in tag_fields), \
                "Pattern 1: Tags not formatted as tuples"
            print("✓ Pattern 1: Tags sent as repeated fields")

            print("\n" + "="*70)
            print("✓ ALL CRITICAL PATTERNS VERIFIED SUCCESSFULLY")
            print("="*70)


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])
