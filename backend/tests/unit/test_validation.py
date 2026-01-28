"""
Unit tests for Input Validation Module

Tests for backend/app/models/validators.py covering:
- URL validation (http/https schemes)
- Path traversal protection
- API key format validation
- Numeric range bounds validation
"""

import pytest
from backend.app.models.validators import (
    validate_url,
    validate_path_no_traversal,
    sanitize_path,
    validate_api_key,
    validate_numeric_range,
    validate_log_level,
    validate_passkey,
    path_validator,
    url_validator
)


class TestURLValidation:
    """Test cases for URL validation."""

    def test_url_validation_valid_https(self):
        """Valid HTTPS URLs should pass."""
        assert validate_url("https://example.com") is True
        assert validate_url("https://tracker.example.com/api") is True
        assert validate_url("https://192.168.1.1:8080") is True

    def test_url_validation_valid_http(self):
        """Valid HTTP URLs should pass."""
        assert validate_url("http://localhost:8191") is True
        assert validate_url("http://flaresolverr:8191/v1") is True

    def test_url_validation_invalid_format(self):
        """Invalid URL formats should fail."""
        assert validate_url("not-a-url") is False
        assert validate_url("") is False
        assert validate_url("example.com") is False  # Missing scheme
        assert validate_url("http://") is False  # Missing host

    def test_url_validation_invalid_schemes(self):
        """Non-http/https schemes should fail."""
        assert validate_url("ftp://files.example.com") is False
        assert validate_url("file:///path/to/file") is False
        assert validate_url("javascript:alert(1)") is False
        assert validate_url("data:text/plain,hello") is False

    def test_url_validation_with_paths_and_params(self):
        """URLs with paths and query params should pass."""
        assert validate_url("https://api.example.com/v1/endpoint?key=value") is True
        assert validate_url("http://localhost:8080/api/torrents#section") is True

    def test_url_validation_edge_cases(self):
        """Edge cases for URL validation."""
        assert validate_url(None) is False
        assert validate_url(123) is False  # Not a string
        assert validate_url("   https://example.com   ") is True  # Whitespace


class TestPathTraversalProtection:
    """Test cases for path traversal protection."""

    def test_path_traversal_blocked(self):
        """Path traversal attempts should be blocked."""
        assert validate_path_no_traversal("../../../etc/passwd") is False
        assert validate_path_no_traversal("/media/../../../etc/passwd") is False
        assert validate_path_no_traversal("..\\..\\windows\\system32") is False
        assert validate_path_no_traversal("folder/../../secret") is False

    def test_valid_paths_allowed(self):
        """Valid paths without traversal should pass."""
        assert validate_path_no_traversal("/media/movies") is True
        assert validate_path_no_traversal("C:\\Users\\Videos") is True
        assert validate_path_no_traversal("/home/user/downloads") is True
        assert validate_path_no_traversal("relative/path/to/file") is True

    def test_path_with_dots_in_names(self):
        """Paths with dots in file/folder names should pass."""
        assert validate_path_no_traversal("/media/movie.2024.1080p") is True
        assert validate_path_no_traversal("file.name.ext") is True
        assert validate_path_no_traversal("/path/to/.hidden_folder") is True

    def test_empty_and_null_paths(self):
        """Empty and null paths should fail."""
        assert validate_path_no_traversal("") is False
        assert validate_path_no_traversal(None) is False


class TestPathSanitization:
    """Test cases for path sanitization."""

    def test_path_sanitization_unicode(self):
        """Invisible Unicode characters should be removed."""
        # Path with Left-to-Right Mark (U+200E)
        assert sanitize_path("/media/\u200emovies") == "/media/movies"

        # Path with Zero Width Space (U+200B)
        assert sanitize_path("/media/\u200bmovies") == "/media/movies"

        # Path with BOM (U+FEFF)
        assert sanitize_path("\ufeff/media/movies") == "/media/movies"

    def test_path_sanitization_multiple_chars(self):
        """Multiple invisible characters should all be removed."""
        path = "\u200e\u200f/media/\u200bmovies\ufeff"
        result = sanitize_path(path)
        assert result == "/media/movies"

    def test_path_sanitization_no_change(self):
        """Clean paths should not be modified."""
        clean_path = "/media/movies/file.mkv"
        assert sanitize_path(clean_path) == clean_path

    def test_path_sanitization_none(self):
        """None input should return None."""
        assert sanitize_path(None) is None

    def test_path_sanitization_whitespace(self):
        """Trailing whitespace should be stripped."""
        assert sanitize_path("  /media/movies  ") == "/media/movies"


class TestAPIKeyValidation:
    """Test cases for API key format validation."""

    def test_api_key_format_valid(self):
        """Valid API key formats should pass."""
        assert validate_api_key("abc123def456ghi789") is True  # 18 chars
        assert validate_api_key("my_api_key_1234567") is True  # With underscore
        assert validate_api_key("api-key-with-dash12") is True  # With hyphen

    def test_api_key_format_too_short(self):
        """Short API keys should fail."""
        assert validate_api_key("short") is False
        assert validate_api_key("123456789012345") is False  # 15 chars

    def test_api_key_format_invalid_chars(self):
        """API keys with invalid characters should fail."""
        assert validate_api_key("has spaces not ok") is False
        assert validate_api_key("key@with#special!") is False

    def test_api_key_custom_min_length(self):
        """Custom minimum length should be respected."""
        assert validate_api_key("1234567890", min_length=10) is True
        assert validate_api_key("123456789", min_length=10) is False

    def test_api_key_bearer_token_format(self):
        """Bearer token (JWT) format should pass."""
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        assert validate_api_key(jwt) is True


class TestNumericRangeValidation:
    """Test cases for numeric range bounds."""

    def test_numeric_range_bounds(self):
        """Values within bounds should pass."""
        assert validate_numeric_range(10, min_value=1, max_value=100) is True
        assert validate_numeric_range(1, min_value=1, max_value=100) is True
        assert validate_numeric_range(100, min_value=1, max_value=100) is True

    def test_numeric_range_below_min(self):
        """Values below minimum should fail."""
        assert validate_numeric_range(0, min_value=1) is False
        assert validate_numeric_range(-5, min_value=0) is False

    def test_numeric_range_above_max(self):
        """Values above maximum should fail."""
        assert validate_numeric_range(500, max_value=365) is False
        assert validate_numeric_range(1000, max_value=100) is False

    def test_numeric_range_no_bounds(self):
        """No bounds means any value passes."""
        assert validate_numeric_range(0) is True
        assert validate_numeric_range(-1000) is True
        assert validate_numeric_range(999999) is True

    def test_numeric_range_only_min(self):
        """Only minimum bound."""
        assert validate_numeric_range(5, min_value=1) is True
        assert validate_numeric_range(0, min_value=1) is False

    def test_numeric_range_only_max(self):
        """Only maximum bound."""
        assert validate_numeric_range(5, max_value=10) is True
        assert validate_numeric_range(11, max_value=10) is False


class TestLogLevelValidation:
    """Test cases for log level validation."""

    def test_valid_log_levels(self):
        """Standard log levels should pass."""
        assert validate_log_level("DEBUG") is True
        assert validate_log_level("INFO") is True
        assert validate_log_level("WARNING") is True
        assert validate_log_level("ERROR") is True
        assert validate_log_level("CRITICAL") is True

    def test_case_insensitive_log_levels(self):
        """Log levels should be case-insensitive."""
        assert validate_log_level("debug") is True
        assert validate_log_level("Info") is True

    def test_invalid_log_levels(self):
        """Invalid log levels should fail."""
        assert validate_log_level("TRACE") is False
        assert validate_log_level("invalid") is False
        assert validate_log_level("") is False
        assert validate_log_level(None) is False


class TestPasskeyValidation:
    """Test cases for tracker passkey validation."""

    def test_valid_passkeys(self):
        """Valid passkey formats should pass."""
        # 32 character hex string
        assert validate_passkey("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4") is True
        # Alphanumeric passkey
        assert validate_passkey("abcdefghijklmnop") is True

    def test_short_passkeys(self):
        """Short passkeys should fail."""
        assert validate_passkey("short") is False
        assert validate_passkey("123456789012345") is False  # 15 chars

    def test_empty_passkeys(self):
        """Empty passkeys should fail."""
        assert validate_passkey("") is False
        assert validate_passkey(None) is False


class TestPydanticValidators:
    """Test cases for Pydantic validator functions."""

    def test_path_validator_valid(self):
        """Valid paths should pass through validator."""
        assert path_validator("/media/movies") == "/media/movies"
        assert path_validator(None) is None

    def test_path_validator_traversal_raises(self):
        """Path traversal should raise ValueError."""
        with pytest.raises(ValueError, match="Path traversal not allowed"):
            path_validator("../../../etc/passwd")

    def test_path_validator_sanitizes(self):
        """Validator should sanitize invisible chars."""
        result = path_validator("/media/\u200emovies")
        assert result == "/media/movies"

    def test_url_validator_valid(self):
        """Valid URLs should pass through validator."""
        assert url_validator("https://example.com") == "https://example.com"
        assert url_validator(None) is None
        assert url_validator("") == ""

    def test_url_validator_invalid_raises(self):
        """Invalid URLs should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid URL format"):
            url_validator("not-a-url")

    def test_url_validator_strips_whitespace(self):
        """Validator should strip whitespace."""
        result = url_validator("  https://example.com  ")
        assert result == "https://example.com"
