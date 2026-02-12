"""
Tracker API Routes for Seedarr v2.0

This module provides RESTful API endpoints for managing trackers.
Trackers represent private torrent tracker configurations with their
own authentication, piece size strategies, and upload settings.

API Endpoints:
    GET    /api/trackers           - List all trackers
    POST   /api/trackers           - Create a new tracker
    GET    /api/trackers/{id}      - Get tracker details
    PUT    /api/trackers/{id}      - Update tracker
    DELETE /api/trackers/{id}      - Delete tracker
    POST   /api/trackers/{id}/test - Test tracker connection
    GET    /trackers               - Trackers management UI page

Usage:
    These routes are registered in main.py and provide both API
    endpoints and HTML page rendering for the trackers management UI.
"""

import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.tracker import Tracker
from ..models.c411_category import C411Category
from ..models.bbcode_template import BBCodeTemplate
from ..models.naming_template import NamingTemplate
from ..adapters.tracker_factory import TrackerFactory
from ..models.settings import Settings
from ..services.configurable_uploader import (
    get_upload_templates,
    get_template_config,
    validate_upload_config
)

logger = logging.getLogger(__name__)

router = APIRouter()
# Auto-detect templates path based on working directory
import os
templates_dir = "templates" if os.path.exists("templates") else "backend/templates"
templates = Jinja2Templates(directory=templates_dir)


# ============================================================================
# Pydantic Models
# ============================================================================

class TrackerCreate(BaseModel):
    """Schema for creating a new tracker."""
    name: str = Field(..., min_length=1, max_length=100, description="Tracker name")
    slug: str = Field(..., min_length=1, max_length=50, description="URL-safe identifier")
    tracker_url: str = Field(..., min_length=1, description="Tracker base URL")

    # Authentication
    passkey: Optional[str] = Field(None, description="Passkey for announce URL")
    api_key: Optional[str] = Field(None, description="API key for Bearer auth")

    # Torrent configuration
    source_flag: Optional[str] = Field(None, description="Source flag for torrent hash")
    piece_size_strategy: str = Field("auto", description="Piece size strategy")
    announce_url_template: Optional[str] = Field(None, description="Announce URL template")

    # Upload configuration
    adapter_type: str = Field("generic", description="Adapter type")
    default_category_id: Optional[str] = Field(None, description="Default category ID")
    default_subcategory_id: Optional[str] = Field(None, description="Default subcategory ID")

    # Options
    requires_cloudflare: bool = Field(False, description="Requires FlareSolverr")
    upload_enabled: bool = Field(True, description="Enable uploads")
    priority: int = Field(0, description="Upload priority")
    enabled: bool = Field(True, description="Tracker enabled")


class TrackerUpdate(BaseModel):
    """Schema for updating a tracker."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    slug: Optional[str] = Field(None, min_length=1, max_length=50)
    tracker_url: Optional[str] = Field(None)

    passkey: Optional[str] = None
    api_key: Optional[str] = None

    source_flag: Optional[str] = None
    piece_size_strategy: Optional[str] = None
    announce_url_template: Optional[str] = None

    adapter_type: Optional[str] = None
    default_category_id: Optional[str] = None
    default_subcategory_id: Optional[str] = None
    default_template_id: Optional[int] = None  # BBCode template for descriptions
    naming_template: Optional[str] = None  # Release naming template
    category_mapping: Optional[Dict[str, Any]] = None
    upload_config: Optional[Dict[str, Any]] = None

    requires_cloudflare: Optional[bool] = None
    upload_enabled: Optional[bool] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None


class TrackerResponse(BaseModel):
    """Schema for tracker response."""
    id: int
    name: str
    slug: str
    tracker_url: str
    passkey: Optional[str]
    api_key: Optional[str]
    announce_url: Optional[str]
    source_flag: Optional[str]
    piece_size_strategy: Optional[str]
    announce_url_template: Optional[str]
    adapter_type: Optional[str]
    default_category_id: Optional[str]
    default_subcategory_id: Optional[str]
    default_template_id: Optional[int] = None  # BBCode template for descriptions
    naming_template: Optional[str] = None  # Release naming template
    category_mapping: Optional[Dict[str, Any]] = None
    upload_config: Optional[Dict[str, Any]] = None
    requires_cloudflare: bool
    upload_enabled: bool
    priority: int
    enabled: bool
    created_at: Optional[str]
    updated_at: Optional[str]

    class Config:
        from_attributes = True


class TrackerTestResult(BaseModel):
    """Schema for tracker test result."""
    success: bool
    message: str
    details: Optional[dict] = None


class UploadTemplateResponse(BaseModel):
    """Schema for upload template response."""
    name: str
    description: str
    config: Dict[str, Any]


class UploadConfigRequest(BaseModel):
    """Schema for setting upload configuration."""
    template: Optional[str] = Field(None, description="Template name to use as base")
    config: Optional[Dict[str, Any]] = Field(None, description="Custom configuration (overrides template)")


class UploadConfigValidation(BaseModel):
    """Schema for upload config validation result."""
    valid: bool
    errors: List[str] = []


# ============================================================================
# HTML Page Routes
# ============================================================================

@router.get("/trackers", response_class=HTMLResponse, tags=["trackers"])
async def trackers_page(request: Request, db: Session = Depends(get_db)):
    """
    Render the trackers management page.

    Returns:
        HTML page for managing trackers
    """
    trackers = Tracker.get_all(db)

    # Available adapter types
    adapter_types = [
        {"value": "config", "label": "Config-driven (YAML)"},
        {"value": "generic", "label": "Generic (fallback)"},
        {"value": "lacale", "label": "La Cale (legacy -> config)"},
        {"value": "c411", "label": "C411 (legacy -> config)"},
    ]

    # Piece size strategies
    piece_size_strategies = [
        {"value": "auto", "label": "Auto (torf default)"},
        {"value": "c411", "label": "C411 (optimized)"},
        {"value": "standard", "label": "Standard (conservative)"},
    ]

    # BBCode templates for description generation
    bbcode_templates = BBCodeTemplate.get_all(db)

    # Naming templates for release name formatting
    naming_templates = NamingTemplate.get_all(db)

    return templates.TemplateResponse(
        "trackers.html",
        {
            "request": request,
            "trackers": trackers,
            "adapter_types": adapter_types,
            "piece_size_strategies": piece_size_strategies,
            "bbcode_templates": bbcode_templates,
            "naming_templates": naming_templates,
        }
    )


# ============================================================================
# Helper Functions
# ============================================================================

async def _auto_configure_tracker(db: Session, tracker: Tracker) -> None:
    """
    Auto-configure a tracker after creation.

    This function:
    1. Syncs categories from tracker API if credentials are available
    2. Auto-selects matching BBCode templates

    Args:
        db: Database session
        tracker: Newly created tracker
    """
    settings = Settings.get_settings(db)

    # Auto-sync categories if credentials are available
    if tracker.api_key or tracker.passkey:
        try:
            factory = TrackerFactory(
                db,
                flaresolverr_url=settings.flaresolverr_url if settings else None
            )
            adapter = factory.get_adapter(tracker)
            categories = await adapter.get_categories()
            if categories:
                count = _sync_categories_generic(db, tracker, categories)
                logger.info(f"Auto-synced {count} categories for tracker {tracker.name}")
        except Exception as e:
            logger.warning(f"Could not auto-sync categories for {tracker.name}: {e}")

    # Auto-select matching BBCode template
    try:
        matching_template = db.query(BBCodeTemplate).filter(
            BBCodeTemplate.name.ilike(f'%{tracker.slug}%')
        ).first()
        if matching_template:
            tracker.default_template_id = matching_template.id
            logger.info(f"Auto-selected template '{matching_template.name}' for {tracker.name}")

        # Set announce URL template if not already set
        if not tracker.announce_url_template:
            tracker.announce_url_template = "{url}/announce?passkey={passkey}"

        db.commit()
    except Exception as e:
        logger.warning(f"Could not auto-select templates for {tracker.name}: {e}")


def _sync_categories_generic(db: Session, tracker: Tracker, categories: list) -> int:
    """
    Sync categories to tracker's category_mapping.

    Works for any tracker - stores categories as mapping dict.
    Supports hierarchical categories (with subcategories) for trackers like C411.

    Args:
        db: Database session
        tracker: Tracker model
        categories: List of category dicts from API

    Returns:
        Number of categories synced
    """
    category_mapping = tracker.category_mapping or {}
    has_subcategories = any(cat.get('subcategories') for cat in categories)

    if has_subcategories:
        # Hierarchical categories (e.g., C411: category + subcategory system)
        logger.info(f"Detected hierarchical categories for {tracker.name}")

        # Also sync to C411Category table if available
        try:
            from app.models.c411_category import C411Category
            # Convert to raw format expected by C411Category.sync_from_api
            raw_cats = []
            for cat in categories:
                raw_cat = {
                    'id': cat.get('category_id', ''),
                    'name': cat.get('name', ''),
                    'subcategories': cat.get('subcategories', [])
                }
                raw_cats.append(raw_cat)
            C411Category.sync_from_api(db, tracker.id, raw_cats)
        except Exception as e:
            logger.debug(f"C411Category sync skipped: {e}")

        default_category_id = None
        subcategory_mapping = {}

        for cat in categories:
            cat_id = cat.get('category_id', '')
            cat_name = cat.get('name', '')
            subcats = cat.get('subcategories') or []

            # "Films & Vidéos" is the main media category
            if _matches_pattern(cat_name, 'film') and (_matches_pattern(cat_name, 'video') or subcats):
                # Check if subcategories contain media types (Film, Série, etc.)
                has_media_subcats = any(
                    _matches_pattern(s.get('name', ''), 'film', 'serie', 'anim', 'doc')
                    for s in subcats
                )
                if has_media_subcats or _matches_pattern(cat_name, 'video'):
                    default_category_id = cat_id

                    for sub in subcats:
                        sub_id = str(sub.get('id', ''))
                        sub_name = sub.get('name') or ''

                        # Film/Movie (not animation or série)
                        if _matches_pattern(sub_name, 'film', 'movie') and not _matches_pattern(sub_name, 'anim', 'serie', 'series'):
                            subcategory_mapping['movie'] = sub_id
                            subcategory_mapping['movie_4k'] = sub_id
                            subcategory_mapping['movie_2160p'] = sub_id
                            subcategory_mapping['movie_1080p'] = sub_id
                            subcategory_mapping['movie_720p'] = sub_id

                        # Série TV / TV Series
                        elif _matches_pattern(sub_name, 'serie', 'series') and not _matches_pattern(sub_name, 'anim'):
                            subcategory_mapping['tv'] = sub_id
                            subcategory_mapping['series'] = sub_id
                            subcategory_mapping['tv_4k'] = sub_id
                            subcategory_mapping['tv_2160p'] = sub_id
                            subcategory_mapping['tv_1080p'] = sub_id
                            subcategory_mapping['tv_720p'] = sub_id
                            subcategory_mapping['series_4k'] = sub_id
                            subcategory_mapping['series_1080p'] = sub_id
                            subcategory_mapping['series_720p'] = sub_id

                        # Animation Série (anime series)
                        elif _matches_pattern(sub_name, 'anim') and _matches_pattern(sub_name, 'serie', 'series'):
                            subcategory_mapping['anime_series'] = sub_id

                        # Animation (anime movie)
                        elif _matches_pattern(sub_name, 'anim') and not _matches_pattern(sub_name, 'serie', 'series'):
                            subcategory_mapping['anime_movie'] = sub_id

                        # Documentaire / Documentary
                        elif _matches_pattern(sub_name, 'doc', 'documentaire', 'documentary'):
                            subcategory_mapping['documentary'] = sub_id
                            subcategory_mapping['documentary_4k'] = sub_id
                            subcategory_mapping['documentary_1080p'] = sub_id
                            subcategory_mapping['documentary_720p'] = sub_id

                        # Concert / Spectacle
                        elif _matches_pattern(sub_name, 'concert', 'spectacle', 'show', 'live'):
                            subcategory_mapping['concert'] = sub_id

                        # Émission TV / TV Show
                        elif _matches_pattern(sub_name, 'emission', 'tvshow'):
                            subcategory_mapping['tv_show'] = sub_id

            # Audio category
            elif _matches_pattern(cat_name, 'audio', 'musique', 'music'):
                subcats = cat.get('subcategories') or []
                for sub in subcats:
                    sub_id = str(sub.get('id', ''))
                    sub_name = sub.get('name') or ''
                    if _matches_pattern(sub_name, 'musique', 'music'):
                        subcategory_mapping['music'] = sub_id
                        category_mapping['music_category'] = cat_id

            # Ebook category
            elif _matches_pattern(cat_name, 'ebook', 'book', 'livre'):
                category_mapping['book_category'] = cat_id

            # Games category
            elif _matches_pattern(cat_name, 'jeux', 'game', 'jeu'):
                category_mapping['game_category'] = cat_id

        # Set main category (parent) for movie/tv/anime/documentary
        if default_category_id:
            category_mapping['default'] = default_category_id
            category_mapping['movie_category'] = default_category_id
            category_mapping['tv_category'] = default_category_id
            category_mapping['anime_category'] = default_category_id
            category_mapping['documentary_category'] = default_category_id

        # Merge subcategory mappings
        category_mapping.update(subcategory_mapping)

        # Set default IDs on tracker model
        if default_category_id and not tracker.default_category_id:
            tracker.default_category_id = default_category_id
        if subcategory_mapping.get('movie') and not getattr(tracker, 'default_subcategory_id', None):
            tracker.default_subcategory_id = subcategory_mapping['movie']

        logger.info(f"Hierarchical category_mapping for {tracker.name}: {category_mapping}")

    else:
        # Flat categories (simple tracker)
        for cat in categories:
            cat_name = cat.get('name', '').lower()
            cat_id = cat.get('category_id', '')

            # Map common category names to standard keys
            if 'film' in cat_name or 'movie' in cat_name:
                category_mapping['movie_category'] = cat_id
            elif 'serie' in cat_name or 'séries' in cat_name or 'tv' in cat_name:
                category_mapping['tv_category'] = cat_id
            elif 'anime' in cat_name or 'animation' in cat_name:
                category_mapping['anime_category'] = cat_id
            elif 'doc' in cat_name:
                category_mapping['documentary_category'] = cat_id

            # Also store by name for subcategory resolution
            category_mapping[cat_name] = cat_id

    tracker.category_mapping = category_mapping
    db.commit()

    return len(categories)


# ============================================================================
# API Routes
# ============================================================================

@router.get("/api/trackers", response_model=List[TrackerResponse], tags=["trackers"])
async def list_trackers(
    enabled_only: bool = Query(False, description="Only return enabled trackers"),
    db: Session = Depends(get_db)
):
    """
    List all trackers.

    Args:
        enabled_only: If True, only return enabled trackers
        db: Database session

    Returns:
        List of tracker configurations
    """
    logger.info("Listing trackers")

    if enabled_only:
        trackers = Tracker.get_enabled(db)
    else:
        trackers = Tracker.get_all(db)

    return [TrackerResponse(**t.to_dict(mask_secrets=True)) for t in trackers]


@router.post("/api/trackers", response_model=TrackerResponse, tags=["trackers"])
async def create_tracker(
    tracker_data: TrackerCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new tracker.

    Args:
        tracker_data: Tracker configuration
        db: Database session

    Returns:
        Created tracker

    Raises:
        HTTPException: If tracker with same name/slug exists
    """
    logger.info(f"Creating tracker: {tracker_data.name}")

    # Check for duplicate name
    existing = Tracker.get_by_slug(db, tracker_data.slug)
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Tracker with slug '{tracker_data.slug}' already exists"
        )

    # Check for duplicate name
    existing_name = db.query(Tracker).filter(Tracker.name == tracker_data.name).first()
    if existing_name:
        raise HTTPException(
            status_code=400,
            detail=f"Tracker with name '{tracker_data.name}' already exists"
        )

    # Create tracker
    tracker = Tracker.create(db, **tracker_data.model_dump())

    logger.info(f"Created tracker: {tracker.name} (id={tracker.id})")

    # Auto-configure tracker (sync categories, select templates)
    await _auto_configure_tracker(db, tracker)

    return TrackerResponse(**tracker.to_dict(mask_secrets=True))


# ============================================================================
# Upload Configuration Routes (MUST be before {tracker_id} routes)
# ============================================================================

@router.get("/api/trackers/available-configs", tags=["trackers"])
async def list_available_configs():
    """
    List available tracker configuration files (YAML/JSON).

    These configs can be used with adapter_type='config' to create
    new trackers without writing Python code.

    Returns:
        List of available config slugs
    """
    try:
        from ..adapters.tracker_config_loader import get_config_loader
        loader = get_config_loader()
        configs = loader.get_available_configs()

        result = []
        for slug in configs:
            if slug.startswith('_'):  # Skip template files
                continue
            try:
                config = loader.load(slug)
                tracker_info = config.get("tracker", {})
                result.append({
                    "slug": slug,
                    "name": tracker_info.get("name", slug),
                    "description": tracker_info.get("description", ""),
                    "auth_type": config.get("auth", {}).get("type", "unknown"),
                    "cloudflare": config.get("cloudflare", {}).get("enabled", False)
                })
            except Exception as e:
                result.append({
                    "slug": slug,
                    "name": slug,
                    "description": f"Error loading: {e}",
                    "auth_type": "error",
                    "cloudflare": False
                })

        return {
            "configs": result,
            "count": len(result)
        }
    except Exception as e:
        return {
            "configs": [],
            "count": 0,
            "error": str(e)
        }


@router.get("/api/trackers/upload-templates", tags=["trackers"])
async def list_upload_templates():
    """
    List all available upload configuration templates.

    Templates provide pre-configured upload settings for common tracker patterns.

    Returns:
        Dictionary of template_id -> template details
    """
    tpl = get_upload_templates()
    return {
        "templates": tpl
    }


@router.get("/api/trackers/upload-templates/{template_id}", tags=["trackers"])
async def get_upload_template(template_id: str):
    """
    Get a specific upload configuration template.

    Args:
        template_id: Template identifier (e.g., "rest_api_bearer")

    Returns:
        Template configuration

    Raises:
        HTTPException: If template not found
    """
    config = get_template_config(template_id)

    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Template '{template_id}' not found"
        )

    tpl = get_upload_templates()
    template_info = tpl.get(template_id, {})

    return {
        "id": template_id,
        "name": template_info.get("name", template_id),
        "description": template_info.get("description", ""),
        "config": config
    }


# ============================================================================
# Tracker CRUD Routes (with {tracker_id})
# ============================================================================

@router.get("/api/trackers/{tracker_id}", response_model=TrackerResponse, tags=["trackers"])
async def get_tracker(
    tracker_id: int,
    db: Session = Depends(get_db)
):
    """
    Get tracker by ID.

    Args:
        tracker_id: Tracker ID
        db: Database session

    Returns:
        Tracker configuration

    Raises:
        HTTPException: If tracker not found
    """
    tracker = Tracker.get_by_id(db, tracker_id)

    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    return TrackerResponse(**tracker.to_dict(mask_secrets=True))


@router.put("/api/trackers/{tracker_id}", response_model=TrackerResponse, tags=["trackers"])
async def update_tracker(
    tracker_id: int,
    tracker_data: TrackerUpdate,
    db: Session = Depends(get_db)
):
    """
    Update an existing tracker.

    Args:
        tracker_id: Tracker ID to update
        tracker_data: Fields to update
        db: Database session

    Returns:
        Updated tracker

    Raises:
        HTTPException: If tracker not found
    """
    logger.info(f"Updating tracker: {tracker_id}")

    # Filter out None values
    update_data = {k: v for k, v in tracker_data.model_dump().items() if v is not None}

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    tracker = Tracker.update(db, tracker_id, **update_data)

    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    logger.info(f"Updated tracker: {tracker.name}")

    return TrackerResponse(**tracker.to_dict(mask_secrets=True))


@router.delete("/api/trackers/{tracker_id}", tags=["trackers"])
async def delete_tracker(
    tracker_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete a tracker.

    Args:
        tracker_id: Tracker ID to delete
        db: Database session

    Returns:
        Success message

    Raises:
        HTTPException: If tracker not found
    """
    logger.info(f"Deleting tracker: {tracker_id}")

    deleted = Tracker.delete(db, tracker_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Tracker not found")

    logger.info(f"Deleted tracker: {tracker_id}")

    return {"message": "Tracker deleted successfully"}


class DuplicateCheckRequest(BaseModel):
    """Schema for duplicate check request."""
    tmdb_id: Optional[str] = Field(None, description="TMDB ID to search")
    imdb_id: Optional[str] = Field(None, description="IMDB ID to search")
    release_name: Optional[str] = Field(None, description="Release name to search")
    quality: Optional[str] = Field(None, description="Quality filter (e.g., 1080p)")
    file_size: Optional[int] = Field(None, description="File size in bytes for exact match detection")


class DuplicateCheckResult(BaseModel):
    """Schema for duplicate check result."""
    is_duplicate: bool
    exact_match: bool = False
    exact_matches: List[Dict[str, Any]] = []
    existing_torrents: List[Dict[str, Any]] = []
    search_method: str
    message: str


@router.post("/api/trackers/{tracker_id}/check-duplicate", response_model=DuplicateCheckResult, tags=["trackers"])
async def check_duplicate(
    tracker_id: int,
    request: DuplicateCheckRequest,
    db: Session = Depends(get_db)
):
    """
    Check if a release already exists on the tracker.

    Uses cascade search strategy:
    1. Search by TMDB ID (most reliable)
    2. Search by IMDB ID (fallback)
    3. Search by release name (final fallback)

    Args:
        tracker_id: Tracker ID to check
        request: Search parameters
        db: Database session

    Returns:
        Duplicate check result
    """
    logger.info(f"Checking duplicates on tracker {tracker_id}")

    tracker = Tracker.get_by_id(db, tracker_id)
    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    # Get FlareSolverr URL from settings
    settings = Settings.get_settings(db)
    flaresolverr_url = settings.flaresolverr_url if settings else None

    try:
        # Create adapter
        factory = TrackerFactory(db, flaresolverr_url=flaresolverr_url)
        adapter = factory.get_adapter(tracker)

        # Perform duplicate check
        result = await adapter.check_duplicate(
            tmdb_id=request.tmdb_id,
            imdb_id=request.imdb_id,
            release_name=request.release_name,
            quality=request.quality,
            file_size=request.file_size
        )

        return DuplicateCheckResult(**result)

    except Exception as e:
        logger.error(f"Duplicate check failed: {e}")
        return DuplicateCheckResult(
            is_duplicate=False,
            existing_torrents=[],
            search_method="error",
            message=f"Check failed: {str(e)}"
        )


import re
import unicodedata


def _normalize_category_name(name: str) -> str:
    """
    Normalize category name for flexible matching.

    Removes accents, special characters, and converts to lowercase
    for case-insensitive and accent-insensitive comparisons.

    Args:
        name: Category name to normalize

    Returns:
        Normalized string with only lowercase alphanumeric characters
    """
    # Normalize unicode characters (NFD decomposes accents)
    normalized = unicodedata.normalize('NFD', name)
    # Remove diacritics (accents)
    without_accents = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
    # Remove non-alphanumeric and lowercase
    return re.sub(r'[^a-z0-9]', '', without_accents.lower())


def _matches_pattern(name: str, *patterns: str) -> bool:
    """
    Check if name matches any of the given patterns.

    Uses normalized comparison for flexible matching that's insensitive
    to accents, case, spaces, and special characters.

    Args:
        name: Category name to check
        *patterns: Pattern strings to match against

    Returns:
        True if normalized name contains any of the patterns
    """
    normalized = _normalize_category_name(name)
    return any(p in normalized for p in patterns)


async def _sync_c411_categories(
    db: Session,
    tracker: Tracker,
    categories: List[Dict[str, Any]]
) -> int:
    """
    Sync C411 categories to database.

    C411 uses a category + subcategory system:
    - categoryId: Main category (e.g., 1 = "Films & Vidéos")
    - subcategoryId: Specific type (e.g., 6 = "Film", 7 = "Série TV")

    Uses flexible pattern matching for category detection that is:
    - Case insensitive (Film, film, FILM all match)
    - Accent insensitive (série, serie both match)
    - Space/punctuation insensitive (Série TV, SerieTV both match)

    Args:
        db: Database session
        tracker: Tracker instance
        categories: List of category dicts from C411 API

    Returns:
        Number of categories synced
    """
    logger.info(f"Syncing {len(categories)} categories for tracker {tracker.name}")

    # Use the C411Category model to sync
    count = C411Category.sync_from_api(db, tracker.id, categories)

    # Build category_mapping based on subcategories
    # C411 structure: category has subcategories, uploads use subcategory_id
    if categories:
        category_mapping = {}
        subcategory_mapping = {}
        default_category_id = None
        default_subcategory_id = None

        for cat in categories:
            cat_id = str(cat.get('id', ''))
            cat_name = cat.get('name') or cat.get('label', '')

            # "Films & Vidéos" is the main media category
            # Flexible match: film + (video or vidéo)
            if _matches_pattern(cat_name, 'film') and _matches_pattern(cat_name, 'video'):
                default_category_id = cat_id

                # Process subcategories
                subcats = cat.get('subcategories') or []
                for sub in subcats:
                    sub_id = str(sub.get('id', ''))
                    sub_name = sub.get('name') or ''

                    # Map subcategories to media types using flexible matching
                    # Film/Movie (but not animation or série)
                    if _matches_pattern(sub_name, 'film', 'movie') and not _matches_pattern(sub_name, 'anim', 'serie', 'series'):
                        subcategory_mapping['movie'] = sub_id
                        subcategory_mapping['movie_4k'] = sub_id
                        subcategory_mapping['movie_1080p'] = sub_id
                        subcategory_mapping['movie_720p'] = sub_id
                        if not default_subcategory_id:
                            default_subcategory_id = sub_id

                    # Série TV / TV Series
                    elif _matches_pattern(sub_name, 'serie', 'series') and not _matches_pattern(sub_name, 'anim'):
                        subcategory_mapping['tv'] = sub_id
                        subcategory_mapping['series'] = sub_id
                        subcategory_mapping['series_4k'] = sub_id
                        subcategory_mapping['series_1080p'] = sub_id
                        subcategory_mapping['series_720p'] = sub_id

                    # Animation Série (anime series)
                    elif _matches_pattern(sub_name, 'anim') and _matches_pattern(sub_name, 'serie', 'series'):
                        subcategory_mapping['anime_series'] = sub_id

                    # Animation (anime movie) - animation but not série
                    elif _matches_pattern(sub_name, 'anim') and not _matches_pattern(sub_name, 'serie', 'series'):
                        subcategory_mapping['anime_movie'] = sub_id

                    # Documentaire / Documentary
                    elif _matches_pattern(sub_name, 'doc', 'documentaire', 'documentary'):
                        subcategory_mapping['documentary'] = sub_id

                    # Concert / Spectacle
                    elif _matches_pattern(sub_name, 'concert', 'spectacle', 'show', 'live'):
                        subcategory_mapping['concert'] = sub_id

                    # Émission TV / TV Show
                    elif _matches_pattern(sub_name, 'emission', 'tvshow'):
                        subcategory_mapping['tv_show'] = sub_id

            # Audio category
            elif _matches_pattern(cat_name, 'audio', 'musique', 'music'):
                subcats = cat.get('subcategories') or []
                for sub in subcats:
                    sub_id = str(sub.get('id', ''))
                    sub_name = sub.get('name') or ''
                    if _matches_pattern(sub_name, 'musique', 'music'):
                        subcategory_mapping['music'] = sub_id
                        category_mapping['music_category'] = cat_id

            # Ebook category
            elif _matches_pattern(cat_name, 'ebook', 'book', 'livre'):
                category_mapping['book_category'] = cat_id

            # Games category
            elif _matches_pattern(cat_name, 'jeux', 'game', 'jeu'):
                category_mapping['game_category'] = cat_id

        # Store the main category ID (for "Films & Vidéos")
        if default_category_id:
            category_mapping['default'] = default_category_id
            category_mapping['movie_category'] = default_category_id
            category_mapping['tv_category'] = default_category_id

        # Merge subcategory mapping into category_mapping
        category_mapping.update(subcategory_mapping)

        # Update tracker with mappings
        if category_mapping:
            tracker.category_mapping = category_mapping
            logger.info(f"Updated category_mapping for {tracker.name}: {category_mapping}")

        # Set defaults if not already set
        if default_category_id and not tracker.default_category_id:
            tracker.default_category_id = default_category_id
            logger.info(f"Set default_category_id={default_category_id}")

        if default_subcategory_id and not tracker.default_subcategory_id:
            tracker.default_subcategory_id = default_subcategory_id
            logger.info(f"Set default_subcategory_id={default_subcategory_id}")

        db.commit()

    return count


@router.post("/api/trackers/{tracker_id}/test", response_model=TrackerTestResult, tags=["trackers"])
async def test_tracker_connection(
    tracker_id: int,
    db: Session = Depends(get_db)
):
    """
    Test tracker connection.

    Tests the tracker configuration by:
    1. Creating an adapter instance
    2. Attempting to authenticate
    3. Validating credentials
    4. Syncing categories (for C411)

    Args:
        tracker_id: Tracker ID to test
        db: Database session

    Returns:
        Test result with success status and message
    """
    logger.info(f"Testing tracker connection: {tracker_id}")

    tracker = Tracker.get_by_id(db, tracker_id)

    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    # Get FlareSolverr URL from settings
    settings = Settings.get_settings(db)
    flaresolverr_url = settings.flaresolverr_url if settings else None

    try:
        # Create adapter
        factory = TrackerFactory(db, flaresolverr_url=flaresolverr_url)
        adapter = factory.get_adapter(tracker)

        # Test authentication
        result = await adapter.validate_credentials()

        if result:
            # Perform health check
            health = await adapter.health_check()

            # Sync categories for all trackers
            categories_synced = 0
            try:
                categories = await adapter.get_categories()
                if categories:
                    categories_synced = _sync_categories_generic(db, tracker, categories)
                    logger.info(f"Synced {categories_synced} categories for {tracker.name}")
            except Exception as e:
                logger.warning(f"Could not sync categories for {tracker.name}: {e}")

            message = f"Successfully connected to {tracker.name}"
            if categories_synced > 0:
                message += f" (synced {categories_synced} categories)"

            return TrackerTestResult(
                success=True,
                message=message,
                details=health
            )
        else:
            return TrackerTestResult(
                success=False,
                message=f"Credentials validation failed for {tracker.name}",
                details=None
            )

    except Exception as e:
        logger.error(f"Tracker test failed: {e}")
        return TrackerTestResult(
            success=False,
            message=f"Connection test failed: {str(e)}",
            details=None
        )


@router.get("/api/trackers/{tracker_id}/categories", tags=["trackers"])
async def get_tracker_categories(
    tracker_id: int,
    db: Session = Depends(get_db)
):
    """
    Get categories for a tracker (C411 only).

    Returns cached categories from database. Use /test endpoint to sync.

    Args:
        tracker_id: Tracker ID

    Returns:
        List of categories with subcategories
    """
    tracker = Tracker.get_by_id(db, tracker_id)
    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    categories = C411Category.get_all_for_tracker(db, tracker_id)

    return {
        "tracker_id": tracker_id,
        "tracker_name": tracker.name,
        "categories": [cat.to_dict() for cat in categories],
        "count": len(categories)
    }


@router.post("/api/trackers/{tracker_id}/sync-categories", tags=["trackers"])
async def sync_tracker_categories(
    tracker_id: int,
    db: Session = Depends(get_db)
):
    """
    Manually sync categories from tracker API.

    Args:
        tracker_id: Tracker ID

    Returns:
        Sync result with count of categories
    """
    tracker = Tracker.get_by_id(db, tracker_id)
    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    try:
        # Get FlareSolverr URL from settings
        settings = Settings.get_settings(db)
        flaresolverr_url = settings.flaresolverr_url if settings else None

        # Create adapter
        factory = TrackerFactory(db, flaresolverr_url=flaresolverr_url)
        adapter = factory.get_adapter(tracker)

        # Fetch and sync categories
        categories = await adapter.get_categories()
        if categories:
            count = _sync_categories_generic(db, tracker, categories)
            return {
                "success": True,
                "message": f"Synced {count} categories",
                "count": count,
                "categories": categories
            }
        else:
            return {
                "success": False,
                "message": "No categories returned from API"
            }

    except Exception as e:
        logger.error(f"Failed to sync categories: {e}")
        return {
            "success": False,
            "message": f"Sync failed: {str(e)}"
        }


@router.post("/api/trackers/{tracker_id}/toggle", response_model=TrackerResponse, tags=["trackers"])
async def toggle_tracker(
    tracker_id: int,
    db: Session = Depends(get_db)
):
    """
    Toggle tracker enabled status.

    Args:
        tracker_id: Tracker ID to toggle
        db: Database session

    Returns:
        Updated tracker
    """
    tracker = Tracker.get_by_id(db, tracker_id)

    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    # Toggle enabled status
    tracker = Tracker.update(db, tracker_id, enabled=not tracker.enabled)

    logger.info(f"Toggled tracker {tracker.name}: enabled={tracker.enabled}")

    return TrackerResponse(**tracker.to_dict(mask_secrets=True))


@router.post("/api/trackers/{tracker_id}/toggle-upload", response_model=TrackerResponse, tags=["trackers"])
async def toggle_tracker_upload(
    tracker_id: int,
    db: Session = Depends(get_db)
):
    """
    Toggle tracker upload enabled status.

    Args:
        tracker_id: Tracker ID to toggle
        db: Database session

    Returns:
        Updated tracker
    """
    tracker = Tracker.get_by_id(db, tracker_id)

    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    # Toggle upload_enabled status
    tracker = Tracker.update(db, tracker_id, upload_enabled=not tracker.upload_enabled)

    logger.info(f"Toggled tracker {tracker.name} upload: upload_enabled={tracker.upload_enabled}")

    return TrackerResponse(**tracker.to_dict(mask_secrets=True))


# ============================================================================
# Upload Configuration Routes (per tracker)
# ============================================================================

@router.get("/api/trackers/{tracker_id}/upload-config", tags=["trackers"])
async def get_tracker_upload_config(
    tracker_id: int,
    db: Session = Depends(get_db)
):
    """
    Get upload configuration for a tracker.

    Args:
        tracker_id: Tracker ID
        db: Database session

    Returns:
        Upload configuration or None if not configured
    """
    tracker = Tracker.get_by_id(db, tracker_id)

    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    return {
        "tracker_id": tracker.id,
        "tracker_name": tracker.name,
        "upload_config": tracker.upload_config,
        "has_config": tracker.upload_config is not None
    }


@router.put("/api/trackers/{tracker_id}/upload-config", tags=["trackers"])
async def set_tracker_upload_config(
    tracker_id: int,
    request: UploadConfigRequest,
    db: Session = Depends(get_db)
):
    """
    Set upload configuration for a tracker.

    You can either:
    1. Use a template by providing "template" field
    2. Provide a custom "config" directly
    3. Use template as base and override with custom config

    Args:
        tracker_id: Tracker ID
        request: Upload config request
        db: Database session

    Returns:
        Updated tracker with new upload config
    """
    tracker = Tracker.get_by_id(db, tracker_id)

    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    config = None

    # Start with template if provided
    if request.template:
        config = get_template_config(request.template)
        if not config:
            raise HTTPException(
                status_code=400,
                detail=f"Template '{request.template}' not found"
            )

    # Override with custom config if provided
    if request.config:
        if config:
            # Merge template with custom config
            config = deep_merge(config, request.config)
        else:
            config = request.config

    if not config:
        raise HTTPException(
            status_code=400,
            detail="Either 'template' or 'config' must be provided"
        )

    # Validate config
    is_valid, errors = validate_upload_config(config)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid upload configuration: {', '.join(errors)}"
        )

    # Update tracker
    tracker = Tracker.update(db, tracker_id, upload_config=config)

    logger.info(f"Updated upload config for tracker {tracker.name}")

    return {
        "success": True,
        "message": f"Upload configuration updated for {tracker.name}",
        "upload_config": tracker.upload_config
    }


@router.delete("/api/trackers/{tracker_id}/upload-config", tags=["trackers"])
async def delete_tracker_upload_config(
    tracker_id: int,
    db: Session = Depends(get_db)
):
    """
    Remove upload configuration from a tracker.

    This will make the tracker use its adapter_type for uploads instead.

    Args:
        tracker_id: Tracker ID
        db: Database session

    Returns:
        Success message
    """
    tracker = Tracker.get_by_id(db, tracker_id)

    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    tracker = Tracker.update(db, tracker_id, upload_config=None)

    logger.info(f"Removed upload config for tracker {tracker.name}")

    return {
        "success": True,
        "message": f"Upload configuration removed for {tracker.name}"
    }


@router.post("/api/trackers/{tracker_id}/upload-config/validate", response_model=UploadConfigValidation, tags=["trackers"])
async def validate_tracker_upload_config(
    tracker_id: int,
    config: Dict[str, Any],
    db: Session = Depends(get_db)
):
    """
    Validate an upload configuration without saving it.

    Args:
        tracker_id: Tracker ID (for context)
        config: Configuration to validate
        db: Database session

    Returns:
        Validation result with errors if any
    """
    tracker = Tracker.get_by_id(db, tracker_id)

    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker not found")

    is_valid, errors = validate_upload_config(config)

    return UploadConfigValidation(
        valid=is_valid,
        errors=errors
    )


# ============================================================================
# Helper Functions
# ============================================================================

def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries.

    Args:
        base: Base dictionary
        override: Override dictionary (takes precedence)

    Returns:
        Merged dictionary
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result
