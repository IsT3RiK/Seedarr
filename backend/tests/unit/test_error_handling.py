"""
Unit tests for Error Handling

Tests for exception hierarchy and error handling in:
- backend/app/services/exceptions.py
- backend/app/processors/pipeline.py

Covering:
- Error classification
- Exception inheritance
- Pipeline error propagation
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from sqlalchemy.orm import Session

from backend.app.services.exceptions import (
    TrackerAPIError,
    CloudflareBypassError,
    NetworkRetryableError,
    is_retryable_error
)


class TestNetworkErrorClassification:
    """Test network error type classification."""

    def test_network_error_is_tracker_subclass(self):
        """NetworkRetryableError should be a TrackerAPIError subclass."""
        error = NetworkRetryableError("Network error")
        assert isinstance(error, TrackerAPIError)

    def test_cloudflare_error_is_tracker_subclass(self):
        """CloudflareBypassError should be a TrackerAPIError subclass."""
        error = CloudflareBypassError("Bypass failed")
        assert isinstance(error, TrackerAPIError)

    def test_network_error_classification(self):
        """Network errors should be classified correctly."""
        # Timeout error
        timeout_error = NetworkRetryableError("Connection timeout")
        assert is_retryable_error(timeout_error) is True

        # Connection error
        connect_error = NetworkRetryableError("Connection refused")
        assert is_retryable_error(connect_error) is True

        # DNS error
        dns_error = NetworkRetryableError("DNS resolution failed")
        assert is_retryable_error(dns_error) is True

    def test_tracker_error_non_retryable(self):
        """Base TrackerAPIError should not be retryable."""
        # Invalid credentials
        auth_error = TrackerAPIError("Invalid passkey", status_code=401)
        assert is_retryable_error(auth_error) is False

        # Bad request
        bad_request = TrackerAPIError("Invalid category ID", status_code=400)
        assert is_retryable_error(bad_request) is False

        # Forbidden
        forbidden = TrackerAPIError("Access denied", status_code=403)
        assert is_retryable_error(forbidden) is False


class TestExceptionProperties:
    """Test exception property access."""

    def test_tracker_api_error_properties(self):
        """TrackerAPIError should expose all properties."""
        error = TrackerAPIError(
            message="Test error",
            status_code=404,
            response_data={"error": "Not found"}
        )

        assert error.message == "Test error"
        assert error.status_code == 404
        assert error.response_data == {"error": "Not found"}
        assert "404" in str(error)
        assert "Test error" in str(error)

    def test_network_error_properties(self):
        """NetworkRetryableError should expose retry_after."""
        original = Exception("Original error")
        error = NetworkRetryableError(
            message="Rate limited",
            original_exception=original,
            retry_after=30
        )

        assert error.message == "Rate limited"
        assert error.original_exception == original
        assert error.retry_after == 30

    def test_cloudflare_error_properties(self):
        """CloudflareBypassError should expose FlareSolverr response."""
        response = {"status": "error", "message": "Timeout"}
        error = CloudflareBypassError(
            message="Bypass timeout",
            flaresolverr_response=response
        )

        assert error.message == "Bypass timeout"
        assert error.flaresolverr_response == response
        assert error.response_data == response  # Inherited property


class TestErrorStringRepresentation:
    """Test error string representations."""

    def test_tracker_error_str_with_status(self):
        """TrackerAPIError str should include status code."""
        error = TrackerAPIError("Not found", status_code=404)
        error_str = str(error)

        assert "TrackerAPIError" in error_str
        assert "404" in error_str
        assert "Not found" in error_str

    def test_tracker_error_str_without_status(self):
        """TrackerAPIError str without status code."""
        error = TrackerAPIError("General error")
        error_str = str(error)

        assert "TrackerAPIError" in error_str
        assert "General error" in error_str

    def test_network_error_str(self):
        """NetworkRetryableError str representation."""
        error = NetworkRetryableError("Connection timeout")
        error_str = str(error)

        assert "NetworkRetryableError" in error_str
        assert "Connection timeout" in error_str


class TestExceptionHierarchy:
    """Test exception class hierarchy."""

    def test_inheritance_chain(self):
        """Verify inheritance chain is correct."""
        # TrackerAPIError -> Exception
        assert issubclass(TrackerAPIError, Exception)

        # CloudflareBypassError -> TrackerAPIError -> Exception
        assert issubclass(CloudflareBypassError, TrackerAPIError)
        assert issubclass(CloudflareBypassError, Exception)

        # NetworkRetryableError -> TrackerAPIError -> Exception
        assert issubclass(NetworkRetryableError, TrackerAPIError)
        assert issubclass(NetworkRetryableError, Exception)

    def test_catch_by_base_class(self):
        """Subclass errors should be catchable by base class."""
        # CloudflareBypassError caught by TrackerAPIError
        with pytest.raises(TrackerAPIError):
            raise CloudflareBypassError("Test")

        # NetworkRetryableError caught by TrackerAPIError
        with pytest.raises(TrackerAPIError):
            raise NetworkRetryableError("Test")

    def test_catch_specific_types(self):
        """Specific error types should be catchable separately."""
        # CloudflareBypassError should not be caught as NetworkRetryableError
        try:
            raise CloudflareBypassError("Test")
        except NetworkRetryableError:
            pytest.fail("CloudflareBypassError should not be NetworkRetryableError")
        except CloudflareBypassError:
            pass  # Expected

        # NetworkRetryableError should not be caught as CloudflareBypassError
        try:
            raise NetworkRetryableError("Test")
        except CloudflareBypassError:
            pytest.fail("NetworkRetryableError should not be CloudflareBypassError")
        except NetworkRetryableError:
            pass  # Expected


class TestPipelineErrorPropagation:
    """Test error propagation in pipeline context."""

    def test_retryable_error_preserved(self):
        """Retryable errors should preserve their type through propagation."""
        original = NetworkRetryableError("Original timeout")

        # Simulating pipeline catch and re-raise
        try:
            try:
                raise original
            except (NetworkRetryableError, CloudflareBypassError) as e:
                # Pipeline preserves retryable errors
                raise
        except NetworkRetryableError as caught:
            assert caught is original
            assert is_retryable_error(caught) is True

    def test_non_retryable_error_preserved(self):
        """Non-retryable errors should preserve their type."""
        original = TrackerAPIError("Invalid request", status_code=400)

        try:
            try:
                raise original
            except TrackerAPIError as e:
                raise
        except TrackerAPIError as caught:
            assert caught is original
            assert is_retryable_error(caught) is False

    def test_unexpected_error_wrapped(self):
        """Unexpected errors should be wrapped in TrackerAPIError."""
        original = ValueError("Unexpected")

        try:
            try:
                raise original
            except Exception as e:
                raise TrackerAPIError(f"Wrapped: {e}") from e
        except TrackerAPIError as wrapped:
            assert "Wrapped" in str(wrapped)
            assert wrapped.__cause__ is original


class TestErrorScenarios:
    """Test common error scenarios."""

    def test_authentication_error_scenario(self):
        """Invalid passkey should be non-retryable."""
        error = TrackerAPIError(
            "Authentication failed - invalid passkey",
            status_code=401
        )
        assert is_retryable_error(error) is False
        assert error.status_code == 401

    def test_rate_limit_error_scenario(self):
        """Rate limiting should be retryable with retry_after."""
        error = NetworkRetryableError(
            "Rate limit exceeded",
            retry_after=60
        )
        assert is_retryable_error(error) is True
        assert error.retry_after == 60

    def test_cloudflare_challenge_timeout(self):
        """Cloudflare challenge timeout should be retryable."""
        error = CloudflareBypassError(
            "FlareSolverr timeout solving challenge",
            flaresolverr_response={"status": "timeout"}
        )
        assert is_retryable_error(error) is True
        assert error.flaresolverr_response["status"] == "timeout"

    def test_network_timeout_scenario(self):
        """Network timeout should be retryable."""
        import socket
        original = socket.timeout("Connection timed out")
        error = NetworkRetryableError(
            "Request timeout",
            original_exception=original
        )
        assert is_retryable_error(error) is True
        assert error.original_exception is original

    def test_connection_refused_scenario(self):
        """Connection refused should be retryable."""
        error = NetworkRetryableError("Connection refused to tracker")
        assert is_retryable_error(error) is True

    def test_invalid_category_scenario(self):
        """Invalid category should be non-retryable."""
        error = TrackerAPIError(
            "Category ID 999 does not exist",
            status_code=400,
            response_data={"error": "invalid_category"}
        )
        assert is_retryable_error(error) is False
        assert error.response_data["error"] == "invalid_category"


class TestErrorCombinations:
    """Test handling of multiple error types."""

    def test_catch_order_matters(self):
        """More specific exceptions should be caught first."""
        def raise_network_error():
            raise NetworkRetryableError("Network issue")

        # Catch specific first
        try:
            raise_network_error()
        except NetworkRetryableError as e:
            caught_type = "network"
        except TrackerAPIError as e:
            caught_type = "tracker"

        assert caught_type == "network"

    def test_tuple_exception_catching(self):
        """Multiple exception types in tuple catch."""
        errors = [
            NetworkRetryableError("Network"),
            CloudflareBypassError("Cloudflare")
        ]

        for error in errors:
            try:
                raise error
            except (NetworkRetryableError, CloudflareBypassError):
                assert is_retryable_error(error) is True
