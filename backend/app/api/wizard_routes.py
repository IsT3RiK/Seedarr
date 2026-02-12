"""
Setup Wizard API Routes

Provides API endpoints for the first-time setup wizard.
The wizard guides users through initial configuration:
1. Prowlarr setup
2. Tracker import
3. TMDB API key
4. qBittorrent configuration
5. Final test

Endpoints:
    GET  /api/wizard/status    - Check if wizard should be shown
    POST /api/wizard/complete  - Mark wizard as completed
    GET  /wizard               - Wizard UI page
"""

import logging
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.database import get_db
from app.models.settings import Settings
from app.models.tracker import Tracker

logger = logging.getLogger(__name__)

router = APIRouter()

# Auto-detect templates path based on working directory
import os
templates_dir = "templates" if os.path.exists("templates") else "backend/templates"
templates = Jinja2Templates(directory=templates_dir)


@router.get("/api/wizard/status")
async def wizard_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Check if the setup wizard should be displayed.

    The wizard is shown if:
    - wizard_completed is False AND
    - No essential configuration exists (no trackers, no Prowlarr, no TMDB)

    Returns:
        JSON with needs_wizard flag and completed steps
    """
    settings = Settings.get_settings(db)
    trackers = Tracker.get_all(db)

    # Check if wizard was explicitly completed
    if settings.wizard_completed:
        return {
            "needs_wizard": False,
            "completed_steps": {
                "prowlarr": bool(settings.prowlarr_url),
                "trackers": len(trackers) > 0,
                "tmdb": bool(settings.tmdb_api_key),
                "qbittorrent": bool(settings.qbittorrent_host)
            }
        }

    # Show wizard if nothing is configured
    needs_wizard = (
        not settings.prowlarr_url and
        not trackers and
        not settings.tmdb_api_key
    )

    return {
        "needs_wizard": needs_wizard,
        "completed_steps": {
            "prowlarr": bool(settings.prowlarr_url),
            "trackers": len(trackers) > 0,
            "tmdb": bool(settings.tmdb_api_key),
            "qbittorrent": bool(settings.qbittorrent_host)
        }
    }


@router.post("/api/wizard/complete")
async def complete_wizard(db: Session = Depends(get_db)) -> Dict[str, str]:
    """
    Mark the setup wizard as completed.

    This prevents the wizard from showing again even if configuration
    is incomplete (user chose to skip).

    Returns:
        Success status
    """
    settings = Settings.get_settings(db)
    settings.wizard_completed = True
    db.commit()

    logger.info("Setup wizard marked as completed")
    return {"status": "ok", "message": "Wizard completed"}


@router.post("/api/wizard/skip")
async def skip_wizard(db: Session = Depends(get_db)) -> Dict[str, str]:
    """
    Skip the setup wizard without completing configuration.

    Marks wizard as completed so it won't show again.

    Returns:
        Success status
    """
    settings = Settings.get_settings(db)
    settings.wizard_completed = True
    db.commit()

    logger.info("Setup wizard skipped by user")
    return {"status": "ok", "message": "Wizard skipped"}


@router.get("/wizard", response_class=HTMLResponse)
async def wizard_page(request: Request, db: Session = Depends(get_db)):
    """
    Render the setup wizard page.

    Returns:
        HTML wizard page
    """
    settings = Settings.get_settings(db)
    trackers = Tracker.get_all(db)

    # Determine current step based on what's configured
    current_step = 1  # Start with welcome

    if settings.prowlarr_url:
        current_step = 2  # Prowlarr done, go to import
    if trackers:
        current_step = 3  # Trackers done, go to TMDB
    if settings.tmdb_api_key:
        current_step = 4  # TMDB done, go to qBittorrent
    if settings.qbittorrent_host:
        current_step = 5  # qBit done, go to test
    if settings.wizard_completed:
        current_step = 6  # All done

    return templates.TemplateResponse(
        "wizard.html",
        {
            "request": request,
            "settings": settings.to_dict(mask_secrets=True),
            "trackers": [t.to_dict(mask_secrets=True) for t in trackers],
            "current_step": current_step,
            "completed_steps": {
                "prowlarr": bool(settings.prowlarr_url),
                "trackers": len(trackers) > 0,
                "tmdb": bool(settings.tmdb_api_key),
                "qbittorrent": bool(settings.qbittorrent_host)
            }
        }
    )
