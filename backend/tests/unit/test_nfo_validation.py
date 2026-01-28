"""
Unit Tests for NFO Validator Service

This test suite validates the NFO validation and generation functionality
implemented in backend.app.services.nfo_validator.py.

Test Coverage:
    - NFO file validation (exists, required fields, content)
    - NFO generation from TMDB cache
    - Pipeline blocking on NFO validation failure
    - Edge cases (missing files, invalid content, empty files)

Requirements:
    - pytest
    - pytest-asyncio (for async test support)
    - sqlalchemy (for database mocking)
"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from sqlalchemy.orm import Session

from backend.app.services.nfo_validator import NFOValidator
from backend.app.services.exceptions import TrackerAPIError
from backend.app.models.tmdb_cache import TMDBCache


class TestNFOValidatorInitialization:
    """Test NFOValidator initialization."""

    def test_initialization(self):
        """Test NFOValidator initializes with database session."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        assert validator.db == db
        assert isinstance(validator, NFOValidator)

    def test_required_fields_constant(self):
        """Test REQUIRED_FIELDS constant is correctly defined."""
        assert NFOValidator.REQUIRED_FIELDS == ['title', 'year', 'plot']


class TestNFOFileValidation:
    """Test NFO file validation functionality."""

    def test_validate_nfo_file_exists_and_valid(self):
        """Test validation passes for existing NFO with required fields."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Create temporary NFO file with valid content
        with tempfile.NamedTemporaryFile(mode='w', suffix='.nfo', delete=False) as f:
            f.write("=" * 80 + "\n")
            f.write("Title: The Test Movie\n")
            f.write("Year: 2023\n")
            f.write("\n")
            f.write("Plot: This is a comprehensive plot description that exceeds the minimum ")
            f.write("character count and provides meaningful content about the movie.\n")
            f.write("=" * 80 + "\n")
            nfo_path = f.name

        try:
            is_valid, error = validator.validate_nfo_file(nfo_path)
            assert is_valid is True
            assert error is None
        finally:
            os.unlink(nfo_path)

    def test_validate_nfo_file_does_not_exist(self):
        """Test validation fails when NFO file doesn't exist."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        is_valid, error = validator.validate_nfo_file("/nonexistent/path/to/movie.nfo")

        assert is_valid is False
        assert "does not exist" in error.lower()

    def test_validate_nfo_file_empty(self):
        """Test validation fails for empty NFO file."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Create empty NFO file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.nfo', delete=False) as f:
            nfo_path = f.name

        try:
            is_valid, error = validator.validate_nfo_file(nfo_path)
            assert is_valid is False
            assert "empty" in error.lower()
        finally:
            os.unlink(nfo_path)

    def test_validate_nfo_file_too_short(self):
        """Test validation fails for NFO with insufficient content."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Create NFO file with too little content
        with tempfile.NamedTemporaryFile(mode='w', suffix='.nfo', delete=False) as f:
            f.write("Short content")
            nfo_path = f.name

        try:
            is_valid, error = validator.validate_nfo_file(nfo_path)
            assert is_valid is False
            assert "too short" in error.lower()
        finally:
            os.unlink(nfo_path)

    def test_validate_nfo_file_missing_title(self):
        """Test validation fails when title field is missing."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Create NFO without title
        with tempfile.NamedTemporaryFile(mode='w', suffix='.nfo', delete=False) as f:
            f.write("Year: 2023\n")
            f.write("Plot: This is a plot description with enough content to pass length checks.\n")
            nfo_path = f.name

        try:
            is_valid, error = validator.validate_nfo_file(nfo_path)
            assert is_valid is False
            assert "title" in error.lower()
        finally:
            os.unlink(nfo_path)

    def test_validate_nfo_file_missing_year(self):
        """Test validation fails when year field is missing."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Create NFO without year
        with tempfile.NamedTemporaryFile(mode='w', suffix='.nfo', delete=False) as f:
            f.write("Title: The Test Movie\n")
            f.write("Plot: This is a plot description with enough content to pass length checks.\n")
            nfo_path = f.name

        try:
            is_valid, error = validator.validate_nfo_file(nfo_path)
            assert is_valid is False
            assert "year" in error.lower()
        finally:
            os.unlink(nfo_path)

    def test_validate_nfo_file_missing_plot(self):
        """Test validation fails when plot field is missing."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Create NFO without plot (and too short for implicit plot)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.nfo', delete=False) as f:
            f.write("Title: The Test Movie\n")
            f.write("Year: 2023\n")
            f.write("Some short text\n")
            nfo_path = f.name

        try:
            is_valid, error = validator.validate_nfo_file(nfo_path)
            assert is_valid is False
            assert "plot" in error.lower()
        finally:
            os.unlink(nfo_path)

    def test_validate_nfo_file_with_alternative_keywords(self):
        """Test validation passes with alternative field names (name, overview, description)."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Create NFO with alternative keywords
        with tempfile.NamedTemporaryFile(mode='w', suffix='.nfo', delete=False) as f:
            f.write("Name: The Test Movie\n")  # 'name' instead of 'title'
            f.write("2023\n")  # Year as just a number
            f.write("Overview: This is a comprehensive overview description ")
            f.write("that exceeds minimum length requirements.\n")  # 'overview' instead of 'plot'
            nfo_path = f.name

        try:
            is_valid, error = validator.validate_nfo_file(nfo_path)
            assert is_valid is True
            assert error is None
        finally:
            os.unlink(nfo_path)

    def test_validate_nfo_file_path_is_directory(self):
        """Test validation fails when path is a directory, not a file."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        with tempfile.TemporaryDirectory() as temp_dir:
            is_valid, error = validator.validate_nfo_file(temp_dir)
            assert is_valid is False
            assert "not a file" in error.lower()


class TestNFOGenerationFromTMDB:
    """Test NFO generation from TMDB cache."""

    def test_generate_nfo_from_tmdb_success(self):
        """Test successful NFO generation from TMDB cache."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Mock TMDB cache entry
        cache_entry = Mock(spec=TMDBCache)
        cache_entry.tmdb_id = "12345"
        cache_entry.title = "Test Movie"
        cache_entry.year = 2023
        cache_entry.plot = "This is a test movie plot that describes the storyline."
        cache_entry.cast = [
            {"name": "Actor One"},
            {"name": "Actor Two"}
        ]
        cache_entry.ratings = {
            "vote_average": 8.5,
            "vote_count": 1000
        }

        # Mock TMDBCache.get_cached
        with patch.object(TMDBCache, 'get_cached', return_value=cache_entry):
            nfo_content = validator.generate_nfo_from_tmdb(
                tmdb_id="12345",
                release_name="Test.Movie.2023.1080p.BluRay.x264"
            )

            assert nfo_content is not None
            assert "Test.Movie.2023.1080p.BluRay.x264" in nfo_content
            assert "Test Movie" in nfo_content
            assert "2023" in nfo_content
            assert "This is a test movie plot" in nfo_content
            assert "Actor One" in nfo_content
            assert "Actor Two" in nfo_content
            assert "8.5" in nfo_content

    def test_generate_nfo_from_tmdb_writes_file(self):
        """Test NFO generation writes to file when output_path provided."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Mock TMDB cache entry
        cache_entry = Mock(spec=TMDBCache)
        cache_entry.tmdb_id = "12345"
        cache_entry.title = "Test Movie"
        cache_entry.year = 2023
        cache_entry.plot = "This is a test movie plot."
        cache_entry.cast = []
        cache_entry.ratings = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "test.nfo")

            with patch.object(TMDBCache, 'get_cached', return_value=cache_entry):
                nfo_content = validator.generate_nfo_from_tmdb(
                    tmdb_id="12345",
                    release_name="Test.Movie.2023",
                    output_path=output_path
                )

                assert os.path.exists(output_path)
                with open(output_path, 'r') as f:
                    file_content = f.read()
                assert file_content == nfo_content
                assert "Test Movie" in file_content

    def test_generate_nfo_from_tmdb_cache_not_found(self):
        """Test NFO generation fails when TMDB cache entry not found."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        with patch.object(TMDBCache, 'get_cached', return_value=None):
            with pytest.raises(TrackerAPIError) as exc_info:
                validator.generate_nfo_from_tmdb(
                    tmdb_id="99999",
                    release_name="NonExistent.Movie.2023"
                )

            assert "not cached" in str(exc_info.value).lower()

    def test_generate_nfo_from_tmdb_missing_title(self):
        """Test NFO generation fails when TMDB cache missing title."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        cache_entry = Mock(spec=TMDBCache)
        cache_entry.tmdb_id = "12345"
        cache_entry.title = None  # Missing title
        cache_entry.year = 2023
        cache_entry.plot = "Test plot"

        with patch.object(TMDBCache, 'get_cached', return_value=cache_entry):
            with pytest.raises(TrackerAPIError) as exc_info:
                validator.generate_nfo_from_tmdb(
                    tmdb_id="12345",
                    release_name="Test.Movie.2023"
                )

            assert "missing title" in str(exc_info.value).lower()

    def test_generate_nfo_from_tmdb_missing_plot(self):
        """Test NFO generation fails when TMDB cache missing plot."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        cache_entry = Mock(spec=TMDBCache)
        cache_entry.tmdb_id = "12345"
        cache_entry.title = "Test Movie"
        cache_entry.year = 2023
        cache_entry.plot = None  # Missing plot

        with patch.object(TMDBCache, 'get_cached', return_value=cache_entry):
            with pytest.raises(TrackerAPIError) as exc_info:
                validator.generate_nfo_from_tmdb(
                    tmdb_id="12345",
                    release_name="Test.Movie.2023"
                )

            assert "missing plot" in str(exc_info.value).lower()

    def test_generate_nfo_from_tmdb_with_string_cast(self):
        """Test NFO generation handles cast as strings (not dicts)."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        cache_entry = Mock(spec=TMDBCache)
        cache_entry.tmdb_id = "12345"
        cache_entry.title = "Test Movie"
        cache_entry.year = 2023
        cache_entry.plot = "Test plot"
        cache_entry.cast = ["Actor One", "Actor Two"]  # Strings instead of dicts
        cache_entry.ratings = {}

        with patch.object(TMDBCache, 'get_cached', return_value=cache_entry):
            nfo_content = validator.generate_nfo_from_tmdb(
                tmdb_id="12345",
                release_name="Test.Movie.2023"
            )

            assert "Actor One" in nfo_content
            assert "Actor Two" in nfo_content

    def test_generate_nfo_from_tmdb_creates_directory(self):
        """Test NFO generation creates parent directory if needed."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        cache_entry = Mock(spec=TMDBCache)
        cache_entry.tmdb_id = "12345"
        cache_entry.title = "Test Movie"
        cache_entry.year = 2023
        cache_entry.plot = "Test plot"
        cache_entry.cast = []
        cache_entry.ratings = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            nested_path = os.path.join(temp_dir, "subdir", "nested", "test.nfo")

            with patch.object(TMDBCache, 'get_cached', return_value=cache_entry):
                validator.generate_nfo_from_tmdb(
                    tmdb_id="12345",
                    release_name="Test.Movie.2023",
                    output_path=nested_path
                )

                assert os.path.exists(nested_path)

    def test_generate_nfo_from_tmdb_file_write_error(self):
        """Test NFO generation handles file write errors."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        cache_entry = Mock(spec=TMDBCache)
        cache_entry.tmdb_id = "12345"
        cache_entry.title = "Test Movie"
        cache_entry.year = 2023
        cache_entry.plot = "Test plot"
        cache_entry.cast = []
        cache_entry.ratings = {}

        with patch.object(TMDBCache, 'get_cached', return_value=cache_entry):
            # Invalid path that will cause write error
            with pytest.raises(TrackerAPIError) as exc_info:
                validator.generate_nfo_from_tmdb(
                    tmdb_id="12345",
                    release_name="Test.Movie.2023",
                    output_path="/invalid/path/that/cannot/be/created/test.nfo"
                )

            assert "failed to write" in str(exc_info.value).lower()


class TestEnsureValidNFO:
    """Test ensure_valid_nfo method (main pipeline entry point)."""

    def test_ensure_valid_nfo_existing_valid_file(self):
        """Test ensure_valid_nfo returns path when existing NFO is valid."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Create temporary media file and valid NFO
        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = os.path.join(temp_dir, "movie.mkv")
            nfo_path = os.path.join(temp_dir, "movie.nfo")

            # Create empty media file
            Path(media_path).touch()

            # Create valid NFO
            with open(nfo_path, 'w') as f:
                f.write("Title: Test Movie\n")
                f.write("Year: 2023\n")
                f.write("Plot: This is a comprehensive plot that meets all requirements.\n")

            result_path = validator.ensure_valid_nfo(
                file_path=media_path,
                tmdb_id="12345",
                release_name="Test.Movie.2023"
            )

            assert result_path == nfo_path

    def test_ensure_valid_nfo_generates_when_missing(self):
        """Test ensure_valid_nfo generates NFO when file doesn't exist."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Mock TMDB cache entry
        cache_entry = Mock(spec=TMDBCache)
        cache_entry.tmdb_id = "12345"
        cache_entry.title = "Test Movie"
        cache_entry.year = 2023
        cache_entry.plot = "This is a test movie plot."
        cache_entry.cast = []
        cache_entry.ratings = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = os.path.join(temp_dir, "movie.mkv")
            nfo_path = os.path.join(temp_dir, "movie.nfo")

            # Create media file only (no NFO)
            Path(media_path).touch()

            with patch.object(TMDBCache, 'get_cached', return_value=cache_entry):
                result_path = validator.ensure_valid_nfo(
                    file_path=media_path,
                    tmdb_id="12345",
                    release_name="Test.Movie.2023"
                )

                assert result_path == nfo_path
                assert os.path.exists(nfo_path)

    def test_ensure_valid_nfo_regenerates_when_invalid(self):
        """Test ensure_valid_nfo regenerates NFO when existing file is invalid."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        # Mock TMDB cache entry
        cache_entry = Mock(spec=TMDBCache)
        cache_entry.tmdb_id = "12345"
        cache_entry.title = "Test Movie"
        cache_entry.year = 2023
        cache_entry.plot = "This is a test movie plot."
        cache_entry.cast = []
        cache_entry.ratings = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = os.path.join(temp_dir, "movie.mkv")
            nfo_path = os.path.join(temp_dir, "movie.nfo")

            # Create media file
            Path(media_path).touch()

            # Create invalid NFO (too short)
            with open(nfo_path, 'w') as f:
                f.write("Invalid")

            with patch.object(TMDBCache, 'get_cached', return_value=cache_entry):
                result_path = validator.ensure_valid_nfo(
                    file_path=media_path,
                    tmdb_id="12345",
                    release_name="Test.Movie.2023"
                )

                assert result_path == nfo_path
                # Verify NFO was regenerated (contains TMDB data)
                with open(nfo_path, 'r') as f:
                    content = f.read()
                assert "Test Movie" in content

    def test_ensure_valid_nfo_fails_without_tmdb_id(self):
        """Test ensure_valid_nfo fails when NFO missing and no TMDB ID provided."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = os.path.join(temp_dir, "movie.mkv")
            Path(media_path).touch()

            with pytest.raises(TrackerAPIError) as exc_info:
                validator.ensure_valid_nfo(
                    file_path=media_path,
                    tmdb_id=None,  # No TMDB ID
                    release_name="Test.Movie.2023"
                )

            assert "tmdb id not provided" in str(exc_info.value).lower()

    def test_ensure_valid_nfo_uses_filename_for_release_name(self):
        """Test ensure_valid_nfo derives release name from filename when not provided."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        cache_entry = Mock(spec=TMDBCache)
        cache_entry.tmdb_id = "12345"
        cache_entry.title = "Test Movie"
        cache_entry.year = 2023
        cache_entry.plot = "Test plot"
        cache_entry.cast = []
        cache_entry.ratings = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = os.path.join(temp_dir, "Test.Movie.2023.1080p.mkv")
            nfo_path = os.path.join(temp_dir, "Test.Movie.2023.1080p.nfo")

            Path(media_path).touch()

            with patch.object(TMDBCache, 'get_cached', return_value=cache_entry):
                result_path = validator.ensure_valid_nfo(
                    file_path=media_path,
                    tmdb_id="12345"
                    # No release_name provided
                )

                assert result_path == nfo_path
                with open(nfo_path, 'r') as f:
                    content = f.read()
                # Should contain filename stem as release name
                assert "Test.Movie.2023.1080p" in content


class TestReadNFOContent:
    """Test read_nfo_content method."""

    def test_read_nfo_content_success(self):
        """Test reading NFO content from file."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        with tempfile.NamedTemporaryFile(mode='w', suffix='.nfo', delete=False) as f:
            test_content = "Test NFO content\nWith multiple lines"
            f.write(test_content)
            nfo_path = f.name

        try:
            content = validator.read_nfo_content(nfo_path)
            assert content == test_content
        finally:
            os.unlink(nfo_path)

    def test_read_nfo_content_file_not_found(self):
        """Test read_nfo_content returns None when file doesn't exist."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        content = validator.read_nfo_content("/nonexistent/file.nfo")
        assert content is None

    def test_read_nfo_content_handles_encoding_errors(self):
        """Test read_nfo_content handles encoding errors gracefully."""
        db = Mock(spec=Session)
        validator = NFOValidator(db)

        with tempfile.NamedTemporaryFile(mode='wb', suffix='.nfo', delete=False) as f:
            # Write binary content that might cause encoding issues
            f.write(b'\xff\xfe Invalid UTF-8 \x00')
            nfo_path = f.name

        try:
            # Should not raise exception (errors='ignore' parameter)
            content = validator.read_nfo_content(nfo_path)
            assert content is not None  # Should still read something
        finally:
            os.unlink(nfo_path)
