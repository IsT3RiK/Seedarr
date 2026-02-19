"""
Health Check API Routes

Provides Kubernetes-compatible health check endpoints for the application.

Endpoints:
- /health/live: Liveness probe - is the application running?
- /health/ready: Readiness probe - is the application ready to receive traffic?
- /health/detailed: Detailed health status of all dependencies
"""

import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.settings import Settings
from app.models.tracker import Tracker
from app.services.health_check_service import (
    get_health_service,
    HealthStatus
)
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness_probe():
    """
    Kubernetes liveness probe.

    Returns 200 if the application is running.
    This endpoint should be lightweight and not check external dependencies.

    Returns:
        {"status": "alive"}
    """
    return {"status": "alive"}


@router.get("/ready")
async def readiness_probe(db: Session = Depends(get_db)):
    """
    Kubernetes readiness probe.

    Returns 200 if the application is ready to receive traffic.
    Checks critical dependencies (database).

    Returns:
        200: Application is ready
        503: Application is not ready
    """
    from fastapi import Response

    health_service = get_health_service()
    db_health = await health_service.check_database()

    if db_health.status == HealthStatus.HEALTHY:
        return {"status": "ready", "database": "connected"}

    return Response(
        content='{"status": "not_ready", "reason": "' + db_health.message + '"}',
        status_code=503,
        media_type="application/json"
    )


@router.get("/detailed")
async def detailed_health(db: Session = Depends(get_db)):
    """
    Detailed health check of all services.

    Returns comprehensive health status for all configured dependencies:
    - Database
    - FlareSolverr
    - qBittorrent
    - TMDB API
    - Tracker
    - Prowlarr

    Returns:
        JSON object with overall status and individual service health
    """
    health_service = get_health_service()
    settings = Settings.get_settings(db)

    result = await health_service.check_all(settings)

    # Add version info
    result["version"] = "2.0.0"

    return result


@router.get("/services/{service_name}")
async def service_health(service_name: str, db: Session = Depends(get_db)):
    """
    Check health of a specific service.

    Args:
        service_name: One of: database, flaresolverr, qbittorrent, tmdb, tracker, prowlarr

    Returns:
        Health status of the specified service
    """
    from fastapi import HTTPException

    health_service = get_health_service()
    settings = Settings.get_settings(db)

    service_checks = {
        "database": health_service.check_database,
        "flaresolverr": lambda: health_service.check_flaresolverr(settings.flaresolverr_url),
        "qbittorrent": lambda: health_service.check_qbittorrent(
            settings.qbittorrent_host,
            settings.qbittorrent_username,
            settings.qbittorrent_password
        ),
        "tmdb": lambda: health_service.check_tmdb(settings.tmdb_api_key),
        "tracker": lambda: health_service.check_tracker(
            settings.tracker_url,
            settings.tracker_passkey
        ),
        "prowlarr": lambda: health_service.check_prowlarr(
            settings.prowlarr_url,
            settings.prowlarr_api_key
        )
    }

    if service_name not in service_checks:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown service: {service_name}. Available: {list(service_checks.keys())}"
        )

    check_func = service_checks[service_name]
    result = await check_func()

    return result.to_dict()


@router.post("/cache/clear")
async def clear_health_cache():
    """
    Clear health check cache.

    Forces fresh health checks on next request.

    Returns:
        Success confirmation
    """
    health_service = get_health_service()
    health_service.clear_cache()

    logger.info("Health check cache cleared")
    return {"status": "success", "message": "Health check cache cleared"}


@router.get("/config-status")
async def get_config_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Check configuration status and return missing or incomplete settings.

    This endpoint helps users identify what needs to be configured
    for the application to function properly.

    Returns:
        JSON with status and list of configuration issues
    """
    settings = Settings.get_settings(db)
    trackers = Tracker.get_all(db)
    enabled_trackers = Tracker.get_enabled(db)

    issues: List[Dict[str, Any]] = []

    # Check TMDB API key
    if not settings.tmdb_api_key:
        issues.append({
            "type": "warning",
            "component": "TMDB",
            "message": "Clé API TMDB non configurée - les métadonnées ne seront pas disponibles",
            "fix_url": "/settings#tmdb"
        })

    # Check qBittorrent
    if not settings.qbittorrent_host:
        issues.append({
            "type": "warning",
            "component": "qBittorrent",
            "message": "qBittorrent non configuré - le seeding automatique ne fonctionnera pas",
            "fix_url": "/settings#qbittorrent"
        })

    # Check trackers
    if not trackers:
        issues.append({
            "type": "error",
            "component": "Trackers",
            "message": "Aucun tracker configuré - vous ne pouvez pas publier de torrents",
            "fix_url": "/trackers"
        })
    elif not enabled_trackers:
        issues.append({
            "type": "warning",
            "component": "Trackers",
            "message": "Aucun tracker activé - activez au moins un tracker pour publier",
            "fix_url": "/trackers"
        })

    # Check all trackers for API key and categories
    for tracker in trackers:
        if tracker.enabled:
            # Check API key for trackers that need it
            if not tracker.api_key and not tracker.passkey:
                issues.append({
                    "type": "warning",
                    "component": f"Tracker {tracker.name}",
                    "message": "Clé API/passkey manquante - les uploads ne fonctionneront pas",
                    "fix_url": f"/trackers?edit={tracker.id}"
                })
            # Check categories
            elif not tracker.category_mapping:
                issues.append({
                    "type": "warning",
                    "component": f"Tracker {tracker.name}",
                    "message": "Catégories non synchronisées - testez la connexion pour synchroniser",
                    "fix_action": f"test_tracker_{tracker.id}",
                    "fix_url": f"/trackers?test={tracker.id}"
                })

    # Check Prowlarr (optional but useful)
    if not settings.prowlarr_url:
        issues.append({
            "type": "info",
            "component": "Prowlarr",
            "message": "Prowlarr non configuré - l'import automatique des trackers n'est pas disponible",
            "fix_url": "/settings#prowlarr"
        })

    # Determine overall status
    has_errors = any(i["type"] == "error" for i in issues)
    has_warnings = any(i["type"] == "warning" for i in issues)

    if has_errors:
        status = "error"
    elif has_warnings:
        status = "warning"
    elif issues:
        status = "info"
    else:
        status = "ok"

    return {
        "status": status,
        "issues": issues,
        "issues_count": len(issues),
        "summary": {
            "trackers_configured": len(trackers),
            "trackers_enabled": len(enabled_trackers),
            "tmdb_configured": bool(settings.tmdb_api_key),
            "qbittorrent_configured": bool(settings.qbittorrent_host),
            "prowlarr_configured": bool(settings.prowlarr_url)
        }
    }
