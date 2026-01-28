"""
Health Check Service

Centralized health check service for all external dependencies.
Provides Kubernetes-compatible liveness/readiness probes and detailed health status.

Features:
- Individual service health checks with timeouts
- Cached health status to prevent excessive polling
- Aggregate health status for overall system health
- Kubernetes-compatible endpoints
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Any, List

import httpx

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """Health status levels."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ServiceHealth:
    """Health status for a single service."""
    name: str
    status: HealthStatus
    message: str = ""
    latency_ms: Optional[float] = None
    version: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    checked_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
        }
        if self.latency_ms is not None:
            result["latency_ms"] = round(self.latency_ms, 2)
        if self.version:
            result["version"] = self.version
        if self.details:
            result["details"] = self.details
        if self.checked_at:
            result["checked_at"] = self.checked_at.isoformat()
        return result


class HealthCheckService:
    """
    Centralized health check service with caching.

    Provides health checks for:
    - Database connectivity
    - FlareSolverr service
    - qBittorrent WebUI
    - TMDB API
    - Tracker API
    - Prowlarr (if configured)
    """

    def __init__(self, cache_ttl_seconds: int = 30):
        """
        Initialize health check service.

        Args:
            cache_ttl_seconds: How long to cache health check results
        """
        self.cache_ttl = cache_ttl_seconds
        self._cache: Dict[str, ServiceHealth] = {}
        self._cache_times: Dict[str, float] = {}
        self._http_timeout = 10.0

    def _is_cache_valid(self, service: str) -> bool:
        """Check if cached result is still valid."""
        if service not in self._cache_times:
            return False
        return (time.time() - self._cache_times[service]) < self.cache_ttl

    def _update_cache(self, service: str, health: ServiceHealth) -> None:
        """Update cache with new health result."""
        self._cache[service] = health
        self._cache_times[service] = time.time()

    async def check_database(self) -> ServiceHealth:
        """Check database connectivity."""
        service_name = "database"

        if self._is_cache_valid(service_name):
            return self._cache[service_name]

        start = time.time()
        try:
            from app.database import engine
            from sqlalchemy import text

            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))

            latency = (time.time() - start) * 1000
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.HEALTHY,
                message="Database connected",
                latency_ms=latency,
                checked_at=datetime.utcnow()
            )
        except Exception as e:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message=f"Database error: {str(e)}",
                checked_at=datetime.utcnow()
            )

        self._update_cache(service_name, health)
        return health

    async def check_flaresolverr(self, url: Optional[str] = None) -> ServiceHealth:
        """Check FlareSolverr service health."""
        service_name = "flaresolverr"

        if not url:
            return ServiceHealth(
                name=service_name,
                status=HealthStatus.UNKNOWN,
                message="Not configured",
                checked_at=datetime.utcnow()
            )

        cache_key = f"{service_name}:{url}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key]

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                response = await client.get(f"{url.rstrip('/')}/health")

            latency = (time.time() - start) * 1000

            if response.status_code == 200:
                health = ServiceHealth(
                    name=service_name,
                    status=HealthStatus.HEALTHY,
                    message="FlareSolverr is running",
                    latency_ms=latency,
                    checked_at=datetime.utcnow()
                )
            else:
                health = ServiceHealth(
                    name=service_name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"HTTP {response.status_code}",
                    latency_ms=latency,
                    checked_at=datetime.utcnow()
                )
        except httpx.TimeoutException:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message="Connection timeout",
                checked_at=datetime.utcnow()
            )
        except httpx.ConnectError:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message="Cannot connect",
                checked_at=datetime.utcnow()
            )
        except Exception as e:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message=str(e),
                checked_at=datetime.utcnow()
            )

        self._update_cache(cache_key, health)
        return health

    async def check_qbittorrent(
        self,
        host: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None
    ) -> ServiceHealth:
        """Check qBittorrent WebUI health."""
        service_name = "qbittorrent"

        if not host:
            return ServiceHealth(
                name=service_name,
                status=HealthStatus.UNKNOWN,
                message="Not configured",
                checked_at=datetime.utcnow()
            )

        cache_key = f"{service_name}:{host}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key]

        # Build URL
        url = host if host.startswith(("http://", "https://")) else f"http://{host}"

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                response = await client.get(f"{url}/api/v2/app/version")
                latency = (time.time() - start) * 1000

                if response.status_code == 200:
                    version = response.text.strip()
                    health = ServiceHealth(
                        name=service_name,
                        status=HealthStatus.HEALTHY,
                        message="qBittorrent connected",
                        latency_ms=latency,
                        version=version,
                        checked_at=datetime.utcnow()
                    )
                elif response.status_code == 403:
                    # Requires auth - try to login
                    if username:
                        login_response = await client.post(
                            f"{url}/api/v2/auth/login",
                            data={"username": username, "password": password or ""}
                        )
                        if login_response.text == "Ok.":
                            # Get version after auth
                            version_response = await client.get(f"{url}/api/v2/app/version")
                            if version_response.status_code == 200:
                                health = ServiceHealth(
                                    name=service_name,
                                    status=HealthStatus.HEALTHY,
                                    message="qBittorrent connected (auth required)",
                                    latency_ms=latency,
                                    version=version_response.text.strip(),
                                    checked_at=datetime.utcnow()
                                )
                            else:
                                health = ServiceHealth(
                                    name=service_name,
                                    status=HealthStatus.DEGRADED,
                                    message="Authenticated but version check failed",
                                    latency_ms=latency,
                                    checked_at=datetime.utcnow()
                                )
                        else:
                            health = ServiceHealth(
                                name=service_name,
                                status=HealthStatus.UNHEALTHY,
                                message="Authentication failed",
                                latency_ms=latency,
                                checked_at=datetime.utcnow()
                            )
                    else:
                        health = ServiceHealth(
                            name=service_name,
                            status=HealthStatus.UNHEALTHY,
                            message="Authentication required",
                            latency_ms=latency,
                            checked_at=datetime.utcnow()
                        )
                else:
                    health = ServiceHealth(
                        name=service_name,
                        status=HealthStatus.UNHEALTHY,
                        message=f"HTTP {response.status_code}",
                        latency_ms=latency,
                        checked_at=datetime.utcnow()
                    )
        except httpx.TimeoutException:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message="Connection timeout",
                checked_at=datetime.utcnow()
            )
        except httpx.ConnectError:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message="Cannot connect",
                checked_at=datetime.utcnow()
            )
        except Exception as e:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message=str(e),
                checked_at=datetime.utcnow()
            )

        self._update_cache(cache_key, health)
        return health

    async def check_tmdb(self, api_key: Optional[str] = None) -> ServiceHealth:
        """Check TMDB API health."""
        service_name = "tmdb"

        if not api_key:
            return ServiceHealth(
                name=service_name,
                status=HealthStatus.UNKNOWN,
                message="Not configured",
                checked_at=datetime.utcnow()
            )

        if self._is_cache_valid(service_name):
            return self._cache[service_name]

        start = time.time()
        try:
            from app.utils.tmdb_auth import format_tmdb_request

            params, headers = format_tmdb_request(api_key)

            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                response = await client.get(
                    "https://api.themoviedb.org/3/configuration",
                    params=params,
                    headers=headers
                )

            latency = (time.time() - start) * 1000

            if response.status_code == 200:
                health = ServiceHealth(
                    name=service_name,
                    status=HealthStatus.HEALTHY,
                    message="TMDB API connected",
                    latency_ms=latency,
                    checked_at=datetime.utcnow()
                )
            elif response.status_code == 401:
                health = ServiceHealth(
                    name=service_name,
                    status=HealthStatus.UNHEALTHY,
                    message="Invalid API key",
                    latency_ms=latency,
                    checked_at=datetime.utcnow()
                )
            else:
                health = ServiceHealth(
                    name=service_name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"HTTP {response.status_code}",
                    latency_ms=latency,
                    checked_at=datetime.utcnow()
                )
        except httpx.TimeoutException:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message="Connection timeout",
                checked_at=datetime.utcnow()
            )
        except Exception as e:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message=str(e),
                checked_at=datetime.utcnow()
            )

        self._update_cache(service_name, health)
        return health

    async def check_tracker(
        self,
        tracker_url: Optional[str] = None,
        passkey: Optional[str] = None
    ) -> ServiceHealth:
        """Check tracker API health."""
        service_name = "tracker"

        if not tracker_url or not passkey:
            return ServiceHealth(
                name=service_name,
                status=HealthStatus.UNKNOWN,
                message="Not configured",
                checked_at=datetime.utcnow()
            )

        cache_key = f"{service_name}:{tracker_url}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key]

        start = time.time()
        try:
            meta_url = f"{tracker_url.rstrip('/')}/api/external/meta"

            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(meta_url, params={"passkey": passkey})

            latency = (time.time() - start) * 1000

            if response.status_code == 200:
                try:
                    data = response.json()
                    categories_count = len(data.get("categories", []))
                    tags_count = sum(
                        len(g.get("tags", [])) for g in data.get("tagGroups", [])
                    )
                    health = ServiceHealth(
                        name=service_name,
                        status=HealthStatus.HEALTHY,
                        message=f"Tracker connected ({categories_count} categories, {tags_count} tags)",
                        latency_ms=latency,
                        details={
                            "categories": categories_count,
                            "tags": tags_count
                        },
                        checked_at=datetime.utcnow()
                    )
                except Exception:
                    health = ServiceHealth(
                        name=service_name,
                        status=HealthStatus.HEALTHY,
                        message="Tracker connected",
                        latency_ms=latency,
                        checked_at=datetime.utcnow()
                    )
            elif response.status_code in (401, 403):
                health = ServiceHealth(
                    name=service_name,
                    status=HealthStatus.UNHEALTHY,
                    message="Invalid passkey",
                    latency_ms=latency,
                    checked_at=datetime.utcnow()
                )
            else:
                health = ServiceHealth(
                    name=service_name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"HTTP {response.status_code}",
                    latency_ms=latency,
                    checked_at=datetime.utcnow()
                )
        except httpx.TimeoutException:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message="Connection timeout",
                checked_at=datetime.utcnow()
            )
        except httpx.ConnectError:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message="Cannot connect",
                checked_at=datetime.utcnow()
            )
        except Exception as e:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message=str(e),
                checked_at=datetime.utcnow()
            )

        self._update_cache(cache_key, health)
        return health

    async def check_prowlarr(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None
    ) -> ServiceHealth:
        """Check Prowlarr health."""
        service_name = "prowlarr"

        if not url or not api_key:
            return ServiceHealth(
                name=service_name,
                status=HealthStatus.UNKNOWN,
                message="Not configured",
                checked_at=datetime.utcnow()
            )

        cache_key = f"{service_name}:{url}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key]

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                response = await client.get(
                    f"{url.rstrip('/')}/api/v1/health",
                    headers={"X-Api-Key": api_key}
                )

            latency = (time.time() - start) * 1000

            if response.status_code == 200:
                health = ServiceHealth(
                    name=service_name,
                    status=HealthStatus.HEALTHY,
                    message="Prowlarr connected",
                    latency_ms=latency,
                    checked_at=datetime.utcnow()
                )
            elif response.status_code == 401:
                health = ServiceHealth(
                    name=service_name,
                    status=HealthStatus.UNHEALTHY,
                    message="Invalid API key",
                    latency_ms=latency,
                    checked_at=datetime.utcnow()
                )
            else:
                health = ServiceHealth(
                    name=service_name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"HTTP {response.status_code}",
                    latency_ms=latency,
                    checked_at=datetime.utcnow()
                )
        except httpx.TimeoutException:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message="Connection timeout",
                checked_at=datetime.utcnow()
            )
        except httpx.ConnectError:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message="Cannot connect",
                checked_at=datetime.utcnow()
            )
        except Exception as e:
            health = ServiceHealth(
                name=service_name,
                status=HealthStatus.UNHEALTHY,
                message=str(e),
                checked_at=datetime.utcnow()
            )

        self._update_cache(cache_key, health)
        return health

    async def check_all(self, settings) -> Dict[str, Any]:
        """
        Check health of all services.

        Args:
            settings: Settings object with service configurations

        Returns:
            Aggregate health status with individual service statuses
        """
        # Run all health checks concurrently
        results = await asyncio.gather(
            self.check_database(),
            self.check_flaresolverr(settings.flaresolverr_url),
            self.check_qbittorrent(
                settings.qbittorrent_host,
                settings.qbittorrent_username,
                settings.qbittorrent_password
            ),
            self.check_tmdb(settings.tmdb_api_key),
            self.check_tracker(settings.tracker_url, settings.tracker_passkey),
            self.check_prowlarr(settings.prowlarr_url, settings.prowlarr_api_key),
            return_exceptions=True
        )

        services = {}
        unhealthy_count = 0
        degraded_count = 0

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Health check exception: {result}")
                continue
            if isinstance(result, ServiceHealth):
                services[result.name] = result.to_dict()
                if result.status == HealthStatus.UNHEALTHY:
                    unhealthy_count += 1
                elif result.status == HealthStatus.DEGRADED:
                    degraded_count += 1

        # Determine overall status
        # Critical services: database
        db_health = services.get("database", {})
        if db_health.get("status") == HealthStatus.UNHEALTHY.value:
            overall_status = HealthStatus.UNHEALTHY
        elif unhealthy_count > 0:
            overall_status = HealthStatus.DEGRADED
        elif degraded_count > 0:
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.HEALTHY

        return {
            "status": overall_status.value,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "services": services,
            "summary": {
                "healthy": sum(1 for s in services.values() if s.get("status") == "healthy"),
                "degraded": degraded_count,
                "unhealthy": unhealthy_count,
                "unknown": sum(1 for s in services.values() if s.get("status") == "unknown")
            }
        }

    def clear_cache(self) -> None:
        """Clear all cached health check results."""
        self._cache.clear()
        self._cache_times.clear()


# Global health check service instance
_health_service: Optional[HealthCheckService] = None


def get_health_service() -> HealthCheckService:
    """Get the global health check service instance."""
    global _health_service
    if _health_service is None:
        _health_service = HealthCheckService()
    return _health_service
