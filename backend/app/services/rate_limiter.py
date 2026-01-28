"""
Rate Limiter Service

Provides token bucket rate limiting for external API calls.

Features:
- Token bucket algorithm with configurable rates
- Async-safe with asyncio locks
- Per-service rate limits
- Decorator for easy application
- Configurable burst allowance
"""

import asyncio
import functools
import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, Callable, TypeVar, ParamSpec, Any

from app.services.exceptions import RateLimitExceeded

logger = logging.getLogger(__name__)

P = ParamSpec('P')
T = TypeVar('T')


@dataclass
class RateLimitConfig:
    """Configuration for a rate limiter."""
    tokens_per_second: float  # Refill rate
    max_tokens: int  # Maximum bucket capacity (burst allowance)
    name: str = ""  # Service name for logging

    @property
    def refill_interval(self) -> float:
        """Time between token refills in seconds."""
        return 1.0 / self.tokens_per_second if self.tokens_per_second > 0 else float('inf')


class TokenBucket:
    """
    Token bucket rate limiter.

    Implements the token bucket algorithm for rate limiting:
    - Bucket has a maximum capacity (burst allowance)
    - Tokens refill at a constant rate
    - Each request consumes one or more tokens
    - Requests are blocked when bucket is empty
    """

    def __init__(self, config: RateLimitConfig):
        """
        Initialize token bucket.

        Args:
            config: Rate limit configuration
        """
        self.config = config
        self._tokens = float(config.max_tokens)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        tokens_to_add = elapsed * self.config.tokens_per_second
        self._tokens = min(self.config.max_tokens, self._tokens + tokens_to_add)
        self._last_refill = now

    async def acquire(self, tokens: int = 1, wait: bool = True, timeout: float = 30.0) -> bool:
        """
        Acquire tokens from the bucket.

        Args:
            tokens: Number of tokens to acquire
            wait: If True, wait for tokens. If False, return immediately.
            timeout: Maximum time to wait for tokens (seconds)

        Returns:
            True if tokens were acquired, False if not (only when wait=False)

        Raises:
            RateLimitExceeded: When wait=True and timeout is exceeded
        """
        start_time = time.monotonic()

        async with self._lock:
            while True:
                self._refill()

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True

                if not wait:
                    return False

                # Calculate wait time until enough tokens are available
                tokens_needed = tokens - self._tokens
                wait_time = tokens_needed / self.config.tokens_per_second

                # Check timeout
                elapsed = time.monotonic() - start_time
                if elapsed + wait_time > timeout:
                    raise RateLimitExceeded(
                        service=self.config.name,
                        retry_after=wait_time
                    )

                # Release lock and wait
                # Note: This is a simplification - in a real implementation
                # you'd want to use asyncio.wait_for with proper timeout handling
                await asyncio.sleep(min(wait_time, 0.1))

    @property
    def available_tokens(self) -> float:
        """Get current available tokens (without acquiring lock)."""
        return self._tokens

    @property
    def time_until_available(self) -> float:
        """Get estimated time until at least 1 token is available."""
        if self._tokens >= 1:
            return 0.0
        return (1 - self._tokens) / self.config.tokens_per_second


class RateLimiter:
    """
    Multi-service rate limiter manager.

    Manages rate limiters for multiple services with configurable limits.
    """

    # Default rate limits per service
    DEFAULT_LIMITS = {
        "tmdb": RateLimitConfig(
            tokens_per_second=4.0,  # 40 requests per 10 seconds
            max_tokens=10,  # Allow burst of 10
            name="tmdb"
        ),
        "tracker": RateLimitConfig(
            tokens_per_second=1.0,  # 1 request per second
            max_tokens=5,  # Allow burst of 5
            name="tracker"
        ),
        "flaresolverr": RateLimitConfig(
            tokens_per_second=0.5,  # 1 request per 2 seconds (FlareSolverr is slow)
            max_tokens=2,
            name="flaresolverr"
        ),
        "qbittorrent": RateLimitConfig(
            tokens_per_second=5.0,  # 5 requests per second
            max_tokens=10,
            name="qbittorrent"
        ),
        "prowlarr": RateLimitConfig(
            tokens_per_second=2.0,  # 2 requests per second
            max_tokens=5,
            name="prowlarr"
        ),
        "imgbb": RateLimitConfig(
            tokens_per_second=1.0,  # 1 request per second
            max_tokens=3,
            name="imgbb"
        )
    }

    def __init__(self):
        """Initialize rate limiter with default limits."""
        self._buckets: Dict[str, TokenBucket] = {}
        self._custom_configs: Dict[str, RateLimitConfig] = {}

    def get_bucket(self, service: str) -> TokenBucket:
        """
        Get or create token bucket for a service.

        Args:
            service: Service name

        Returns:
            TokenBucket for the service
        """
        if service not in self._buckets:
            # Use custom config if set, otherwise use default
            config = self._custom_configs.get(service)
            if not config:
                config = self.DEFAULT_LIMITS.get(
                    service,
                    RateLimitConfig(
                        tokens_per_second=1.0,
                        max_tokens=5,
                        name=service
                    )
                )
            self._buckets[service] = TokenBucket(config)
        return self._buckets[service]

    def configure(self, service: str, tokens_per_second: float, max_tokens: int) -> None:
        """
        Configure rate limit for a service.

        Args:
            service: Service name
            tokens_per_second: Token refill rate
            max_tokens: Maximum bucket capacity
        """
        config = RateLimitConfig(
            tokens_per_second=tokens_per_second,
            max_tokens=max_tokens,
            name=service
        )
        self._custom_configs[service] = config
        # Reset bucket if it exists
        if service in self._buckets:
            self._buckets[service] = TokenBucket(config)
        logger.info(f"Rate limit configured for {service}: {tokens_per_second}/s, burst={max_tokens}")

    async def acquire(
        self,
        service: str,
        tokens: int = 1,
        wait: bool = True,
        timeout: float = 30.0
    ) -> bool:
        """
        Acquire tokens for a service.

        Args:
            service: Service name
            tokens: Number of tokens to acquire
            wait: If True, wait for tokens
            timeout: Maximum wait time

        Returns:
            True if acquired, False otherwise
        """
        bucket = self.get_bucket(service)
        return await bucket.acquire(tokens, wait, timeout)

    def get_status(self, service: str) -> Dict[str, Any]:
        """
        Get rate limiter status for a service.

        Args:
            service: Service name

        Returns:
            Dictionary with status information
        """
        bucket = self.get_bucket(service)
        return {
            "service": service,
            "available_tokens": bucket.available_tokens,
            "max_tokens": bucket.config.max_tokens,
            "tokens_per_second": bucket.config.tokens_per_second,
            "time_until_available": bucket.time_until_available
        }

    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all active rate limiters."""
        return {
            service: self.get_status(service)
            for service in self._buckets
        }


# Global rate limiter instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def rate_limited(
    service: str,
    tokens: int = 1,
    wait: bool = True,
    timeout: float = 30.0
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator to apply rate limiting to a function.

    Args:
        service: Service name for rate limiting
        tokens: Tokens to consume per call
        wait: If True, wait for tokens. If False, raise immediately.
        timeout: Maximum wait time in seconds

    Returns:
        Decorated function

    Example:
        @rate_limited(service="tmdb", tokens=1)
        async def fetch_movie_details(movie_id: int):
            # This will be rate limited
            return await tmdb_api.get_movie(movie_id)
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                rate_limiter = get_rate_limiter()
                await rate_limiter.acquire(service, tokens, wait, timeout)
                return await func(*args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                # For sync functions, we need to run the async acquire in an event loop
                rate_limiter = get_rate_limiter()
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                loop.run_until_complete(rate_limiter.acquire(service, tokens, wait, timeout))
                return func(*args, **kwargs)
            return sync_wrapper

    return decorator


def configure_rate_limit(service: str, tokens_per_second: float, max_tokens: int) -> None:
    """
    Configure rate limit for a service.

    Convenience function to configure the global rate limiter.

    Args:
        service: Service name
        tokens_per_second: Token refill rate
        max_tokens: Maximum bucket capacity
    """
    get_rate_limiter().configure(service, tokens_per_second, max_tokens)


async def acquire_rate_limit(
    service: str,
    tokens: int = 1,
    wait: bool = True,
    timeout: float = 30.0
) -> bool:
    """
    Acquire rate limit tokens.

    Convenience function for manual rate limit acquisition.

    Args:
        service: Service name
        tokens: Tokens to acquire
        wait: Whether to wait
        timeout: Maximum wait time

    Returns:
        True if acquired
    """
    return await get_rate_limiter().acquire(service, tokens, wait, timeout)
