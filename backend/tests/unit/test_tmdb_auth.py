"""Unit tests for TMDB Authentication Utility"""
import pytest
from backend.app.utils.tmdb_auth import detect_tmdb_credential_type, format_tmdb_request


class TestDetectCredentialType:
    """Test credential type detection logic."""

    def test_detect_credential_type_v3(self):
        """Test v3 API key detection (32-char alphanumeric)."""
        api_key = "df667ef7a7f9009def29e0bd78725f3d"
        result = detect_tmdb_credential_type(api_key)
        assert result == "v3"

    def test_detect_credential_type_v4(self):
        """Test v4 Bearer token detection (JWT format)."""
        token = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJkZjY2N2VmN2E3ZjkwMDlkZWYyOWUwYmQ3ODcyNWYzZCIsIm5iZiI6MTc2Njk1OTQxNS4yOTQwMDAxLCJzdWIiOiI2OTUxYTkzNzVlMTNmZGY1OGRjYmQyNDUiLCJzY29wZXMiOlsiYXBpX3JlYWQiXSwidmVyc2lvbiI6MX0.V7mt6SEpZ8BZgXuPqUNFbNGyBJjRxMd_8bxy-1As9a4"
        result = detect_tmdb_credential_type(token)
        assert result == "v4"

    def test_invalid_credential_format_short(self):
        """Test invalid credential format (too short) raises ValueError."""
        with pytest.raises(ValueError, match="Invalid TMDB credential format"):
            detect_tmdb_credential_type("short")

    def test_invalid_credential_format_wrong_length(self):
        """Test invalid credential format (wrong length) raises ValueError."""
        with pytest.raises(ValueError, match="Invalid TMDB credential format"):
            detect_tmdb_credential_type("invalid_format_123456789012")

    def test_empty_credential(self):
        """Test empty credential raises ValueError."""
        with pytest.raises(ValueError, match="Invalid TMDB credential format"):
            detect_tmdb_credential_type("")

    def test_none_credential(self):
        """Test None credential raises ValueError."""
        with pytest.raises(ValueError, match="Invalid TMDB credential format"):
            detect_tmdb_credential_type(None)

    def test_non_alphanumeric_v3_format(self):
        """Test 32-char non-alphanumeric string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid TMDB credential format"):
            detect_tmdb_credential_type("df667ef7-a7f9-009d-ef29-e0bd78725f3d")  # Has dashes


class TestFormatTMDBRequest:
    """Test request formatting for different credential types."""

    def test_v3_authentication_query_param(self):
        """Test v3 credentials added as query parameter."""
        api_key = "df667ef7a7f9009def29e0bd78725f3d"
        params, headers = format_tmdb_request(api_key)

        assert params == {"api_key": api_key}
        assert headers == {}

    def test_v4_authentication_header(self):
        """Test v4 credentials added as Authorization header."""
        token = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJkZjY2N2VmN2E3ZjkwMDlkZWYyOWUwYmQ3ODcyNWYzZCIsIm5iZiI6MTc2Njk1OTQxNS4yOTQwMDAxLCJzdWIiOiI2OTUxYTkzNzVlMTNmZGY1OGRjYmQyNDUiLCJzY29wZXMiOlsiYXBpX3JlYWQiXSwidmVyc2lvbiI6MX0.V7mt6SEpZ8BZgXuPqUNFbNGyBJjRxMd_8bxy-1As9a4"
        params, headers = format_tmdb_request(token)

        assert params == {}
        assert headers == {"Authorization": f"Bearer {token}"}

    def test_bearer_prefix_correct(self):
        """Test Bearer prefix is correctly added to v4 token."""
        token = "eyJhbGciOiJIUzI1NiJ9.test"
        params, headers = format_tmdb_request(token)

        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Authorization"] == f"Bearer {token}"

    def test_invalid_format_raises_error(self):
        """Test invalid format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid TMDB credential format"):
            format_tmdb_request("invalid_format")

    def test_empty_format_raises_error(self):
        """Test empty credential raises ValueError."""
        with pytest.raises(ValueError):
            format_tmdb_request("")
