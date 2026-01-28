"""
Typed Exception Hierarchy for Seedarr v2.0

This module defines a hierarchy of exceptions for handling various error conditions
in the torrent publishing pipeline, with support for automatic retry logic with
exponential backoff for retryable errors.

Exception Hierarchy:
    TrackerAPIError (base, non-retryable)
    ├── CloudflareBypassError (retryable)
    └── NetworkRetryableError (retryable with exponential backoff)

The @retry_on_network_error decorator provides automatic retry logic with:
    - Maximum 5 retries
    - Exponential backoff: 2^n seconds delay
    - Comprehensive logging of retry attempts
"""

import asyncio
import functools
import logging
import time
from typing import Callable, TypeVar, ParamSpec, Any

logger = logging.getLogger(__name__)

# Type variables for generic decorator typing
P = ParamSpec('P')
T = TypeVar('T')


# ============================================================================
# Exception Hierarchy
# ============================================================================

class TrackerAPIError(Exception):
    """
    Base exception for tracker API errors (non-retryable).

    Use this for business logic errors that should fail fast:
    - Invalid passkey/authentication
    - Invalid request parameters
    - Resource not found
    - Permission denied

    These errors indicate client-side issues that won't be resolved by retrying.
    """

    def __init__(self, message: str, status_code: int = None, response_data: dict = None):
        """
        Initialize TrackerAPIError.

        Args:
            message: Human-readable error description
            status_code: HTTP status code if applicable
            response_data: Raw response data for debugging
        """
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_data = response_data

    def __str__(self) -> str:
        if self.status_code:
            return f"{self.__class__.__name__} (HTTP {self.status_code}): {self.message}"
        return f"{self.__class__.__name__}: {self.message}"


class CloudflareBypassError(TrackerAPIError):
    """
    Exception for Cloudflare bypass failures (retryable).

    Raised when FlareSolverr service:
    - Is unreachable/unavailable
    - Times out during challenge solving
    - Returns invalid/corrupted response
    - Fails to extract cookies

    These errors are typically transient and should be retried with backoff.
    """

    def __init__(self, message: str, flaresolverr_response: dict = None):
        """
        Initialize CloudflareBypassError.

        Args:
            message: Human-readable error description
            flaresolverr_response: Raw FlareSolverr response for debugging
        """
        super().__init__(message, response_data=flaresolverr_response)
        self.flaresolverr_response = flaresolverr_response


class NetworkRetryableError(TrackerAPIError):
    """
    Exception for network-level errors that should be retried (retryable with exponential backoff).

    Use this for transient network issues:
    - Connection timeouts
    - DNS resolution failures
    - Temporary service unavailability (HTTP 503)
    - Rate limiting (HTTP 429)
    - Network socket errors

    The @retry_on_network_error decorator will automatically retry these errors
    with exponential backoff up to 5 attempts.
    """

    def __init__(self, message: str, original_exception: Exception = None, retry_after: int = None):
        """
        Initialize NetworkRetryableError.

        Args:
            message: Human-readable error description
            original_exception: Original exception that triggered this error
            retry_after: Suggested retry delay in seconds (e.g., from Retry-After header)
        """
        super().__init__(message)
        self.original_exception = original_exception
        self.retry_after = retry_after


class RateLimitExceeded(TrackerAPIError):
    """
    Exception raised when a rate limit is exceeded.

    This is a specialized exception for when the application's internal
    rate limiter blocks a request before it reaches the external API.

    Attributes:
        service: The service that was rate limited
        retry_after: Seconds to wait before retrying
    """

    def __init__(self, service: str, retry_after: float, message: str = None):
        """
        Initialize RateLimitExceeded.

        Args:
            service: Name of the rate-limited service (e.g., "tmdb", "tracker")
            retry_after: Time in seconds until the rate limit resets
            message: Optional custom message
        """
        msg = message or f"Rate limit exceeded for {service}. Retry after {retry_after:.1f}s"
        super().__init__(msg)
        self.service = service
        self.retry_after = retry_after


# ============================================================================
# Retry Decorator
# ============================================================================

def retry_on_network_error(
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: int = 2,
    retryable_exceptions: tuple = (NetworkRetryableError, CloudflareBypassError)
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator for automatic retry with exponential backoff on network errors.

    Retries functions that raise NetworkRetryableError or CloudflareBypassError
    with exponential backoff: delay = min(base_delay * (exponential_base ^ attempt), max_delay)

    Default behavior:
        - Max retries: 5
        - Delay progression: 1s, 2s, 4s, 8s, 16s (capped at max_delay)
        - Retryable exceptions: NetworkRetryableError, CloudflareBypassError
        - Non-retryable exceptions: TrackerAPIError (base class)

    Args:
        max_retries: Maximum number of retry attempts (default: 5)
        base_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay in seconds (default: 60.0)
        exponential_base: Base for exponential calculation (default: 2)
        retryable_exceptions: Tuple of exception types to retry

    Returns:
        Decorated function with retry logic

    Example:
        @retry_on_network_error(max_retries=3)
        async def upload_to_tracker(data):
            # This will retry up to 3 times on NetworkRetryableError
            response = await tracker_api.upload(data)
            return response
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        # Detect if function is async or sync
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                last_exception = None

                for attempt in range(max_retries + 1):
                    try:
                        return await func(*args, **kwargs)

                    except retryable_exceptions as e:
                        last_exception = e

                        # Don't retry on the last attempt
                        if attempt >= max_retries:
                            logger.error(
                                f"Max retries ({max_retries}) exceeded for {func.__name__}. "
                                f"Final error: {e}"
                            )
                            raise

                        # Calculate exponential backoff delay
                        delay = min(
                            base_delay * (exponential_base ** attempt),
                            max_delay
                        )

                        # Use retry_after if suggested by the exception
                        if isinstance(e, NetworkRetryableError) and e.retry_after:
                            delay = min(e.retry_after, max_delay)

                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {e}. "
                            f"Retrying in {delay}s..."
                        )

                        # Async sleep
                        await asyncio.sleep(delay)

                    except TrackerAPIError as e:
                        # Non-retryable TrackerAPIError - fail fast
                        logger.error(
                            f"Non-retryable error in {func.__name__}: {e}. "
                            f"Not retrying."
                        )
                        raise

                    except Exception as e:
                        # Unexpected error - log and re-raise without retry
                        logger.error(
                            f"Unexpected error in {func.__name__}: {type(e).__name__}: {e}. "
                            f"Not retrying."
                        )
                        raise

                # This should never be reached, but for type safety
                if last_exception:
                    raise last_exception

            return async_wrapper

        else:
            @functools.wraps(func)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                last_exception = None

                for attempt in range(max_retries + 1):
                    try:
                        return func(*args, **kwargs)

                    except retryable_exceptions as e:
                        last_exception = e

                        # Don't retry on the last attempt
                        if attempt >= max_retries:
                            logger.error(
                                f"Max retries ({max_retries}) exceeded for {func.__name__}. "
                                f"Final error: {e}"
                            )
                            raise

                        # Calculate exponential backoff delay
                        delay = min(
                            base_delay * (exponential_base ** attempt),
                            max_delay
                        )

                        # Use retry_after if suggested by the exception
                        if isinstance(e, NetworkRetryableError) and e.retry_after:
                            delay = min(e.retry_after, max_delay)

                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {e}. "
                            f"Retrying in {delay}s..."
                        )

                        # Sync sleep
                        time.sleep(delay)

                    except TrackerAPIError as e:
                        # Non-retryable TrackerAPIError - fail fast
                        logger.error(
                            f"Non-retryable error in {func.__name__}: {e}. "
                            f"Not retrying."
                        )
                        raise

                    except Exception as e:
                        # Unexpected error - log and re-raise without retry
                        logger.error(
                            f"Unexpected error in {func.__name__}: {type(e).__name__}: {e}. "
                            f"Not retrying."
                        )
                        raise

                # This should never be reached, but for type safety
                if last_exception:
                    raise last_exception

            return sync_wrapper

    return decorator


# ============================================================================
# Convenience Functions
# ============================================================================

def is_retryable_error(exception: Exception) -> bool:
    """
    Check if an exception is retryable.

    Args:
        exception: Exception to check

    Returns:
        True if exception should be retried, False otherwise
    """
    return isinstance(exception, (NetworkRetryableError, CloudflareBypassError))


def classify_http_error(status_code: int, message: str, response_data: dict = None) -> TrackerAPIError:
    """
    Classify HTTP errors into appropriate exception types.

    Args:
        status_code: HTTP status code
        message: Error message
        response_data: Optional response data for debugging

    Returns:
        Appropriate exception instance based on status code
    """
    # 429 Rate Limiting - retryable
    if status_code == 429:
        retry_after = None
        if response_data and 'retry_after' in response_data:
            retry_after = int(response_data['retry_after'])
        return NetworkRetryableError(
            message=f"Rate limited: {message}",
            retry_after=retry_after
        )

    # 503 Service Unavailable - retryable
    if status_code == 503:
        return NetworkRetryableError(
            message=f"Service temporarily unavailable: {message}"
        )

    # 502, 504 Gateway errors - retryable
    if status_code in (502, 504):
        return NetworkRetryableError(
            message=f"Gateway error (HTTP {status_code}): {message}"
        )

    # 4xx Client errors (except 429) - non-retryable
    if 400 <= status_code < 500:
        return TrackerAPIError(
            message=message,
            status_code=status_code,
            response_data=response_data
        )

    # 5xx Server errors (except 502, 503, 504) - non-retryable by default
    # (server-side bugs unlikely to be resolved by retry)
    return TrackerAPIError(
        message=message,
        status_code=status_code,
        response_data=response_data
    )
