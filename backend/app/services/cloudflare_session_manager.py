"""
CloudflareSessionManager for Seedarr v2.0

This module handles FlareSolverr integration for bypassing Cloudflare protection.
It manages cookie lifecycle, session management, and implements circuit breaker
pattern for robust failure handling.

Features:
    - FlareSolverr integration for Cloudflare bypass
    - Cookie extraction and session management
    - Circuit breaker pattern (opens after 3 consecutive failures)
    - Automatic retry with exponential backoff
    - Health check monitoring
    - Comprehensive error handling with typed exceptions

Circuit Breaker States:
    - CLOSED: Normal operation, requests go through
    - OPEN: After 3 failures, fast-fail without calling FlareSolverr
    - HALF_OPEN: After timeout, allow one request to test service recovery

Usage:
    manager = CloudflareSessionManager(
        flaresolverr_url="http://localhost:8191",
        max_timeout=60000
    )

    # Get authenticated session with cookies
    session = await manager.get_session(tracker_url="https://tracker.example.com")

    # Use session for tracker API calls
    response = session.get("https://tracker.example.com/api/upload")

    # Check service health
    is_healthy = await manager.health_check()
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any
import requests
from requests import Session

from .exceptions import (
    CloudflareBypassError,
    NetworkRetryableError,
    retry_on_network_error
)
from app.config import config

logger = logging.getLogger(__name__)


class CircuitBreakerState(Enum):
    """Circuit breaker states for FlareSolverr failure handling."""
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Service unavailable, fail fast
    HALF_OPEN = "half_open"  # Testing service recovery


class CloudflareSessionManager:
    """
    Manages Cloudflare bypass using FlareSolverr service with circuit breaker pattern.

    This class handles all FlareSolverr communication, cookie extraction, and session
    management for bypassing Cloudflare protection. It implements a circuit breaker
    pattern to prevent cascading failures when FlareSolverr is unavailable.

    Circuit Breaker Behavior:
        - CLOSED: All requests go through normally
        - After 3 consecutive failures: Opens circuit
        - OPEN: Fail fast without calling FlareSolverr (60s timeout)
        - After timeout: Transitions to HALF_OPEN
        - HALF_OPEN: One test request allowed
        - Success: Circuit closes, normal operation resumes
        - Failure: Circuit reopens for another timeout period

    Attributes:
        flaresolverr_url: FlareSolverr service URL (e.g., http://localhost:8191)
        max_timeout: Maximum timeout for FlareSolverr requests in milliseconds
        circuit_state: Current circuit breaker state
        failure_count: Consecutive failure counter
        last_failure_time: Timestamp of last failure
        circuit_open_duration: Duration to keep circuit open (seconds)
    """

    def __init__(
        self,
        flaresolverr_url: str,
        max_timeout: int = None
    ):
        """
        Initialize CloudflareSessionManager.

        Args:
            flaresolverr_url: FlareSolverr service URL (e.g., http://localhost:8191)
            max_timeout: Maximum timeout for FlareSolverr requests in milliseconds (default: from config)
        """
        self.flaresolverr_url = flaresolverr_url.rstrip('/')
        self.max_timeout = max_timeout or config.FLARESOLVERR_TIMEOUT

        # Circuit breaker state (using centralized configuration)
        self.circuit_state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.max_failures = config.CIRCUIT_BREAKER_MAX_FAILURES
        self.circuit_open_duration = config.CIRCUIT_BREAKER_OPEN_DURATION

        logger.info(
            f"CloudflareSessionManager initialized with FlareSolverr URL: {self.flaresolverr_url}"
        )

    def _check_circuit_breaker(self) -> None:
        """
        Check circuit breaker state and transition if needed.

        Raises:
            CloudflareBypassError: If circuit is open (service unavailable)
        """
        if self.circuit_state == CircuitBreakerState.OPEN:
            # Check if timeout has elapsed
            if self.last_failure_time:
                time_since_failure = (datetime.utcnow() - self.last_failure_time).total_seconds()

                if time_since_failure >= self.circuit_open_duration:
                    # Transition to HALF_OPEN to test recovery
                    logger.info(
                        f"Circuit breaker transitioning from OPEN to HALF_OPEN "
                        f"after {time_since_failure:.1f}s"
                    )
                    self.circuit_state = CircuitBreakerState.HALF_OPEN
                else:
                    # Circuit still open, fail fast
                    remaining_time = self.circuit_open_duration - time_since_failure
                    error_msg = (
                        f"Circuit breaker OPEN: FlareSolverr unavailable. "
                        f"Retry in {remaining_time:.1f}s"
                    )
                    logger.error(error_msg)
                    raise CloudflareBypassError(error_msg)

    def _record_success(self) -> None:
        """Record successful FlareSolverr request and close circuit if needed."""
        if self.circuit_state != CircuitBreakerState.CLOSED:
            logger.info(
                f"Circuit breaker closing after successful request "
                f"(was {self.circuit_state.value})"
            )
            self.circuit_state = CircuitBreakerState.CLOSED

        # Reset failure counter
        self.failure_count = 0
        self.last_failure_time = None

    def _record_failure(self) -> None:
        """Record FlareSolverr failure and open circuit if threshold reached."""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()

        logger.warning(
            f"FlareSolverr failure recorded: {self.failure_count}/{self.max_failures}"
        )

        # Open circuit if failure threshold reached
        if self.failure_count >= self.max_failures:
            self.circuit_state = CircuitBreakerState.OPEN
            logger.error(
                f"Circuit breaker OPENED after {self.failure_count} consecutive failures. "
                f"Will retry in {self.circuit_open_duration}s"
            )

    @retry_on_network_error(max_retries=3)
    async def get_session(self, tracker_url: str) -> Session:
        """
        Get authenticated requests.Session with Cloudflare bypass cookies.

        This method delegates the Cloudflare challenge solving to FlareSolverr,
        extracts the cookies, and returns a configured requests.Session object
        ready for authenticated tracker API calls.

        Args:
            tracker_url: Target tracker URL to bypass Cloudflare for

        Returns:
            requests.Session configured with authentication cookies

        Raises:
            CloudflareBypassError: If FlareSolverr fails to solve challenge
            NetworkRetryableError: If network/timeout errors occur (will auto-retry)

        Example:
            session = await manager.get_session("https://lacale.example.com")
            response = session.get("https://lacale.example.com/api/upload")
        """
        # Check circuit breaker state
        self._check_circuit_breaker()

        logger.info(f"Requesting Cloudflare bypass for URL: {tracker_url}")

        try:
            # Call FlareSolverr to solve Cloudflare challenge
            # Note: Using sync requests in async context - consider aiohttp for production
            response = await asyncio.to_thread(
                requests.post,
                f"{self.flaresolverr_url}/v1",
                json={
                    "cmd": "request.get",
                    "url": tracker_url,
                    "maxTimeout": self.max_timeout
                },
                timeout=self.max_timeout / 1000  # Convert to seconds
            )

            # Check HTTP status
            if response.status_code != 200:
                error_msg = f"FlareSolverr returned HTTP {response.status_code}"
                logger.error(f"{error_msg}: {response.text}")
                self._record_failure()
                raise CloudflareBypassError(
                    error_msg,
                    flaresolverr_response=response.json() if response.text else None
                )

            # Parse FlareSolverr response
            flaresolverr_data = response.json()

            # Check for solution
            if 'solution' not in flaresolverr_data:
                error_msg = "FlareSolverr response missing 'solution' field"
                logger.error(f"{error_msg}: {flaresolverr_data}")
                self._record_failure()
                raise CloudflareBypassError(error_msg, flaresolverr_response=flaresolverr_data)

            solution = flaresolverr_data['solution']

            # Extract cookies from solution
            if 'cookies' not in solution:
                error_msg = "FlareSolverr solution missing 'cookies' field"
                logger.error(f"{error_msg}: {solution}")
                self._record_failure()
                raise CloudflareBypassError(error_msg, flaresolverr_response=flaresolverr_data)

            cookies = solution['cookies']

            # Create session and apply cookies
            session = Session()
            for cookie in cookies:
                if 'name' in cookie and 'value' in cookie:
                    session.cookies.set(cookie['name'], cookie['value'])
                    logger.debug(f"Applied cookie: {cookie['name']}")
                else:
                    logger.warning(f"Skipping malformed cookie: {cookie}")

            # Record success
            self._record_success()

            logger.info(
                f"Successfully bypassed Cloudflare for {tracker_url} "
                f"({len(cookies)} cookies extracted)"
            )

            return session

        except requests.exceptions.Timeout as e:
            error_msg = f"FlareSolverr request timeout after {self.max_timeout}ms"
            logger.error(f"{error_msg}: {e}")
            self._record_failure()
            raise NetworkRetryableError(error_msg, original_exception=e)

        except requests.exceptions.ConnectionError as e:
            error_msg = f"Failed to connect to FlareSolverr at {self.flaresolverr_url}"
            logger.error(f"{error_msg}: {e}")
            self._record_failure()
            raise NetworkRetryableError(error_msg, original_exception=e)

        except requests.exceptions.RequestException as e:
            error_msg = f"FlareSolverr request failed: {type(e).__name__}"
            logger.error(f"{error_msg}: {e}")
            self._record_failure()
            raise NetworkRetryableError(error_msg, original_exception=e)

        except Exception as e:
            error_msg = f"Unexpected error during Cloudflare bypass: {type(e).__name__}"
            logger.error(f"{error_msg}: {e}", exc_info=True)
            self._record_failure()
            raise CloudflareBypassError(error_msg)

    async def health_check(self) -> bool:
        """
        Perform health check on FlareSolverr service.

        Tests FlareSolverr availability without full challenge solving.
        Useful for monitoring and circuit breaker recovery testing.

        Returns:
            True if FlareSolverr is healthy and responding, False otherwise

        Example:
            if await manager.health_check():
                print("FlareSolverr is healthy")
            else:
                print("FlareSolverr is unavailable")
        """
        try:
            logger.debug(f"Performing health check on {self.flaresolverr_url}")

            # Simple health check - just verify service responds
            response = await asyncio.to_thread(
                requests.get,
                self.flaresolverr_url,
                timeout=config.HEALTH_CHECK_TIMEOUT
            )

            is_healthy = response.status_code in (200, 404)  # 404 is ok, service is running

            if is_healthy:
                logger.info(f"FlareSolverr health check: OK (HTTP {response.status_code})")
            else:
                logger.warning(
                    f"FlareSolverr health check: FAIL (HTTP {response.status_code})"
                )

            return is_healthy

        except Exception as e:
            logger.error(f"FlareSolverr health check failed: {type(e).__name__}: {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        """
        Get current circuit breaker status and statistics.

        Returns:
            Dictionary with circuit breaker state, failure count, and timing info

        Example:
            status = manager.get_status()
            print(f"Circuit state: {status['state']}")
            print(f"Failures: {status['failure_count']}")
        """
        status = {
            'state': self.circuit_state.value,
            'failure_count': self.failure_count,
            'max_failures': self.max_failures,
            'last_failure_time': self.last_failure_time.isoformat() if self.last_failure_time else None,
            'flaresolverr_url': self.flaresolverr_url,
            'max_timeout_ms': self.max_timeout
        }

        # Add time until circuit closes if open
        if self.circuit_state == CircuitBreakerState.OPEN and self.last_failure_time:
            time_since_failure = (datetime.utcnow() - self.last_failure_time).total_seconds()
            remaining_time = max(0, self.circuit_open_duration - time_since_failure)
            status['circuit_reopens_in_seconds'] = remaining_time

        return status

    def reset_circuit_breaker(self) -> None:
        """
        Manually reset circuit breaker to CLOSED state.

        Use this for administrative recovery after FlareSolverr service is restored.
        Should only be called after verifying service is healthy.

        Example:
            # After restarting FlareSolverr
            if await manager.health_check():
                manager.reset_circuit_breaker()
        """
        logger.info(
            f"Circuit breaker manually reset from {self.circuit_state.value} to CLOSED"
        )
        self.circuit_state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None

    def __repr__(self) -> str:
        """String representation of CloudflareSessionManager."""
        return (
            f"<CloudflareSessionManager(url='{self.flaresolverr_url}', "
            f"circuit_state={self.circuit_state.value}, "
            f"failures={self.failure_count}/{self.max_failures})>"
        )
