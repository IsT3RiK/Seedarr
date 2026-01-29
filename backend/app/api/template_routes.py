"""
Template API Routes for Seedarr v2.0

This module provides API endpoints for managing all template types:
- BBCode templates (presentation/description)
- Naming templates (release name formatting)
- NFO templates (technical information files)
"""

import json
import logging
import os
from typing import Optional, List
from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.database import get_db
from app.models import BBCodeTemplate, NamingTemplate, NFOTemplate
from app.services.bbcode_generator import get_bbcode_generator

logger = logging.getLogger(__name__)

router = APIRouter()
# Use relative path when running from backend directory
templates_dir = "templates" if os.path.exists("templates") else "backend/templates"
templates = Jinja2Templates(directory=templates_dir)


# ============== Pydantic Models ==============

# BBCode Template Models
class BBCodeTemplateCreate(BaseModel):
    """Request model for creating a BBCode template."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    content: str = Field(..., min_length=1)


class BBCodeTemplateUpdate(BaseModel):
    """Request model for updating a BBCode template."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    content: Optional[str] = Field(None, min_length=1)


# Naming Template Models
class NamingTemplateCreate(BaseModel):
    """Request model for creating a naming template."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    template: str = Field(..., min_length=1)


class NamingTemplateUpdate(BaseModel):
    """Request model for updating a naming template."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    template: Optional[str] = Field(None, min_length=1)


# NFO Template Models
class NFOTemplateCreate(BaseModel):
    """Request model for creating an NFO template."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    content: str = Field(..., min_length=1)


class NFOTemplateUpdate(BaseModel):
    """Request model for updating an NFO template."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    content: Optional[str] = Field(None, min_length=1)


# Legacy models for backward compatibility
class TemplateCreate(BBCodeTemplateCreate):
    """Legacy: use BBCodeTemplateCreate instead."""
    pass


class TemplateUpdate(BBCodeTemplateUpdate):
    """Legacy: use BBCodeTemplateUpdate instead."""
    pass


class TemplatePreviewRequest(BaseModel):
    """Request model for previewing a template."""
    content: str = Field(..., min_length=1)


# Export/Import Models
class TemplateExportData(BaseModel):
    """Single template data for export."""
    name: str
    description: Optional[str] = None
    content: Optional[str] = None  # Used for BBCode and NFO
    template: Optional[str] = None  # Used for Naming


class TemplateExport(BaseModel):
    """Export format for templates."""
    type: str  # "bbcode", "naming", "nfo"
    version: str = "1.0"
    template: Optional[TemplateExportData] = None  # Single template
    templates: Optional[List[TemplateExportData]] = None  # Multiple templates


# ============== Page Routes ==============

@router.get("/templates", response_class=HTMLResponse)
async def unified_templates_page(request: Request, db: Session = Depends(get_db)):
    """Render the unified templates management page with all template types."""
    bbcode_templates = BBCodeTemplate.get_all(db)
    naming_templates = NamingTemplate.get_all(db)
    nfo_templates = NFOTemplate.get_all(db)

    bbcode_variables = BBCodeTemplate.get_available_variables()
    naming_variables = NamingTemplate.get_available_variables()
    nfo_variables = NFOTemplate.get_available_variables()

    naming_examples = NamingTemplate.get_example_templates()

    return templates.TemplateResponse(
        "templates.html",
        {
            "request": request,
            "bbcode_templates": bbcode_templates,
            "naming_templates": naming_templates,
            "nfo_templates": nfo_templates,
            "bbcode_variables": bbcode_variables,
            "naming_variables": naming_variables,
            "nfo_variables": nfo_variables,
            "naming_examples": naming_examples,
        }
    )


@router.get("/templates/bbcode", response_class=HTMLResponse)
async def bbcode_templates_page(request: Request, db: Session = Depends(get_db)):
    """Render the BBCode templates management page (legacy route)."""
    all_templates = BBCodeTemplate.get_all(db)
    variables = BBCodeTemplate.get_available_variables()

    return templates.TemplateResponse(
        "bbcode_templates.html",
        {
            "request": request,
            "templates": all_templates,
            "variables": variables,
        }
    )


# ============== API Routes ==============

@router.get("/api/templates")
async def list_templates(db: Session = Depends(get_db)):
    """Get all BBCode templates."""
    logger.debug(f"Fetching all BBCode templates from database")
    all_templates = BBCodeTemplate.get_all(db)
    logger.debug(f"Found {len(all_templates)} templates")
    for t in all_templates:
        logger.debug(f"  Template: {t.id} - {t.name}")
    return {
        "status": "success",
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "is_default": t.is_default,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in all_templates
        ]
    }


@router.get("/api/templates/variables")
async def get_template_variables():
    """Get the list of available template variables."""
    return {
        "status": "success",
        "variables": BBCodeTemplate.get_available_variables()
    }


@router.get("/api/templates/default")
async def get_default_template(db: Session = Depends(get_db)):
    """Get the default template."""
    template = BBCodeTemplate.get_default(db)
    if not template:
        return {
            "status": "error",
            "message": "No default template configured"
        }

    return {
        "status": "success",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "content": template.content,
            "is_default": template.is_default,
        }
    }


@router.get("/api/templates/{template_id}")
async def get_template(template_id: int, db: Session = Depends(get_db)):
    """Get a specific template by ID."""
    template = BBCodeTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "status": "success",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "content": template.content,
            "is_default": template.is_default,
            "created_at": template.created_at.isoformat() if template.created_at else None,
            "updated_at": template.updated_at.isoformat() if template.updated_at else None,
        }
    }


@router.post("/api/templates")
async def create_template(data: TemplateCreate, db: Session = Depends(get_db)):
    """Create a new BBCode template."""
    # Check if name already exists
    existing = BBCodeTemplate.get_by_name(db, data.name)
    if existing:
        raise HTTPException(status_code=400, detail="A template with this name already exists")

    template = BBCodeTemplate(
        name=data.name,
        description=data.description,
        content=data.content,
        is_default=False,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    logger.info(f"Created BBCode template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": "Template created successfully",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "is_default": template.is_default,
        }
    }


@router.put("/api/templates/{template_id}")
async def update_template(
    template_id: int,
    data: TemplateUpdate,
    db: Session = Depends(get_db)
):
    """Update an existing BBCode template."""
    template = BBCodeTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Check if new name conflicts with existing
    if data.name and data.name != template.name:
        existing = BBCodeTemplate.get_by_name(db, data.name)
        if existing:
            raise HTTPException(status_code=400, detail="A template with this name already exists")
        template.name = data.name

    if data.description is not None:
        template.description = data.description

    if data.content is not None:
        template.content = data.content

    db.commit()
    db.refresh(template)

    logger.info(f"Updated BBCode template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": "Template updated successfully",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "content": template.content,
            "is_default": template.is_default,
        }
    }


@router.delete("/api/templates/{template_id}")
async def delete_template(template_id: int, db: Session = Depends(get_db)):
    """Delete a BBCode template."""
    template = BBCodeTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if template.is_default:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the default template. Set another template as default first."
        )

    template_name = template.name
    db.delete(template)
    db.commit()

    logger.info(f"Deleted BBCode template: {template_name} (ID: {template_id})")

    return {
        "status": "success",
        "message": f"Template '{template_name}' deleted successfully"
    }


@router.post("/api/templates/{template_id}/set-default")
async def set_default_template(template_id: int, db: Session = Depends(get_db)):
    """Set a template as the default."""
    template = BBCodeTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    template.set_as_default(db)

    logger.info(f"Set default BBCode template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": f"Template '{template.name}' is now the default"
    }


@router.post("/api/templates/preview")
async def preview_template(data: TemplatePreviewRequest):
    """Preview a template with sample data."""
    generator = get_bbcode_generator()
    rendered = generator.preview_template(data.content)

    return {
        "status": "success",
        "bbcode": rendered,
    }


# ============================================================================
# BBCode Templates API (new paths with /bbcode/)
# ============================================================================

@router.get("/api/templates/bbcode/{template_id}")
async def get_bbcode_template(template_id: int, db: Session = Depends(get_db)):
    """Get a specific BBCode template by ID."""
    template = BBCodeTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "status": "success",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "content": template.content,
            "is_default": template.is_default,
            "created_at": template.created_at.isoformat() if template.created_at else None,
            "updated_at": template.updated_at.isoformat() if template.updated_at else None,
        }
    }


@router.post("/api/templates/bbcode")
async def create_bbcode_template(data: BBCodeTemplateCreate, db: Session = Depends(get_db)):
    """Create a new BBCode template."""
    existing = BBCodeTemplate.get_by_name(db, data.name)
    if existing:
        raise HTTPException(status_code=400, detail="A template with this name already exists")

    template = BBCodeTemplate(
        name=data.name,
        description=data.description,
        content=data.content,
        is_default=False,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    logger.info(f"Created BBCode template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": "Template created successfully",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "is_default": template.is_default,
        }
    }


@router.put("/api/templates/bbcode/{template_id}")
async def update_bbcode_template(
    template_id: int,
    data: BBCodeTemplateUpdate,
    db: Session = Depends(get_db)
):
    """Update an existing BBCode template."""
    template = BBCodeTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if data.name and data.name != template.name:
        existing = BBCodeTemplate.get_by_name(db, data.name)
        if existing:
            raise HTTPException(status_code=400, detail="A template with this name already exists")
        template.name = data.name

    if data.description is not None:
        template.description = data.description

    if data.content is not None:
        template.content = data.content

    db.commit()
    db.refresh(template)

    logger.info(f"Updated BBCode template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": "Template updated successfully",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "content": template.content,
            "is_default": template.is_default,
        }
    }


@router.delete("/api/templates/bbcode/{template_id}")
async def delete_bbcode_template(template_id: int, db: Session = Depends(get_db)):
    """Delete a BBCode template."""
    template = BBCodeTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if template.is_default:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the default template. Set another template as default first."
        )

    template_name = template.name
    db.delete(template)
    db.commit()

    logger.info(f"Deleted BBCode template: {template_name} (ID: {template_id})")

    return {
        "status": "success",
        "message": f"Template '{template_name}' deleted successfully"
    }


@router.post("/api/templates/bbcode/{template_id}/set-default")
async def set_default_bbcode_template(template_id: int, db: Session = Depends(get_db)):
    """Set a BBCode template as the default."""
    template = BBCodeTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    template.set_as_default(db)

    logger.info(f"Set default BBCode template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": f"Template '{template.name}' is now the default"
    }


# ============================================================================
# Naming Templates API
# ============================================================================

@router.get("/api/templates/naming")
async def list_naming_templates(db: Session = Depends(get_db)):
    """Get all naming templates."""
    all_templates = NamingTemplate.get_all(db)
    return {
        "status": "success",
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "template": t.template,
                "is_default": t.is_default,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in all_templates
        ]
    }


@router.get("/api/templates/naming/{template_id}")
async def get_naming_template(template_id: int, db: Session = Depends(get_db)):
    """Get a specific naming template by ID."""
    template = NamingTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "status": "success",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "template": template.template,
            "is_default": template.is_default,
            "created_at": template.created_at.isoformat() if template.created_at else None,
            "updated_at": template.updated_at.isoformat() if template.updated_at else None,
        }
    }


@router.post("/api/templates/naming")
async def create_naming_template(data: NamingTemplateCreate, db: Session = Depends(get_db)):
    """Create a new naming template."""
    existing = NamingTemplate.get_by_name(db, data.name)
    if existing:
        raise HTTPException(status_code=400, detail="A template with this name already exists")

    template = NamingTemplate(
        name=data.name,
        description=data.description,
        template=data.template,
        is_default=False,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    logger.info(f"Created naming template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": "Template created successfully",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "template": template.template,
            "is_default": template.is_default,
        }
    }


@router.put("/api/templates/naming/{template_id}")
async def update_naming_template(
    template_id: int,
    data: NamingTemplateUpdate,
    db: Session = Depends(get_db)
):
    """Update an existing naming template."""
    template = NamingTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if data.name and data.name != template.name:
        existing = NamingTemplate.get_by_name(db, data.name)
        if existing:
            raise HTTPException(status_code=400, detail="A template with this name already exists")
        template.name = data.name

    if data.description is not None:
        template.description = data.description

    if data.template is not None:
        template.template = data.template

    db.commit()
    db.refresh(template)

    logger.info(f"Updated naming template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": "Template updated successfully",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "template": template.template,
            "is_default": template.is_default,
        }
    }


@router.delete("/api/templates/naming/{template_id}")
async def delete_naming_template(template_id: int, db: Session = Depends(get_db)):
    """Delete a naming template."""
    template = NamingTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if template.is_default:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the default template. Set another template as default first."
        )

    template_name = template.name
    db.delete(template)
    db.commit()

    logger.info(f"Deleted naming template: {template_name} (ID: {template_id})")

    return {
        "status": "success",
        "message": f"Template '{template_name}' deleted successfully"
    }


@router.post("/api/templates/naming/{template_id}/set-default")
async def set_default_naming_template(template_id: int, db: Session = Depends(get_db)):
    """Set a naming template as the default."""
    template = NamingTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    template.set_as_default(db)

    logger.info(f"Set default naming template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": f"Template '{template.name}' is now the default"
    }


# ============================================================================
# NFO Templates API
# ============================================================================

@router.get("/api/templates/nfo")
async def list_nfo_templates(db: Session = Depends(get_db)):
    """Get all NFO templates."""
    all_templates = NFOTemplate.get_all(db)
    return {
        "status": "success",
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "content": t.content,
                "is_default": t.is_default,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in all_templates
        ]
    }


@router.get("/api/templates/nfo/{template_id}")
async def get_nfo_template(template_id: int, db: Session = Depends(get_db)):
    """Get a specific NFO template by ID."""
    template = NFOTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "status": "success",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "content": template.content,
            "is_default": template.is_default,
            "created_at": template.created_at.isoformat() if template.created_at else None,
            "updated_at": template.updated_at.isoformat() if template.updated_at else None,
        }
    }


@router.post("/api/templates/nfo")
async def create_nfo_template(data: NFOTemplateCreate, db: Session = Depends(get_db)):
    """Create a new NFO template."""
    existing = NFOTemplate.get_by_name(db, data.name)
    if existing:
        raise HTTPException(status_code=400, detail="A template with this name already exists")

    template = NFOTemplate(
        name=data.name,
        description=data.description,
        content=data.content,
        is_default=False,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    logger.info(f"Created NFO template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": "Template created successfully",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "is_default": template.is_default,
        }
    }


@router.put("/api/templates/nfo/{template_id}")
async def update_nfo_template(
    template_id: int,
    data: NFOTemplateUpdate,
    db: Session = Depends(get_db)
):
    """Update an existing NFO template."""
    template = NFOTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if data.name and data.name != template.name:
        existing = NFOTemplate.get_by_name(db, data.name)
        if existing:
            raise HTTPException(status_code=400, detail="A template with this name already exists")
        template.name = data.name

    if data.description is not None:
        template.description = data.description

    if data.content is not None:
        template.content = data.content

    db.commit()
    db.refresh(template)

    logger.info(f"Updated NFO template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": "Template updated successfully",
        "template": {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "content": template.content,
            "is_default": template.is_default,
        }
    }


@router.delete("/api/templates/nfo/{template_id}")
async def delete_nfo_template(template_id: int, db: Session = Depends(get_db)):
    """Delete an NFO template."""
    template = NFOTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if template.is_default:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the default template. Set another template as default first."
        )

    template_name = template.name
    db.delete(template)
    db.commit()

    logger.info(f"Deleted NFO template: {template_name} (ID: {template_id})")

    return {
        "status": "success",
        "message": f"Template '{template_name}' deleted successfully"
    }


@router.post("/api/templates/nfo/{template_id}/set-default")
async def set_default_nfo_template(template_id: int, db: Session = Depends(get_db)):
    """Set an NFO template as the default."""
    template = NFOTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    template.set_as_default(db)

    logger.info(f"Set default NFO template: {template.name} (ID: {template.id})")

    return {
        "status": "success",
        "message": f"Template '{template.name}' is now the default"
    }


# ============================================================================
# Template Export/Import API
# ============================================================================

@router.get("/api/templates/bbcode/{template_id}/export")
async def export_bbcode_template(template_id: int, db: Session = Depends(get_db)):
    """Export a single BBCode template as JSON."""
    template = BBCodeTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    export_data = {
        "type": "bbcode",
        "version": "1.0",
        "template": {
            "name": template.name,
            "description": template.description,
            "content": template.content,
        }
    }

    # Sanitize filename
    safe_name = "".join(c for c in template.name if c.isalnum() or c in (' ', '-', '_')).strip()
    safe_name = safe_name.replace(' ', '_')

    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f'attachment; filename="bbcode_{safe_name}.json"'
        }
    )


@router.get("/api/templates/naming/{template_id}/export")
async def export_naming_template(template_id: int, db: Session = Depends(get_db)):
    """Export a single Naming template as JSON."""
    template = NamingTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    export_data = {
        "type": "naming",
        "version": "1.0",
        "template": {
            "name": template.name,
            "description": template.description,
            "template": template.template,
        }
    }

    safe_name = "".join(c for c in template.name if c.isalnum() or c in (' ', '-', '_')).strip()
    safe_name = safe_name.replace(' ', '_')

    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f'attachment; filename="naming_{safe_name}.json"'
        }
    )


@router.get("/api/templates/nfo/{template_id}/export")
async def export_nfo_template(template_id: int, db: Session = Depends(get_db)):
    """Export a single NFO template as JSON."""
    template = NFOTemplate.get_by_id(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    export_data = {
        "type": "nfo",
        "version": "1.0",
        "template": {
            "name": template.name,
            "description": template.description,
            "content": template.content,
        }
    }

    safe_name = "".join(c for c in template.name if c.isalnum() or c in (' ', '-', '_')).strip()
    safe_name = safe_name.replace(' ', '_')

    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f'attachment; filename="nfo_{safe_name}.json"'
        }
    )


@router.get("/api/templates/export-all/{template_type}")
async def export_all_templates(template_type: str, db: Session = Depends(get_db)):
    """Export all templates of a given type as JSON."""
    if template_type not in ["bbcode", "naming", "nfo"]:
        raise HTTPException(status_code=400, detail="Invalid template type. Must be 'bbcode', 'naming', or 'nfo'")

    templates_list = []

    if template_type == "bbcode":
        all_templates = BBCodeTemplate.get_all(db)
        for t in all_templates:
            templates_list.append({
                "name": t.name,
                "description": t.description,
                "content": t.content,
            })
    elif template_type == "naming":
        all_templates = NamingTemplate.get_all(db)
        for t in all_templates:
            templates_list.append({
                "name": t.name,
                "description": t.description,
                "template": t.template,
            })
    elif template_type == "nfo":
        all_templates = NFOTemplate.get_all(db)
        for t in all_templates:
            templates_list.append({
                "name": t.name,
                "description": t.description,
                "content": t.content,
            })

    export_data = {
        "type": template_type,
        "version": "1.0",
        "templates": templates_list
    }

    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f'attachment; filename="{template_type}_templates_all.json"'
        }
    )


@router.post("/api/templates/bbcode/import")
async def import_bbcode_templates(
    file: UploadFile = File(...),
    conflict_mode: str = Form("rename"),
    db: Session = Depends(get_db)
):
    """Import BBCode template(s) from JSON file."""
    if conflict_mode not in ["rename", "skip", "overwrite"]:
        raise HTTPException(status_code=400, detail="Invalid conflict_mode. Must be 'rename', 'skip', or 'overwrite'")

    try:
        content = await file.read()
        data = json.loads(content.decode('utf-8'))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading file: {str(e)}")

    # Validate format
    if data.get("type") != "bbcode":
        raise HTTPException(status_code=400, detail="Invalid template type. Expected 'bbcode'")

    # Get templates to import (single or multiple)
    templates_to_import = []
    if data.get("template"):
        templates_to_import = [data["template"]]
    elif data.get("templates"):
        templates_to_import = data["templates"]
    else:
        raise HTTPException(status_code=400, detail="No template data found in file")

    imported = []
    skipped = []

    for tpl in templates_to_import:
        if not tpl.get("name") or not tpl.get("content"):
            skipped.append({"name": tpl.get("name", "unknown"), "reason": "Missing name or content"})
            continue

        name = tpl["name"]
        existing = BBCodeTemplate.get_by_name(db, name)

        if existing:
            if conflict_mode == "skip":
                skipped.append({"name": name, "reason": "Already exists"})
                continue
            elif conflict_mode == "rename":
                # Find a unique name
                counter = 1
                new_name = f"{name} (imported)"
                while BBCodeTemplate.get_by_name(db, new_name):
                    counter += 1
                    new_name = f"{name} (imported {counter})"
                name = new_name
            elif conflict_mode == "overwrite":
                db.delete(existing)
                db.flush()

        new_template = BBCodeTemplate(
            name=name,
            description=tpl.get("description"),
            content=tpl["content"],
            is_default=False,
        )
        db.add(new_template)
        imported.append(name)

    db.commit()

    logger.info(f"Imported {len(imported)} BBCode template(s): {imported}")

    return {
        "status": "success",
        "imported": imported,
        "skipped": skipped,
        "message": f"Imported {len(imported)} template(s)"
    }


@router.post("/api/templates/naming/import")
async def import_naming_templates(
    file: UploadFile = File(...),
    conflict_mode: str = Form("rename"),
    db: Session = Depends(get_db)
):
    """Import Naming template(s) from JSON file."""
    if conflict_mode not in ["rename", "skip", "overwrite"]:
        raise HTTPException(status_code=400, detail="Invalid conflict_mode. Must be 'rename', 'skip', or 'overwrite'")

    try:
        content = await file.read()
        data = json.loads(content.decode('utf-8'))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading file: {str(e)}")

    # Validate format
    if data.get("type") != "naming":
        raise HTTPException(status_code=400, detail="Invalid template type. Expected 'naming'")

    # Get templates to import
    templates_to_import = []
    if data.get("template"):
        templates_to_import = [data["template"]]
    elif data.get("templates"):
        templates_to_import = data["templates"]
    else:
        raise HTTPException(status_code=400, detail="No template data found in file")

    imported = []
    skipped = []

    for tpl in templates_to_import:
        if not tpl.get("name") or not tpl.get("template"):
            skipped.append({"name": tpl.get("name", "unknown"), "reason": "Missing name or template"})
            continue

        name = tpl["name"]
        existing = NamingTemplate.get_by_name(db, name)

        if existing:
            if conflict_mode == "skip":
                skipped.append({"name": name, "reason": "Already exists"})
                continue
            elif conflict_mode == "rename":
                counter = 1
                new_name = f"{name} (imported)"
                while NamingTemplate.get_by_name(db, new_name):
                    counter += 1
                    new_name = f"{name} (imported {counter})"
                name = new_name
            elif conflict_mode == "overwrite":
                db.delete(existing)
                db.flush()

        new_template = NamingTemplate(
            name=name,
            description=tpl.get("description"),
            template=tpl["template"],
            is_default=False,
        )
        db.add(new_template)
        imported.append(name)

    db.commit()

    logger.info(f"Imported {len(imported)} Naming template(s): {imported}")

    return {
        "status": "success",
        "imported": imported,
        "skipped": skipped,
        "message": f"Imported {len(imported)} template(s)"
    }


@router.post("/api/templates/nfo/import")
async def import_nfo_templates(
    file: UploadFile = File(...),
    conflict_mode: str = Form("rename"),
    db: Session = Depends(get_db)
):
    """Import NFO template(s) from JSON file."""
    if conflict_mode not in ["rename", "skip", "overwrite"]:
        raise HTTPException(status_code=400, detail="Invalid conflict_mode. Must be 'rename', 'skip', or 'overwrite'")

    try:
        content = await file.read()
        data = json.loads(content.decode('utf-8'))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading file: {str(e)}")

    # Validate format
    if data.get("type") != "nfo":
        raise HTTPException(status_code=400, detail="Invalid template type. Expected 'nfo'")

    # Get templates to import
    templates_to_import = []
    if data.get("template"):
        templates_to_import = [data["template"]]
    elif data.get("templates"):
        templates_to_import = data["templates"]
    else:
        raise HTTPException(status_code=400, detail="No template data found in file")

    imported = []
    skipped = []

    for tpl in templates_to_import:
        if not tpl.get("name") or not tpl.get("content"):
            skipped.append({"name": tpl.get("name", "unknown"), "reason": "Missing name or content"})
            continue

        name = tpl["name"]
        existing = NFOTemplate.get_by_name(db, name)

        if existing:
            if conflict_mode == "skip":
                skipped.append({"name": name, "reason": "Already exists"})
                continue
            elif conflict_mode == "rename":
                counter = 1
                new_name = f"{name} (imported)"
                while NFOTemplate.get_by_name(db, new_name):
                    counter += 1
                    new_name = f"{name} (imported {counter})"
                name = new_name
            elif conflict_mode == "overwrite":
                db.delete(existing)
                db.flush()

        new_template = NFOTemplate(
            name=name,
            description=tpl.get("description"),
            content=tpl["content"],
            is_default=False,
        )
        db.add(new_template)
        imported.append(name)

    db.commit()

    logger.info(f"Imported {len(imported)} NFO template(s): {imported}")

    return {
        "status": "success",
        "imported": imported,
        "skipped": skipped,
        "message": f"Imported {len(imported)} template(s)"
    }
