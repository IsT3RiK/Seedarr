"""
Prowlarr Integration API Routes

This module provides FastAPI routes for Prowlarr integration:
- Test Prowlarr connection
- List available indexers
- Import indexers as trackers
- Search for duplicates

Endpoints:
    GET  /api/prowlarr/status       - Check Prowlarr connection
    GET  /api/prowlarr/indexers     - List all indexers from Prowlarr
    POST /api/prowlarr/import       - Import indexers as trackers
    POST /api/prowlarr/search       - Search across indexers
"""

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from typing import Optional, List
import logging

from app.database import get_db
from app.models.settings import Settings
from app.models.tracker import Tracker
from app.services.prowlarr_client import (
    ProwlarrClient,
    ProwlarrError,
    get_prowlarr_client,
    reset_prowlarr_client
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prowlarr", tags=["prowlarr"])


def _get_prowlarr_client(db: Session) -> Optional[ProwlarrClient]:
    """Get Prowlarr client from settings."""
    settings = Settings.get_settings(db)

    if not settings or not settings.prowlarr_url or not settings.prowlarr_api_key:
        return None

    return ProwlarrClient(
        base_url=settings.prowlarr_url,
        api_key=settings.prowlarr_api_key
    )


@router.get("/status")
async def get_prowlarr_status(db: Session = Depends(get_db)):
    """
    Check Prowlarr connection status.

    Returns:
        Connection status and Prowlarr version
    """
    try:
        client = _get_prowlarr_client(db)

        if not client:
            return {
                "status": "not_configured",
                "message": "Prowlarr URL or API key not configured in settings"
            }

        health = await client.health_check()

        if health.get('healthy'):
            return {
                "status": "connected",
                "version": health.get('version'),
                "url": health.get('url')
            }
        else:
            return {
                "status": "error",
                "message": health.get('error', 'Connection failed'),
                "url": health.get('url')
            }

    except ProwlarrError as e:
        logger.error(f"Prowlarr connection error: {e}")
        return {
            "status": "error",
            "message": str(e)
        }
    except Exception as e:
        logger.error(f"Unexpected error checking Prowlarr: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.get("/indexers")
async def list_indexers(
    enabled_only: bool = Query(False, description="Only return enabled indexers"),
    db: Session = Depends(get_db)
):
    """
    List all indexers from Prowlarr.

    Args:
        enabled_only: If true, only return enabled indexers

    Returns:
        List of indexers with their configuration
    """
    try:
        client = _get_prowlarr_client(db)

        if not client:
            return {
                "status": "error",
                "message": "Prowlarr not configured"
            }

        if enabled_only:
            indexers = await client.get_enabled_indexers()
        else:
            indexers = await client.get_indexers()

        # Transform to simplified format
        result = []
        for idx in indexers:
            urls = idx.get('indexerUrls', [])
            result.append({
                'prowlarr_id': idx.get('id'),
                'name': idx.get('name'),
                'definition': idx.get('definitionName'),
                'protocol': idx.get('protocol'),
                'privacy': idx.get('privacy'),
                'enabled': idx.get('enable', False),
                'language': idx.get('language'),
                'url': urls[0] if urls else None,
                'description': idx.get('description', ''),
                # Check if already imported
                'imported': Tracker.get_by_slug(
                    db,
                    idx.get('definitionName', '').lower() or idx.get('name', '').lower().replace(' ', '-')
                ) is not None
            })

        return {
            "status": "success",
            "count": len(result),
            "indexers": result
        }

    except ProwlarrError as e:
        logger.error(f"Error fetching indexers: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/import")
async def import_indexers(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Import selected indexers as trackers.

    Request body:
        {
            "indexer_ids": [1, 2, 3]  // Prowlarr indexer IDs to import
        }

    Returns:
        Import results
    """
    try:
        client = _get_prowlarr_client(db)

        if not client:
            return {
                "status": "error",
                "message": "Prowlarr not configured"
            }

        body = await request.json()
        indexer_ids = body.get('indexer_ids', [])

        if not indexer_ids:
            return {
                "status": "error",
                "message": "No indexer IDs provided"
            }

        # Get all indexers
        all_indexers = await client.get_indexers()
        indexers_by_id = {idx['id']: idx for idx in all_indexers}

        imported = []
        skipped = []
        errors = []

        for idx_id in indexer_ids:
            if idx_id not in indexers_by_id:
                errors.append({'id': idx_id, 'error': 'Indexer not found in Prowlarr'})
                continue

            indexer = indexers_by_id[idx_id]
            tracker_data = client.indexer_to_tracker_dict(indexer)

            # Check if already exists
            existing = Tracker.get_by_slug(db, tracker_data['slug'])
            if existing:
                skipped.append({
                    'id': idx_id,
                    'name': indexer.get('name'),
                    'reason': 'Already imported'
                })
                continue

            try:
                # Create new tracker
                tracker = Tracker(
                    name=tracker_data['name'],
                    slug=tracker_data['slug'],
                    tracker_url=tracker_data['tracker_url'],
                    adapter_type=tracker_data['adapter_type'],
                    requires_cloudflare=tracker_data['requires_cloudflare'],
                    enabled=tracker_data['enabled'],
                    upload_enabled=tracker_data['upload_enabled'],
                    priority=tracker_data['priority']
                )

                # Store Prowlarr metadata in extra field if available
                # (We'd need to add an extra_config column to Tracker model)

                db.add(tracker)
                db.commit()

                imported.append({
                    'id': idx_id,
                    'name': indexer.get('name'),
                    'slug': tracker_data['slug'],
                    'tracker_id': tracker.id
                })

                logger.info(f"Imported tracker from Prowlarr: {tracker.name}")

            except Exception as e:
                db.rollback()
                errors.append({
                    'id': idx_id,
                    'name': indexer.get('name'),
                    'error': str(e)
                })

        return {
            "status": "success",
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "summary": {
                "imported_count": len(imported),
                "skipped_count": len(skipped),
                "error_count": len(errors)
            }
        }

    except ProwlarrError as e:
        logger.error(f"Error importing indexers: {e}")
        return {
            "status": "error",
            "message": str(e)
        }
    except Exception as e:
        logger.error(f"Unexpected error importing indexers: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/search")
async def search_indexers(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Search across Prowlarr indexers.

    Request body:
        {
            "query": "Movie.2024.1080p",
            "indexer_ids": [1, 2],  // Optional, searches all if not provided
            "tmdb_id": "12345",     // Optional, for TMDB search
            "imdb_id": "tt1234567"  // Optional, for IMDB search
        }

    Returns:
        Search results
    """
    try:
        client = _get_prowlarr_client(db)

        if not client:
            return {
                "status": "error",
                "message": "Prowlarr not configured"
            }

        body = await request.json()
        query = body.get('query')
        indexer_ids = body.get('indexer_ids')
        tmdb_id = body.get('tmdb_id')
        imdb_id = body.get('imdb_id')

        results = []

        if tmdb_id:
            results = await client.search_by_tmdb(tmdb_id, indexer_ids=indexer_ids)
        elif imdb_id:
            results = await client.search_by_imdb(imdb_id, indexer_ids=indexer_ids)
        elif query:
            results = await client.search(query, indexer_ids=indexer_ids)
        else:
            return {
                "status": "error",
                "message": "Provide query, tmdb_id, or imdb_id"
            }

        # Format results
        formatted = []
        for r in results[:50]:  # Limit to 50 results
            formatted.append({
                'title': r.get('title'),
                'indexer': r.get('indexer'),
                'indexer_id': r.get('indexerId'),
                'size': r.get('size'),
                'size_human': _format_size(r.get('size', 0)),
                'publish_date': r.get('publishDate'),
                'seeders': r.get('seeders'),
                'leechers': r.get('leechers'),
                'info_url': r.get('infoUrl'),
                'categories': r.get('categories', [])
            })

        return {
            "status": "success",
            "count": len(formatted),
            "total": len(results),
            "results": formatted
        }

    except ProwlarrError as e:
        logger.error(f"Search error: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/check-duplicate")
async def check_duplicate(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Check for duplicates across all enabled Prowlarr indexers.

    Request body:
        {
            "tmdb_id": "12345",
            "imdb_id": "tt1234567",
            "release_name": "Movie.2024.1080p.BluRay",
            "quality": "1080p"
        }

    Returns:
        Duplicate check results per indexer
    """
    try:
        client = _get_prowlarr_client(db)

        if not client:
            return {
                "status": "error",
                "message": "Prowlarr not configured"
            }

        body = await request.json()
        tmdb_id = body.get('tmdb_id')
        imdb_id = body.get('imdb_id')
        release_name = body.get('release_name')
        quality = body.get('quality')

        # Get enabled indexers
        indexers = await client.get_enabled_indexers()

        results = {}
        overall_is_duplicate = False

        for idx in indexers:
            idx_id = idx.get('id')
            idx_name = idx.get('name')

            try:
                result = await client.check_duplicate_on_indexer(
                    indexer_id=idx_id,
                    tmdb_id=tmdb_id,
                    imdb_id=imdb_id,
                    release_name=release_name,
                    quality=quality
                )

                results[idx_name] = result

                if result.get('is_duplicate'):
                    overall_is_duplicate = True

            except Exception as e:
                results[idx_name] = {
                    'error': str(e),
                    'is_duplicate': False
                }

        return {
            "status": "success",
            "is_duplicate": overall_is_duplicate,
            "results_by_indexer": results,
            "checked_indexers": len(indexers)
        }

    except ProwlarrError as e:
        logger.error(f"Duplicate check error: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


def _format_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    if size_bytes >= 1073741824:  # 1 GB
        return f"{size_bytes / 1073741824:.2f} GB"
    elif size_bytes >= 1048576:  # 1 MB
        return f"{size_bytes / 1048576:.2f} MB"
    elif size_bytes >= 1024:  # 1 KB
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes} B"
