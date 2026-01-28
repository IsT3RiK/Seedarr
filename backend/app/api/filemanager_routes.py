"""
File Manager API Routes for Seedarr v2.0

This module provides a file browser interface to navigate directories
and trigger scans on media files.

Features:
    - GET /filemanager: File manager UI page
    - GET /api/filemanager/browse: List directory contents (HTMX)
    - POST /api/filemanager/scan: Trigger scan on file/folder
"""

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
from pathlib import Path
import os
import logging

from app.models.settings import Settings
from app.models.file_entry import FileEntry
from app.database import get_db

logger = logging.getLogger(__name__)


def sanitize_path(path: Optional[str]) -> Optional[str]:
    """
    Sanitize file path by removing invisible Unicode characters.

    Common invisible characters that can cause issues:
    - U+200E (Left-to-Right Mark)
    - U+200F (Right-to-Left Mark)
    - U+200B (Zero Width Space)
    - U+FEFF (BOM)
    - U+202A-U+202E (Directional formatting)
    """
    if not path:
        return path

    # List of invisible/problematic Unicode characters to remove
    invisible_chars = [
        '\u200e', '\u200f',  # LRM, RLM
        '\u200b', '\u200c', '\u200d',  # Zero-width chars
        '\ufeff',  # BOM
        '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',  # Directional
        '\u2066', '\u2067', '\u2068', '\u2069',  # Isolates
    ]

    result = path
    for char in invisible_chars:
        result = result.replace(char, '')

    return result.strip()

router = APIRouter()
# Auto-detect templates path based on working directory
templates_dir = "templates" if os.path.exists("templates") else "backend/templates"
templates = Jinja2Templates(directory=templates_dir)

# File type categories
VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'}
TORRENT_EXTENSIONS = {'.torrent'}
METADATA_EXTENSIONS = {'.nfo', '.txt'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}


def get_file_type(filename: str) -> str:
    """Determine file type category from extension."""
    ext = Path(filename).suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return 'video'
    elif ext in TORRENT_EXTENSIONS:
        return 'torrent'
    elif ext in METADATA_EXTENSIONS:
        return 'metadata'
    elif ext in IMAGE_EXTENSIONS:
        return 'image'
    else:
        return 'other'


def format_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def is_path_allowed(path: str, settings: Settings) -> bool:
    """
    Check if path is within allowed directories.
    Prevents path traversal attacks.
    """
    try:
        resolved_path = Path(path).resolve()

        # Check against input_media_path
        if settings.input_media_path:
            input_path = Path(settings.input_media_path).resolve()
            if str(resolved_path).startswith(str(input_path)):
                return True

        # Check against output_dir
        if settings.output_dir:
            output_path = Path(settings.output_dir).resolve()
            if str(resolved_path).startswith(str(output_path)):
                return True

        return False
    except Exception:
        return False


PAGE_SIZE = 20

import time as _time

# In-memory cache: {path: (items, monotonic_timestamp)}
_dir_cache: dict = {}
_DIR_CACHE_TTL = 60  # seconds


def _get_cached_directory(path: str) -> List[dict]:
    """Return cached directory listing, or scan and cache it.

    Uses os.scandir() for performance on network drives:
    on Windows, DirEntry.is_dir() and DirEntry.stat() use data
    already fetched by FindFirstFile/FindNextFile, so no extra
    network round-trip per file.
    """
    now = _time.monotonic()
    cached = _dir_cache.get(path)
    if cached and (now - cached[1]) < _DIR_CACHE_TTL:
        return cached[0]

    all_items = []
    try:
        if not os.path.isdir(path):
            return []

        # Collect DirEntry objects first (fast on Windows/network: no extra syscalls)
        entries = []
        try:
            with os.scandir(path) as scanner:
                for entry in scanner:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        entries.append((entry, is_dir))
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError) as e:
            logger.warning(f"Cannot scan {path}: {e}")
            return []

        # Sort: directories first, then by name (case-insensitive)
        entries.sort(key=lambda x: (not x[1], x[0].name.lower()))

        # Build item dicts - stat() is cached by DirEntry on Windows
        for entry, is_dir in entries:
            try:
                st = entry.stat(follow_symlinks=False)
                item = {
                    'name': entry.name,
                    'path': entry.path,
                    'is_dir': is_dir,
                    'size': st.st_size if not is_dir else 0,
                    'size_formatted': format_size(st.st_size) if not is_dir else '-',
                    'modified': datetime.fromtimestamp(st.st_mtime),
                    'modified_formatted': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M'),
                    'type': 'folder' if is_dir else get_file_type(entry.name),
                    'extension': os.path.splitext(entry.name)[1].lower() if not is_dir else ''
                }
                all_items.append(item)
            except (PermissionError, OSError) as e:
                logger.warning(f"Cannot access {entry.path}: {e}")
                continue
    except Exception as e:
        logger.error(f"Error reading directory {path}: {e}")

    # Keep cache small: max 5 directories
    if len(_dir_cache) >= 5:
        oldest_key = min(_dir_cache, key=lambda k: _dir_cache[k][1])
        del _dir_cache[oldest_key]

    _dir_cache[path] = (all_items, now)
    return all_items


def get_directory_contents(path: str, offset: int = 0, limit: int = 0) -> tuple:
    """
    Get contents of a directory with optional pagination.
    Returns (items, total_count).
    If limit is 0, returns all items.
    """
    all_items = _get_cached_directory(path)
    total = len(all_items)
    if limit > 0:
        return all_items[offset:offset + limit], total
    return all_items, total


def get_breadcrumbs(path: str, base_path: str) -> List[dict]:
    """
    Generate breadcrumb navigation from path.
    """
    breadcrumbs = []
    current = Path(path).resolve()
    base = Path(base_path).resolve()

    # Build path components
    parts = []
    temp = current
    while temp != base and temp != temp.parent:
        parts.append({'name': temp.name, 'path': str(temp)})
        temp = temp.parent

    # Add base path
    parts.append({'name': base.name or 'Root', 'path': str(base)})

    # Reverse to get correct order
    breadcrumbs = list(reversed(parts))

    return breadcrumbs


@router.get("/filemanager", response_class=HTMLResponse)
async def filemanager_page(
    request: Request,
    path: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Render the file manager page.
    """
    try:
        settings = Settings.get_settings(db)

        # Use input_media_path from settings (sanitize to remove invisible chars)
        base_path = sanitize_path(settings.input_media_path)

        # Check if path is configured
        if not base_path:
            return templates.TemplateResponse(
                "filemanager.html",
                {
                    "request": request,
                    "error": "Input Media Path not configured. Please set it in Settings.",
                    "items": [],
                    "breadcrumbs": [],
                    "current_path": "",
                    "base_path": ""
                }
            )

        # Check if path exists
        if not Path(base_path).exists():
            return templates.TemplateResponse(
                "filemanager.html",
                {
                    "request": request,
                    "error": f"Directory not found: {base_path}. Please check the path in Settings.",
                    "items": [],
                    "breadcrumbs": [],
                    "current_path": "",
                    "base_path": ""
                }
            )

        # Use provided path or default to base
        current_path = path if path else base_path

        # Security check
        if not is_path_allowed(current_path, settings):
            current_path = base_path

        # Verify path exists
        if not Path(current_path).exists():
            return templates.TemplateResponse(
                "filemanager.html",
                {
                    "request": request,
                    "error": f"Directory not found: {current_path}",
                    "items": [],
                    "breadcrumbs": [],
                    "current_path": current_path,
                    "base_path": base_path
                }
            )

        # Get directory contents (paginated)
        items, total_count = get_directory_contents(current_path, offset=0, limit=PAGE_SIZE)
        breadcrumbs = get_breadcrumbs(current_path, base_path)

        # Get parent path
        parent_path = str(Path(current_path).parent)
        if not is_path_allowed(parent_path, settings):
            parent_path = None

        return templates.TemplateResponse(
            "filemanager.html",
            {
                "request": request,
                "items": items,
                "breadcrumbs": breadcrumbs,
                "current_path": current_path,
                "base_path": base_path,
                "parent_path": parent_path,
                "total_count": total_count,
                "has_more": len(items) < total_count,
                "next_offset": PAGE_SIZE,
                "error": None
            }
        )
    except Exception as e:
        logger.error(f"Error rendering file manager: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/filemanager/browse", response_class=HTMLResponse)
async def browse_directory(
    request: Request,
    path: str = Query(..., description="Directory path to browse"),
    db: Session = Depends(get_db)
):
    """
    Browse a directory and return HTML fragment for HTMX.
    """
    try:
        settings = Settings.get_settings(db)
        base_path = sanitize_path(settings.input_media_path)

        if not base_path:
            return """
            <div class="alert alert-error">
                <span>Input Media Path not configured. Please set it in Settings.</span>
            </div>
            """

        # Security check
        if not is_path_allowed(path, settings):
            return """
            <div class="alert alert-error">
                <span>Access denied: Path is outside allowed directories.</span>
            </div>
            """

        # Verify path exists
        if not Path(path).exists():
            return f"""
            <div class="alert alert-error">
                <span>Directory not found: {path}</span>
            </div>
            """

        items, total_count = get_directory_contents(path, offset=0, limit=PAGE_SIZE)
        breadcrumbs = get_breadcrumbs(path, base_path)

        parent_path = str(Path(path).parent)
        if not is_path_allowed(parent_path, settings):
            parent_path = None

        return templates.TemplateResponse(
            "components/filemanager_content.html",
            {
                "request": request,
                "items": items,
                "breadcrumbs": breadcrumbs,
                "current_path": path,
                "base_path": base_path,
                "parent_path": parent_path,
                "total_count": total_count,
                "has_more": len(items) < total_count,
                "next_offset": PAGE_SIZE,
            }
        )
    except Exception as e:
        logger.error(f"Error browsing directory: {e}")
        return f"""
        <div class="alert alert-error">
            <span>Error: {str(e)}</span>
        </div>
        """


@router.get("/api/filemanager/search")
async def search_items(
    request: Request,
    path: str = Query(..., description="Directory path"),
    q: str = Query(..., description="Search query"),
    db: Session = Depends(get_db)
):
    """Return matching items as JSON for client-side search."""
    settings = Settings.get_settings(db)
    if not is_path_allowed(path, settings):
        return []

    all_items = _get_cached_directory(path)
    query = q.lower()
    results = [
        {"name": item["name"], "path": item["path"], "is_dir": item["is_dir"],
         "type": item["type"], "size_formatted": item["size_formatted"],
         "modified_formatted": item["modified_formatted"], "extension": item["extension"]}
        for item in all_items
        if query in item["name"].lower()
    ]
    return results


@router.get("/api/filemanager/load-more", response_class=HTMLResponse)
async def load_more_items(
    request: Request,
    path: str = Query(..., description="Directory path"),
    offset: int = Query(..., description="Offset to start from"),
    db: Session = Depends(get_db)
):
    """
    Load more items for infinite scroll. Returns table rows HTML fragment.
    """
    try:
        settings = Settings.get_settings(db)

        if not is_path_allowed(path, settings):
            return ""

        items, total_count = get_directory_contents(path, offset=offset, limit=PAGE_SIZE)
        has_more = (offset + len(items)) < total_count
        next_offset = offset + PAGE_SIZE

        return templates.TemplateResponse(
            "components/filemanager_rows.html",
            {
                "request": request,
                "items": items,
                "current_path": path,
                "has_more": has_more,
                "next_offset": next_offset,
            }
        )
    except Exception as e:
        logger.error(f"Error loading more items: {e}")
        return ""


@router.post("/api/filemanager/scan")
async def scan_path(
    request: Request,
    path: str = Query(..., description="File or folder path to scan"),
    db: Session = Depends(get_db)
):
    """
    Trigger a scan on a file or folder.
    Creates FileEntry records for media files.
    """
    try:
        settings = Settings.get_settings(db)

        # Security check
        if not is_path_allowed(path, settings):
            raise HTTPException(status_code=403, detail="Access denied: Path is outside allowed directories")

        target = Path(path)
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Path not found: {path}")

        created_entries = []
        skipped_entries = []

        if target.is_file():
            # Single file scan
            if get_file_type(target.name) == 'video':
                entry = FileEntry.create_or_get(db, str(target))
                if entry:
                    created_entries.append(target.name)
                else:
                    skipped_entries.append(target.name)
            else:
                raise HTTPException(status_code=400, detail="Only video files can be scanned")
        else:
            # Directory scan - find all video files
            for file_path in target.rglob('*'):
                if file_path.is_file() and get_file_type(file_path.name) == 'video':
                    try:
                        entry = FileEntry.create_or_get(db, str(file_path))
                        if entry:
                            created_entries.append(file_path.name)
                        else:
                            skipped_entries.append(file_path.name)
                    except Exception as e:
                        logger.warning(f"Error creating entry for {file_path}: {e}")
                        skipped_entries.append(file_path.name)

        message = f"Scan complete: {len(created_entries)} file(s) added to queue"
        if skipped_entries:
            message += f", {len(skipped_entries)} skipped (already in queue)"

        logger.info(f"Scan triggered for {path}: {len(created_entries)} new, {len(skipped_entries)} skipped")

        return {
            "status": "success",
            "message": message,
            "created": created_entries,
            "skipped": skipped_entries
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error scanning path {path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
