"""
Unit tests for Retry Mechanism

Tests for the @retry_on_network_error decorator from backend/app/services/exceptions.py
covering:
- Retry on timeout
- Max retry limits
- Exponential backoff
- No retry on client errors
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
import time

from backend.app.services.exceptions import (
    retry_on_network_error,
    NetworkRetryableError,
    CloudflareBypassError,
    TrackerAPIError,
    is_retryable_error,
    classify_http_error
)


class TestRetryOnTimeout:
    """Test retry behavior on timeout errors."""

    @pytest.mark.asyncio
    async def test_retry_on_timeout_success_after_retry(self):
        """Function should succeed after retry on timeout."""
        call_count = 0

        @retry_on_network_error(max_retries=3, base_delay=0.01)
        async def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise NetworkRetryableError("Timeout occurred")
            return "success"

        result = await flaky_function()

        assert result == "success"
        assert call_count == 2  # First attempt + 1 retry

    @pytest.mark.asyncio
    async def test_retry_on_cloudflare_error(self):
        """Function should retry on CloudflareBypassError."""
        call_count = 0

        @retry_on_network_error(max_retries=3, base_delay=0.01)
        async def cloudflare_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise CloudflareBypassError("FlareSolverr timeout")
            return "bypassed"

        result = await cloudflare_function()

        assert result == "bypassed"
        assert call_count == 3


class TestMaxRetryLimits:
    """Test that max retry limits are respected."""

    @pytest.mark.asyncio
    async def test_retry_respects_max_retries(self):
        """Function should stop after max retries and raise."""
        call_count = 0

        @retry_on_network_error(max_retries=3, base_delay=0.01)
        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise NetworkRetryableError("Always fails")

        with pytest.raises(NetworkRetryableError):
            await always_fails()

        # Initial attempt (1) + 3 retries = 4 total calls
        assert call_count == 4

    @pytest.mark.asyncio
    async def test_retry_with_max_retries_zero(self):
        """With max_retries=0, no retries should happen."""
        call_count = 0

        @retry_on_network_error(max_retries=0, base_delay=0.01)
        async def no_retry_function():
            nonlocal call_count
            call_count += 1
            raise NetworkRetryableError("Fails immediately")

        with pytest.raises(NetworkRetryableError):
            await no_retry_function()

        assert call_count == 1  # Only initial attempt


class TestExponentialBackoff:
    """Test exponential backoff delay calculation."""

    @pytest.mark.asyncio
    async def test_retry_exponential_backoff(self):
        """Delays should increase exponentially."""
        delays = []
        call_count = 0

        # Mock asyncio.sleep to capture delays
        original_sleep = asyncio.sleep

        async def mock_sleep(delay):
            delays.append(delay)
            await original_sleep(0.001)  # Minimal actual delay

        @retry_on_network_error(max_retries=3, base_delay=1.0, exponential_base=2)
        async def function_with_delays():
            nonlocal call_count
            call_count += 1
            raise NetworkRetryableError("Retry me")

        with patch('asyncio.sleep', mock_sleep):
            with pytest.raises(NetworkRetryableError):
                await function_with_delays()

        # Expected delays: 1.0, 2.0, 4.0 (base * 2^attempt)
        assert len(delays) == 3
        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(2.0)
        assert delays[2] == pytest.approx(4.0)

    @pytest.mark.asyncio
    async def test_retry_max_delay_cap(self):
        """Delays should be capped at max_delay."""
        delays = []

        async def mock_sleep(delay):
            delays.append(delay)

        @retry_on_network_error(
            max_retries=5,
            base_delay=10.0,
            max_delay=30.0,
            exponential_base=2
        )
        async def function_with_cap():
            raise NetworkRetryableError("Retry me")

        with patch('asyncio.sleep', mock_sleep):
            with pytest.raises(NetworkRetryableError):
                await function_with_cap()

        # Expected: 10, 20, 30, 30, 30 (capped at max_delay=30)
        assert delays[0] == pytest.approx(10.0)
        assert delays[1] == pytest.approx(20.0)
        # All subsequent should be capped at 30
        for delay in delays[2:]:
            assert delay == pytest.approx(30.0)


class TestNoRetryOnClientError:
    """Test that client errors don't trigger retries."""

    @pytest.mark.asyncio
    async def test_no_retry_on_client_error(self):
        """TrackerAPIError (non-retryable) should not be retried."""
        call_count = 0

        @retry_on_network_error(max_retries=5, base_delay=0.01)
        async def client_error_function():
            nonlocal call_count
            call_count += 1
            raise TrackerAPIError("Invalid request", status_code=400)

        with pytest.raises(TrackerAPIError):
            await client_error_function()

        # Only 1 call - no retries for non-retryable errors
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_unexpected_error(self):
        """Unexpected exceptions should not be retried."""
        call_count = 0

        @retry_on_network_error(max_retries=5, base_delay=0.01)
        async def unexpected_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("Unexpected error")

        with pytest.raises(ValueError):
            await unexpected_error()

        assert call_count == 1


class TestRetryAfterHeader:
    """Test respect for retry_after suggestion."""

    @pytest.mark.asyncio
    async def test_retry_uses_retry_after(self):
        """Should use retry_after from exception when provided."""
        delays = []

        async def mock_sleep(delay):
            delays.append(delay)

        @retry_on_network_error(max_retries=2, base_delay=1.0, max_delay=60.0)
        async def function_with_retry_after():
            raise NetworkRetryableError("Rate limited", retry_after=10)

        with patch('asyncio.sleep', mock_sleep):
            with pytest.raises(NetworkRetryableError):
                await function_with_retry_after()

        # Should use retry_after=10 instead of calculated delay
        assert delays[0] == pytest.approx(10.0)


class TestSyncFunction:
    """Test decorator with synchronous functions."""

    def test_sync_retry_on_network_error(self):
        """Sync functions should also be retried."""
        call_count = 0

        @retry_on_network_error(max_retries=2, base_delay=0.01)
        def sync_flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise NetworkRetryableError("Sync timeout")
            return "sync_success"

        result = sync_flaky()

        assert result == "sync_success"
        assert call_count == 2


class TestHelperFunctions:
    """Test helper functions for error classification."""

    def test_is_retryable_error_network(self):
        """NetworkRetryableError should be retryable."""
        error = NetworkRetryableError("Network error")
        assert is_retryable_error(error) is True

    def test_is_retryable_error_cloudflare(self):
        """CloudflareBypassError should be retryable."""
        error = CloudflareBypassError("Bypass failed")
        assert is_retryable_error(error) is True

    def test_is_retryable_error_tracker(self):
        """TrackerAPIError should NOT be retryable."""
        error = TrackerAPIError("API error")
        assert is_retryable_error(error) is False

    def test_is_retryable_error_standard(self):
        """Standard exceptions should NOT be retryable."""
        assert is_retryable_error(ValueError("test")) is False
        assert is_retryable_error(Exception("test")) is False


class TestHTTPErrorClassification:
    """Test classify_http_error function."""

    def test_classify_429_as_retryable(self):
        """HTTP 429 (rate limit) should be retryable."""
        error = classify_http_error(429, "Rate limited")
        assert isinstance(error, NetworkRetryableError)

    def test_classify_503_as_retryable(self):
        """HTTP 503 (service unavailable) should be retryable."""
        error = classify_http_error(503, "Service down")
        assert isinstance(error, NetworkRetryableError)

    def test_classify_502_as_retryable(self):
        """HTTP 502 (bad gateway) should be retryable."""
        error = classify_http_error(502, "Gateway error")
        assert isinstance(error, NetworkRetryableError)

    def test_classify_504_as_retryable(self):
        """HTTP 504 (gateway timeout) should be retryable."""
        error = classify_http_error(504, "Gateway timeout")
        assert isinstance(error, NetworkRetryableError)

    def test_classify_400_as_non_retryable(self):
        """HTTP 400 (bad request) should NOT be retryable."""
        error = classify_http_error(400, "Bad request")
        assert isinstance(error, TrackerAPIError)
        assert not isinstance(error, NetworkRetryableError)

    def test_classify_401_as_non_retryable(self):
        """HTTP 401 (unauthorized) should NOT be retryable."""
        error = classify_http_error(401, "Unauthorized")
        assert isinstance(error, TrackerAPIError)
        assert not isinstance(error, NetworkRetryableError)

    def test_classify_404_as_non_retryable(self):
        """HTTP 404 (not found) should NOT be retryable."""
        error = classify_http_error(404, "Not found")
        assert isinstance(error, TrackerAPIError)
        assert not isinstance(error, NetworkRetryableError)

    def test_classify_500_as_non_retryable(self):
        """HTTP 500 (internal server error) should NOT be retryable by default."""
        error = classify_http_error(500, "Server error")
        assert isinstance(error, TrackerAPIError)
        # 500 is not in the retryable list (502, 503, 504 are)

    def test_classify_preserves_retry_after(self):
        """Should extract retry_after from response data."""
        error = classify_http_error(
            429,
            "Rate limited",
            response_data={'retry_after': 30}
        )
        assert isinstance(error, NetworkRetryableError)
        assert error.retry_after == 30
