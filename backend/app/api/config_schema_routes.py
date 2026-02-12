"""
Config Schema API Routes for Seedarr v2.0

This module provides API endpoints for managing tracker configuration schemas (YAML files).
Allows viewing, editing, and validating tracker configs directly from the UI.

API Endpoints:
    GET    /api/config-schemas           - List all config schemas
    GET    /api/config-schemas/{slug}    - Get config schema content
    PUT    /api/config-schemas/{slug}    - Update config schema
    POST   /api/config-schemas           - Create new config schema
    DELETE /api/config-schemas/{slug}    - Delete config schema
    POST   /api/config-schemas/validate  - Validate config without saving
    GET    /config-schemas               - Config schemas editor UI page
"""

import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import yaml

from ..adapters.tracker_config_loader import get_config_loader, TrackerConfigLoader
from ..database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Auto-detect templates path
templates_dir = "templates" if os.path.exists("templates") else "backend/templates"
templates = Jinja2Templates(directory=templates_dir)


# ============================================================================
# Pydantic Models
# ============================================================================

class ConfigSchemaCreate(BaseModel):
    """Schema for creating a new config."""
    slug: str = Field(..., min_length=1, max_length=50, description="Config identifier (filename without .yaml)")
    content: str = Field(..., description="YAML content")


class ConfigSchemaUpdate(BaseModel):
    """Schema for updating a config."""
    content: str = Field(..., description="YAML content")


class ConfigSchemaResponse(BaseModel):
    """Schema for config response."""
    slug: str
    name: str
    description: str
    content: str
    auth_type: str
    cloudflare_enabled: bool
    has_workflow: bool
    has_mappings: bool
    validation_errors: List[str] = []


class ConfigSchemaListItem(BaseModel):
    """Schema for config list item."""
    slug: str
    name: str
    description: str
    auth_type: str
    cloudflare_enabled: bool
    has_workflow: bool
    has_mappings: bool


class ValidationResult(BaseModel):
    """Schema for validation result."""
    valid: bool
    errors: List[str] = []
    warnings: List[str] = []


# ============================================================================
# Helper Functions
# ============================================================================

def get_config_path(slug: str) -> Path:
    """Get the path to a config file."""
    loader = get_config_loader()
    return loader.config_dir / f"{slug}.yaml"


def read_config_file(slug: str) -> str:
    """Read raw YAML content from config file."""
    config_path = get_config_path(slug)
    if not config_path.exists():
        raise HTTPException(status_code=404, detail=f"Config '{slug}' not found")

    with open(config_path, 'r', encoding='utf-8') as f:
        return f.read()


def write_config_file(slug: str, content: str) -> None:
    """Write YAML content to config file."""
    config_path = get_config_path(slug)

    # Ensure directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(content)


def parse_config(content: str) -> Dict[str, Any]:
    """Parse YAML content and return dict."""
    try:
        return yaml.safe_load(content) or {}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")


def get_config_info(slug: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract info from config dict."""
    tracker = config.get("tracker", {})
    auth = config.get("auth", {})
    cloudflare = config.get("cloudflare", {})

    return {
        "slug": slug,
        "name": tracker.get("name", slug),
        "description": tracker.get("description", ""),
        "auth_type": auth.get("type", "unknown"),
        "cloudflare_enabled": cloudflare.get("enabled", False),
        "has_workflow": bool(config.get("workflow")),
        "has_mappings": bool(config.get("mappings")),
    }


# ============================================================================
# HTML Page Routes
# ============================================================================

@router.get("/config-schemas", response_class=HTMLResponse, tags=["config-schemas"])
async def config_schemas_page(request: Request):
    """
    Render the config schemas editor page.

    Returns:
        HTML page for editing tracker configurations
    """
    loader = get_config_loader()
    configs = loader.get_available_configs()

    config_list = []
    for slug in sorted(configs):
        if slug.startswith('_'):  # Skip template files
            continue
        try:
            config = loader.load(slug)
            info = get_config_info(slug, config)
            config_list.append(info)
        except Exception as e:
            config_list.append({
                "slug": slug,
                "name": slug,
                "description": f"Error: {e}",
                "auth_type": "error",
                "cloudflare_enabled": False,
                "has_workflow": False,
                "has_mappings": False,
            })

    return templates.TemplateResponse(
        "config_schemas.html",
        {
            "request": request,
            "configs": config_list,
        }
    )


# ============================================================================
# API Routes
# ============================================================================

@router.get("/api/config-schemas", tags=["config-schemas"])
async def list_config_schemas():
    """
    List all available config schemas.

    Returns:
        List of config schema metadata
    """
    loader = get_config_loader()
    configs = loader.get_available_configs()

    result = []
    for slug in sorted(configs):
        if slug.startswith('_'):  # Skip template files
            continue
        try:
            config = loader.load(slug)
            info = get_config_info(slug, config)
            result.append(ConfigSchemaListItem(**info))
        except Exception as e:
            result.append(ConfigSchemaListItem(
                slug=slug,
                name=slug,
                description=f"Error loading: {e}",
                auth_type="error",
                cloudflare_enabled=False,
                has_workflow=False,
                has_mappings=False,
            ))

    return {
        "configs": result,
        "count": len(result)
    }


@router.get("/api/config-schemas/template", tags=["config-schemas"])
async def get_config_template():
    """
    Get the template config file content.

    Use this as a starting point for new tracker configs.

    Returns:
        Template YAML content
    """
    try:
        content = read_config_file("_template")
        return {
            "slug": "_template",
            "content": content
        }
    except HTTPException:
        # If template doesn't exist, return a basic template
        basic_template = """# Tracker Configuration
# Copy this file and rename to your_tracker.yaml

tracker:
  name: "My Tracker"
  slug: "mytracker"
  description: "Description of tracker"

auth:
  type: "bearer"
  header: "Authorization"
  prefix: "Bearer "

cloudflare:
  enabled: false

endpoints:
  upload: "/api/torrents/upload"
  categories: "/api/categories"

workflow:
  - name: "upload"
    method: "POST"
    url: "{tracker_url}/api/torrents/upload"
    type: "multipart"
    fields:
      torrent_file:
        source: "torrent_data"
        type: "file"
        filename: "{release_name}.torrent"
        name: "torrent_file"
        required: true
      title:
        source: "release_name"
        type: "string"
        name: "title"
        required: true

response:
  success_field: "success"
  error_field: "error"
  torrent_id_field: "data.id"
"""
        return {
            "slug": "_template",
            "content": basic_template
        }


@router.get("/api/config-schemas/{slug}", tags=["config-schemas"])
async def get_config_schema(slug: str):
    """
    Get a config schema by slug.

    Args:
        slug: Config identifier (filename without .yaml)

    Returns:
        Config schema with content and metadata
    """
    content = read_config_file(slug)
    config = parse_config(content)
    info = get_config_info(slug, config)

    # Validate config
    loader = get_config_loader()
    is_valid, errors = loader.validate(config)

    return ConfigSchemaResponse(
        **info,
        content=content,
        validation_errors=errors if not is_valid else []
    )


@router.put("/api/config-schemas/{slug}", tags=["config-schemas"])
async def update_config_schema(slug: str, data: ConfigSchemaUpdate):
    """
    Update a config schema.

    Args:
        slug: Config identifier
        data: New YAML content

    Returns:
        Updated config schema
    """
    logger.info(f"Updating config schema: {slug}")

    # Parse and validate the new content
    config = parse_config(data.content)

    loader = get_config_loader()
    is_valid, errors = loader.validate(config)

    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid configuration: {'; '.join(errors)}"
        )

    # Write the file
    write_config_file(slug, data.content)

    # Clear cache
    loader.clear_cache()

    # Return updated config
    info = get_config_info(slug, config)

    logger.info(f"Config schema '{slug}' updated successfully")

    return ConfigSchemaResponse(
        **info,
        content=data.content,
        validation_errors=[]
    )


@router.post("/api/config-schemas", tags=["config-schemas"])
async def create_config_schema(data: ConfigSchemaCreate):
    """
    Create a new config schema.

    Args:
        data: Slug and YAML content

    Returns:
        Created config schema
    """
    logger.info(f"Creating config schema: {data.slug}")

    # Check if already exists
    config_path = get_config_path(data.slug)
    if config_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Config '{data.slug}' already exists"
        )

    # Parse and validate
    config = parse_config(data.content)

    loader = get_config_loader()
    is_valid, errors = loader.validate(config)

    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid configuration: {'; '.join(errors)}"
        )

    # Write the file
    write_config_file(data.slug, data.content)

    # Clear cache
    loader.clear_cache()

    # Return created config
    info = get_config_info(data.slug, config)

    logger.info(f"Config schema '{data.slug}' created successfully")

    return ConfigSchemaResponse(
        **info,
        content=data.content,
        validation_errors=[]
    )


@router.delete("/api/config-schemas/{slug}", tags=["config-schemas"])
async def delete_config_schema(slug: str):
    """
    Delete a config schema.

    Args:
        slug: Config identifier

    Returns:
        Success message
    """
    logger.info(f"Deleting config schema: {slug}")

    # Prevent deleting template
    if slug == "_template":
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the template file"
        )

    config_path = get_config_path(slug)
    if not config_path.exists():
        raise HTTPException(status_code=404, detail=f"Config '{slug}' not found")

    # Delete the file
    config_path.unlink()

    # Clear cache
    loader = get_config_loader()
    loader.clear_cache()

    logger.info(f"Config schema '{slug}' deleted successfully")

    return {"message": f"Config '{slug}' deleted successfully"}


@router.post("/api/config-schemas/validate", tags=["config-schemas"])
async def validate_config_schema(data: ConfigSchemaUpdate):
    """
    Validate config content without saving.

    Args:
        data: YAML content to validate

    Returns:
        Validation result with errors/warnings
    """
    errors = []
    warnings = []

    # Parse YAML
    try:
        config = yaml.safe_load(data.content)
        if not config:
            errors.append("Empty configuration")
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
    except yaml.YAMLError as e:
        errors.append(f"Invalid YAML syntax: {e}")
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    # Validate structure
    loader = get_config_loader()
    is_valid, validation_errors = loader.validate(config)

    if not is_valid:
        errors.extend(validation_errors)

    # Check for common issues (warnings)
    tracker = config.get("tracker", {})
    if not tracker.get("name"):
        warnings.append("Missing tracker.name - will use slug as name")

    auth = config.get("auth", {})
    if auth.get("type") == "bearer" and not auth.get("header"):
        warnings.append("Bearer auth without header - will default to 'Authorization'")

    endpoints = config.get("endpoints", {})
    if not endpoints.get("upload"):
        warnings.append("No upload endpoint defined")

    workflow = config.get("workflow", [])
    upload_section = config.get("upload", {})
    if not workflow and not upload_section.get("fields"):
        warnings.append("No workflow or upload.fields defined - uploads may fail")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


@router.post("/api/config-schemas/{slug}/duplicate", tags=["config-schemas"])
async def duplicate_config_schema(slug: str, new_slug: str):
    """
    Duplicate an existing config schema.

    Args:
        slug: Source config identifier
        new_slug: New config identifier

    Returns:
        Created config schema
    """
    logger.info(f"Duplicating config schema: {slug} -> {new_slug}")

    # Read source
    content = read_config_file(slug)

    # Check if target exists
    new_path = get_config_path(new_slug)
    if new_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Config '{new_slug}' already exists"
        )

    # Update slug in content
    config = parse_config(content)
    if "tracker" in config:
        config["tracker"]["slug"] = new_slug
        config["tracker"]["name"] = f"{config['tracker'].get('name', slug)} (Copy)"

    # Convert back to YAML
    new_content = yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Write new file
    write_config_file(new_slug, new_content)

    # Clear cache
    loader = get_config_loader()
    loader.clear_cache()

    # Return created config
    info = get_config_info(new_slug, config)

    logger.info(f"Config schema '{new_slug}' created from '{slug}'")

    return ConfigSchemaResponse(
        **info,
        content=new_content,
        validation_errors=[]
    )


# ============================================================================
# Test Endpoints - For validating tracker configs
# ============================================================================

@router.post("/api/config-schemas/{slug}/test-auth", tags=["config-schemas"])
async def test_auth(slug: str, request: Request, db: Session = Depends(get_db)):
    """
    Test authentication against a tracker using the config schema.

    Requires the tracker to exist in the database with credentials.

    Args:
        slug: Config schema slug

    Returns:
        Authentication test result
    """
    from ..models.tracker import Tracker
    from ..models.settings import Settings
    from ..adapters.tracker_factory import TrackerFactory

    # Find a tracker using this config
    tracker = db.query(Tracker).filter(Tracker.slug == slug).first()
    if not tracker:
        raise HTTPException(
            status_code=404,
            detail=f"No tracker found with slug '{slug}'. Create a tracker first."
        )

    if not tracker.api_key and not tracker.passkey:
        raise HTTPException(
            status_code=400,
            detail="Tracker has no API key or passkey configured"
        )

    settings = Settings.get_settings(db)

    try:
        factory = TrackerFactory(
            db,
            flaresolverr_url=settings.flaresolverr_url if settings else None
        )
        adapter = factory.get_adapter(tracker)

        # Try fetching categories as auth test
        categories = await adapter.get_categories()

        return {
            "status": "success",
            "message": f"Authentication successful for {tracker.name}",
            "categories_count": len(categories) if categories else 0,
            "sample_categories": categories[:5] if categories else []
        }
    except Exception as e:
        logger.error(f"Auth test failed for {slug}: {e}")
        return {
            "status": "error",
            "message": f"Authentication failed: {str(e)}"
        }


@router.post("/api/config-schemas/{slug}/test-search", tags=["config-schemas"])
async def test_search(slug: str, request: Request, db: Session = Depends(get_db)):
    """
    Test search/duplicate check using the config schema.

    Request body (optional):
        {
            "query": "test search",
            "tmdb_id": "12345",
            "imdb_id": "tt1234567"
        }

    Args:
        slug: Config schema slug

    Returns:
        Search test results
    """
    from ..models.tracker import Tracker
    from ..models.settings import Settings
    from ..adapters.tracker_factory import TrackerFactory

    tracker = db.query(Tracker).filter(Tracker.slug == slug).first()
    if not tracker:
        raise HTTPException(
            status_code=404,
            detail=f"No tracker found with slug '{slug}'. Create a tracker first."
        )

    settings = Settings.get_settings(db)

    try:
        body = await request.json()
    except Exception:
        body = {}

    query = body.get("query", "test")
    tmdb_id = body.get("tmdb_id")
    imdb_id = body.get("imdb_id")

    try:
        factory = TrackerFactory(
            db,
            flaresolverr_url=settings.flaresolverr_url if settings else None
        )
        adapter = factory.get_adapter(tracker)

        result = await adapter.check_duplicate(
            release_name=query,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id
        )

        return {
            "status": "success",
            "message": f"Search completed on {tracker.name}",
            "is_duplicate": result.get("is_duplicate", False),
            "results_count": len(result.get("results", [])),
            "sample_results": result.get("results", [])[:5]
        }
    except Exception as e:
        logger.error(f"Search test failed for {slug}: {e}")
        return {
            "status": "error",
            "message": f"Search failed: {str(e)}"
        }


@router.post("/api/config-schemas/{slug}/test-upload", tags=["config-schemas"])
async def test_upload(slug: str, db: Session = Depends(get_db)):
    """
    Dry-run upload validation using the config schema.

    Tests that the config schema can build a valid upload request
    without actually sending it to the tracker.

    Args:
        slug: Config schema slug

    Returns:
        Validation result showing what would be sent
    """
    from ..models.tracker import Tracker
    from ..models.settings import Settings
    from ..adapters.tracker_factory import TrackerFactory

    # Load and validate the config
    loader = get_config_loader()
    try:
        config = loader.load(slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config '{slug}' not found")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Config error: {e}")

    is_valid, errors = loader.validate(config)

    # Check required sections for upload
    warnings = []
    upload_fields = config.get("upload", {}).get("fields", {})
    workflow = config.get("workflow", [])

    if not upload_fields and not workflow:
        errors.append("No upload.fields or workflow defined - upload impossible")

    if workflow:
        for i, step in enumerate(workflow):
            step_name = step.get("name", f"step_{i}")
            fields = step.get("fields", {})
            required_fields = [f for f, c in fields.items() if isinstance(c, dict) and c.get("required")]
            if required_fields:
                warnings.append(f"Step '{step_name}' requires: {', '.join(required_fields)}")

    # Check if tracker exists for credential validation
    tracker = db.query(Tracker).filter(Tracker.slug == slug).first()
    tracker_status = "found" if tracker else "not_found"

    if tracker:
        if not tracker.api_key and not tracker.passkey:
            warnings.append("Tracker has no API key or passkey - uploads will fail")
        if not tracker.tracker_url:
            warnings.append("Tracker has no URL configured")

    # Check validation section
    validation = config.get("validation", {})
    validated_fields = list(validation.keys()) if validation else []

    # Check rate limiting
    rate_limiting = config.get("rate_limiting", {})

    # Check sanitization
    sanitize = config.get("sanitize", {})

    # Check prowlarr integration
    prowlarr = config.get("prowlarr", {})

    return {
        "status": "valid" if not errors else "invalid",
        "config_slug": slug,
        "errors": errors,
        "warnings": warnings,
        "tracker_in_db": tracker_status,
        "upload_method": "workflow" if workflow else "legacy_fields",
        "workflow_steps": len(workflow),
        "upload_fields_count": len(upload_fields),
        "mappings": list(config.get("mappings", {}).keys()),
        "dynamic_sources": list(config.get("dynamic_sources", {}).keys()),
        "validated_fields": validated_fields,
        "has_rate_limiting": bool(rate_limiting),
        "has_sanitization": bool(sanitize),
        "has_prowlarr_config": bool(prowlarr),
        "prowlarr_definitions": prowlarr.get("definitions", []),
    }
