"""
ConnectionHealthService - Background health checks for external services

Checks Radarr, Sonarr, Prowlarr and FlareSolverr connections on startup
and every 5 minutes. Results are cached in memory and served via
GET /api/settings/connection-status.
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ─── In-memory cache ────────────────────────────────────────────────────────
# Dict[service_name, {'status': 'success'|'error'|'unchecked', 'message': str, 'checked_at': str}]
_status_cache: Dict[str, Dict[str, Any]] = {}


def get_cached_status() -> Dict[str, Dict[str, Any]]:
    """Return a copy of the current in-memory status cache."""
    return dict(_status_cache)


def _set(service: str, ok: bool, message: str) -> None:
    _status_cache[service] = {
        'status': 'success' if ok else 'error',
        'message': message,
        'checked_at': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
    }


# ─── Individual checks ───────────────────────────────────────────────────────

async def _check_radarr(url: str, api_key: str) -> None:
    try:
        from app.services.radarr_client import RadarrClient
        health = await RadarrClient(url, api_key, timeout=10).health_check()
        if health.get('healthy'):
            _set('radarr', True, f"Connecté à Radarr v{health.get('version', '?')}")
        else:
            _set('radarr', False, health.get('error', 'Connexion échouée'))
    except Exception as e:
        _set('radarr', False, str(e))


async def _check_sonarr(url: str, api_key: str) -> None:
    try:
        from app.services.sonarr_client import SonarrClient
        health = await SonarrClient(url, api_key, timeout=10).health_check()
        if health.get('healthy'):
            _set('sonarr', True, f"Connecté à Sonarr v{health.get('version', '?')}")
        else:
            _set('sonarr', False, health.get('error', 'Connexion échouée'))
    except Exception as e:
        _set('sonarr', False, str(e))


async def _check_prowlarr(url: str, api_key: str) -> None:
    try:
        from app.services.prowlarr_client import ProwlarrClient
        health = await ProwlarrClient(url, api_key).health_check()
        if health.get('healthy'):
            _set('prowlarr', True, f"Connecté à Prowlarr v{health.get('version', '?')}")
        else:
            _set('prowlarr', False, health.get('error', 'Connexion échouée'))
    except Exception as e:
        _set('prowlarr', False, str(e))


async def _check_flaresolverr(url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{url.rstrip('/')}/health")
        if r.status_code == 200:
            _set('flaresolverr', True, 'FlareSolverr opérationnel')
        else:
            _set('flaresolverr', False, f'HTTP {r.status_code}')
    except Exception as e:
        _set('flaresolverr', False, str(e))


async def _check_qbittorrent(host: str, username: Optional[str] = None, password: Optional[str] = None) -> None:
    try:
        qb_url = host if host.startswith(('http://', 'https://')) else f"http://{host}"
        base = qb_url.rstrip('/')
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{base}/api/v2/app/version")
            if r.status_code == 403 and username:
                # qBittorrent requires authentication
                login = await client.post(
                    f"{base}/api/v2/auth/login",
                    data={"username": username, "password": password or ""}
                )
                if login.text == "Ok.":
                    r = await client.get(f"{base}/api/v2/app/version")
                else:
                    _set('qbittorrent', False, 'Identifiants invalides')
                    return
        if r.status_code == 200:
            version = r.text.strip()
            _set('qbittorrent', True, f'qBittorrent v{version}' if version else 'qBittorrent opérationnel')
        else:
            _set('qbittorrent', False, f'HTTP {r.status_code}')
    except Exception as e:
        _set('qbittorrent', False, str(e))


async def _check_tmdb(api_key: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://api.themoviedb.org/3/configuration",
                params={"api_key": api_key}
            )
        if r.status_code == 200:
            _set('tmdb', True, 'TMDB API opérationnelle')
        elif r.status_code == 401:
            _set('tmdb', False, 'Clé API invalide')
        else:
            _set('tmdb', False, f'HTTP {r.status_code}')
    except Exception as e:
        _set('tmdb', False, str(e))


# ─── Main check function ──────────────────────────────────────────────────────

async def check_all_services(db) -> Dict[str, Dict[str, Any]]:
    """
    Check all configured services and update the in-memory cache.

    Only checks services that have both URL and credentials configured.
    Safe to call in background — exceptions are caught per service.

    Args:
        db: SQLAlchemy database session

    Returns:
        Updated status cache
    """
    from app.models.settings import Settings
    settings = Settings.get_settings(db)

    tasks_ran = 0

    if settings.radarr_url and settings.radarr_api_key:
        await _check_radarr(settings.radarr_url, settings.radarr_api_key)
        tasks_ran += 1

    if settings.sonarr_url and settings.sonarr_api_key:
        await _check_sonarr(settings.sonarr_url, settings.sonarr_api_key)
        tasks_ran += 1

    if settings.prowlarr_url and settings.prowlarr_api_key:
        await _check_prowlarr(settings.prowlarr_url, settings.prowlarr_api_key)
        tasks_ran += 1

    if settings.flaresolverr_url:
        await _check_flaresolverr(settings.flaresolverr_url)
        tasks_ran += 1

    if settings.qbittorrent_host:
        await _check_qbittorrent(
            settings.qbittorrent_host,
            settings.qbittorrent_username,
            settings.qbittorrent_password,
        )
        tasks_ran += 1

    if settings.tmdb_api_key:
        await _check_tmdb(settings.tmdb_api_key)
        tasks_ran += 1

    if tasks_ran:
        logger.info(f"✓ Connection health check done ({tasks_ran} service(s))")

    return get_cached_status()
