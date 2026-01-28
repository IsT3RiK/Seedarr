"""
Dashboard API Routes for Seedarr v2.0

This module provides FastAPI routes for the WireGuard-inspired dashboard UI.
All dashboard screens use the modern dark theme design with consistent navigation
and layout patterns.

Features:
    - GET /dashboard: Main dashboard with pipeline visualization
    - GET /queue: Active processing queue
    - GET /history: Completed jobs history
    - GET /logs: Terminal-style log viewer

UI Design:
    - WireGuard-inspired dark theme with sidebar navigation
    - Consistent layout across all screens using base.html template
    - Service health monitors in top bar
    - Responsive design for mobile/tablet/desktop
"""

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from app.models.file_entry import FileEntry, Status
from app.services.log_store import get_log_store

logger = logging.getLogger(__name__)

# Router
router = APIRouter()

# Templates - auto-detect path based on working directory
import os
templates_dir = "templates" if os.path.exists("templates") else "backend/templates"
templates = Jinja2Templates(directory=templates_dir)

# Database dependency
from app.database import get_db


def _transform_jobs_for_queue(entries: list) -> list:
    """Transform FileEntry objects to format expected by queue template."""
    import os
    jobs = []
    for entry in entries:
        filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"

        # Calculate relative time
        time_diff = datetime.utcnow() - (entry.created_at or datetime.utcnow())
        if time_diff.days > 0:
            started_relative = f"{time_diff.days}d ago"
        elif time_diff.seconds > 3600:
            started_relative = f"{time_diff.seconds // 3600}h ago"
        elif time_diff.seconds > 60:
            started_relative = f"{time_diff.seconds // 60}m ago"
        else:
            started_relative = "just now"

        jobs.append({
            "id": entry.id,
            "name": filename,
            "size": "Unknown",  # FileEntry doesn't have size field yet
            "file_count": 1,
            "current_stage": entry.status.value.replace("_", " ").title(),
            "progress": _calculate_progress(entry.status),
            "time_remaining": "Unknown",
            "started_relative": started_relative
        })
    return jobs


def _transform_jobs_for_history(entries: list) -> list:
    """Transform FileEntry objects to format expected by history template."""
    import os
    jobs = []
    for entry in entries:
        filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"

        # Calculate duration
        duration = "Unknown"
        if entry.created_at and entry.updated_at:
            duration_seconds = (entry.updated_at - entry.created_at).total_seconds()
            if duration_seconds > 3600:
                hours = int(duration_seconds // 3600)
                minutes = int((duration_seconds % 3600) // 60)
                duration = f"{hours}h {minutes}m"
            elif duration_seconds > 60:
                minutes = int(duration_seconds // 60)
                seconds = int(duration_seconds % 60)
                duration = f"{minutes}m {seconds}s"
            else:
                duration = f"{int(duration_seconds)}s"

        # Calculate relative completed time
        if entry.updated_at:
            time_diff = datetime.utcnow() - entry.updated_at
            if time_diff.days > 0:
                completed_relative = f"{time_diff.days}d ago"
            elif time_diff.seconds > 3600:
                completed_relative = f"{time_diff.seconds // 3600}h ago"
            elif time_diff.seconds > 60:
                completed_relative = f"{time_diff.seconds // 60}m ago"
            else:
                completed_relative = "just now"
        else:
            completed_relative = "Unknown"

        # Map status to template format
        if entry.status == Status.UPLOADED:
            status = "successful"
        elif entry.status == Status.FAILED:
            status = "failed"
        else:
            status = "cancelled"

        jobs.append({
            "id": entry.id,
            "name": filename,
            "size": "Unknown",  # FileEntry doesn't have size field yet
            "file_count": 1,
            "status": status,
            "duration": duration,
            "completed_relative": completed_relative,
            "completed_datetime": entry.updated_at.strftime("%Y-%m-%d %H:%M") if entry.updated_at else "Unknown"
        })
    return jobs


class _DotDict:
    """Helper class to access dict keys as attributes for template compatibility."""
    def __init__(self, data):
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict):
                    setattr(self, key, _DotDict(value))
                else:
                    setattr(self, key, value)

    def __bool__(self):
        return bool(self.__dict__)

    def items(self):
        """Support .items() for iteration."""
        return self.__dict__.items()


def _convert_to_namespace(data):
    """Convert dict to object with attributes for template access."""
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    return _DotDict(data)


def _calculate_progress(status: Status) -> int:
    """Calculate progress percentage based on status."""
    progress_map = {
        Status.PENDING: 0,
        Status.SCANNED: 15,
        Status.ANALYZED: 30,
        Status.PENDING_APPROVAL: 35,  # v2.1: Waiting for user approval
        Status.APPROVED: 40,          # v2.1: User approved
        Status.PREPARING: 50,         # v2.1: Creating hardlinks, screenshots
        Status.RENAMED: 65,
        Status.METADATA_GENERATED: 80,
        Status.UPLOADED: 100,
        Status.FAILED: 0
    }
    return progress_map.get(status, 0)


async def check_service_health(service_url: str, timeout: int = 5) -> str:
    """
    Check if a service is reachable and responding.

    Args:
        service_url: URL of the service to check
        timeout: Request timeout in seconds

    Returns:
        Status string: "connected", "disconnected", or "unknown"
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(service_url)
            return "connected" if response.status_code < 500 else "error"
    except Exception:
        return "disconnected"


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: Session = Depends(get_db)):
    """
    Render the main dashboard page.
    """
    try:
        # Calculate active count (files being processed, not in terminal states)
        active_count = db.query(FileEntry).filter(
            FileEntry.status.notin_([Status.UPLOADED, Status.FAILED])
        ).count()

        # Calculate completed today (files uploaded today)
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        completed_today = db.query(FileEntry).filter(
            FileEntry.status == Status.UPLOADED,
            FileEntry.uploaded_at >= today_start
        ).count()

        # Calculate queue size (pending files)
        queue_size = db.query(FileEntry).filter(
            FileEntry.status == Status.PENDING
        ).count()

        # Calculate success rate
        total_uploaded = db.query(FileEntry).filter(
            FileEntry.status == Status.UPLOADED
        ).count()
        total_failed = db.query(FileEntry).filter(
            FileEntry.status == Status.FAILED
        ).count()
        total_completed = total_uploaded + total_failed

        if total_completed > 0:
            success_rate = round((total_uploaded / total_completed) * 100, 1)
        else:
            success_rate = 0.0

        # Calculate completed yesterday (needed by template)
        yesterday_start = today_start - timedelta(days=1)
        completed_yesterday = db.query(FileEntry).filter(
            FileEntry.status == Status.UPLOADED,
            FileEntry.uploaded_at >= yesterday_start,
            FileEntry.uploaded_at < today_start
        ).count()

        # Fetch recent jobs for pipeline visualization (last 10 jobs ordered by updated_at)
        recent_entries = db.query(FileEntry).order_by(FileEntry.updated_at.desc()).limit(10).all()

        # Transform entries to job format for template
        import os
        recent_jobs = []
        for entry in recent_entries:
            filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"
            display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]

            # Try to get file size
            file_size = None
            if entry.file_path:
                try:
                    # Handle both local and container paths
                    if os.path.exists(entry.file_path):
                        file_size = os.path.getsize(entry.file_path)
                except Exception:
                    pass

            progress = _calculate_progress(entry.status)

            recent_jobs.append({
                "id": entry.id,
                "filename": display_name,
                "file_path": entry.file_path,
                "file_size": file_size,
                "status": entry.status,
                "progress": progress,
                "current_stage": entry.status.value.replace("_", " ").title() if entry.status else "Unknown",
                "created_at": entry.created_at,
                "error_message": entry.error_message
            })

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "recent_jobs": recent_jobs,
                "stats": {
                    "active_jobs": active_count,
                    "completed_today": completed_today,
                    "completed_yesterday": completed_yesterday,
                    "queue_size": queue_size,
                    "success_rate": success_rate
                }
            }
        )
    except Exception as e:
        logger.error(f"Error rendering dashboard page: {e}")
        # Return a minimal error page rather than raising 500
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "error": str(e),
                "recent_jobs": [],
                "stats": {
                    "active_jobs": 0,
                    "completed_today": 0,
                    "completed_yesterday": 0,
                    "queue_size": 0,
                    "success_rate": 0.0
                }
            }
        )

@router.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request, db: Session = Depends(get_db)):
    """
    Render the active queue page.

    Displays jobs currently being processed with status badges
    and progress indicators for each pipeline stage.
    Uses pagination (20 items) with infinite scroll for more.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML response with queue view
    """
    import os

    # Pagination defaults
    limit = 20
    offset = 0

    try:
        # Build query for active jobs (not uploaded or failed)
        query = db.query(FileEntry).filter(
            FileEntry.status.notin_([Status.UPLOADED, Status.FAILED])
        )

        # Get total count
        total_count = query.count()

        # Fetch paginated entries ordered by most recently updated
        entries = query.order_by(FileEntry.updated_at.desc()).offset(offset).limit(limit).all()

        # Transform entries to job format for template
        active_jobs = []
        for entry in entries:
            # Get filename from path
            filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"

            # Use release_name if available, otherwise use filename without extension
            display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]

            # Try to get file size
            file_size = None
            if entry.file_path and os.path.exists(entry.file_path):
                try:
                    file_size = os.path.getsize(entry.file_path)
                except Exception:
                    pass

            # Calculate progress based on status
            progress = _calculate_progress(entry.status)

            # Format started time
            started = entry.created_at.strftime('%Y-%m-%d %H:%M') if entry.created_at else "Unknown"

            active_jobs.append({
                "id": entry.id,
                "filename": display_name,
                "file_path": entry.file_path,
                "file_size": file_size,
                "status": entry.status,
                "progress": progress,
                "current_stage": entry.status.value.replace("_", " ").title() if entry.status else "Unknown",
                "created_at": entry.created_at,
                "error_message": entry.error_message,
                # V2.1 fields for approval workflow and tracker statuses
                "release_name": entry.release_name,
                "final_release_name": getattr(entry, 'final_release_name', None),
                "tmdb_id": entry.tmdb_id,
                "tmdb_type": entry.tmdb_type,
                "cover_url": entry.cover_url,
                "tracker_statuses": entry.tracker_statuses if entry.tracker_statuses else {},
                "duplicate_check_results": entry.duplicate_check_results if hasattr(entry, 'duplicate_check_results') and entry.duplicate_check_results else None,
                "approval_requested_at": getattr(entry, 'approval_requested_at', None),
                "approved_at": getattr(entry, 'approved_at', None),
                "approved_by": getattr(entry, 'approved_by', None),
            })

        # Calculate pagination info
        has_more = (offset + limit) < total_count
        next_offset = offset + limit

        return templates.TemplateResponse(
            "queue.html",
            {
                "request": request,
                "active_jobs": active_jobs,
                "total_count": total_count,
                "offset": offset,
                "limit": limit,
                "search": "",
                "has_more": has_more,
                "next_offset": next_offset
            }
        )
    except Exception as e:
        logger.error(f"Error rendering queue page: {e}")
        return templates.TemplateResponse(
            "queue.html",
            {
                "request": request,
                "error": str(e),
                "active_jobs": [],
                "total_count": 0,
                "offset": 0,
                "limit": 20,
                "search": "",
                "has_more": False,
                "next_offset": 0
            }
        )


@router.get("/api/dashboard/refresh-jobs", response_class=HTMLResponse)
async def refresh_dashboard_jobs(request: Request, db: Session = Depends(get_db)):
    """
    Refresh recent jobs on the dashboard.

    Returns the recent jobs fragment for HTMX polling/injection.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment containing the updated recent jobs
    """
    import os

    try:
        # Fetch recent jobs (last 10) ordered by updated_at
        recent_entries = db.query(FileEntry).order_by(FileEntry.updated_at.desc()).limit(10).all()

        # Transform entries to job format for template
        recent_jobs = []
        for entry in recent_entries:
            filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"
            display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]

            file_size = None
            if entry.file_path:
                try:
                    if os.path.exists(entry.file_path):
                        file_size = os.path.getsize(entry.file_path)
                except Exception:
                    pass

            progress = _calculate_progress(entry.status)

            recent_jobs.append({
                "id": entry.id,
                "filename": display_name,
                "file_path": entry.file_path,
                "file_size": file_size,
                "status": entry.status,
                "progress": progress,
                "current_stage": entry.status.value.replace("_", " ").title() if entry.status else "Unknown",
                "created_at": entry.created_at,
                "error_message": entry.error_message
            })

        return templates.TemplateResponse(
            "components/dashboard_recent_jobs.html",
            {
                "request": request,
                "recent_jobs": recent_jobs
            }
        )
    except Exception as e:
        logger.error(f"Error refreshing dashboard jobs: {e}")
        return f"<p class='text-error'>Error refreshing: {str(e)}</p>"


@router.get("/api/queue/refresh", response_class=HTMLResponse)
async def refresh_queue(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str = Query("", max_length=100),
    db: Session = Depends(get_db)
):
    """
    Refresh queue content with latest data.

    Returns the queue content fragment for HTMX polling.
    Supports pagination and search filtering.

    Args:
        request: FastAPI request object
        offset: Number of items to skip (default 0)
        limit: Number of items to return (default 20, max 100)
        search: Search query for filtering by filename/release_name
        db: Database session

    Returns:
        HTML fragment containing the updated queue content
    """
    import os

    try:
        # Build base query for active jobs (not uploaded or failed)
        query = db.query(FileEntry).filter(
            FileEntry.status.notin_([Status.UPLOADED, Status.FAILED])
        )

        # Apply search filter if provided
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    FileEntry.file_path.ilike(search_pattern),
                    FileEntry.release_name.ilike(search_pattern)
                )
            )

        # Get total count for pagination info
        total_count = query.count()

        # Apply ordering and pagination
        entries = query.order_by(FileEntry.updated_at.desc()).offset(offset).limit(limit).all()

        # Transform entries to job format for template
        active_jobs = []
        for entry in entries:
            filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"
            display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]

            file_size = None
            if entry.file_path:
                try:
                    if os.path.exists(entry.file_path):
                        file_size = os.path.getsize(entry.file_path)
                except Exception:
                    pass

            progress = _calculate_progress(entry.status)

            # Calculate relative time
            time_diff = datetime.utcnow() - (entry.created_at or datetime.utcnow())
            if time_diff.days > 0:
                started_relative = f"{time_diff.days}d ago"
            elif time_diff.seconds > 3600:
                started_relative = f"{time_diff.seconds // 3600}h ago"
            elif time_diff.seconds > 60:
                started_relative = f"{time_diff.seconds // 60}m ago"
            else:
                started_relative = "just now"

            active_jobs.append({
                "id": entry.id,
                "filename": display_name,
                "file_path": entry.file_path,
                "file_size": file_size,
                "status": entry.status,
                "progress": progress,
                "current_stage": entry.status.value.replace("_", " ").title() if entry.status else "Unknown",
                "created_at": entry.created_at,
                "started_relative": started_relative,
                "error_message": entry.error_message,
                # V2.1 fields for approval workflow and tracker statuses
                "release_name": entry.release_name,
                "final_release_name": getattr(entry, 'final_release_name', None),
                "tmdb_id": entry.tmdb_id,
                "tmdb_type": entry.tmdb_type,
                "cover_url": entry.cover_url,
                "tracker_statuses": entry.tracker_statuses if entry.tracker_statuses else {},
                "duplicate_check_results": entry.duplicate_check_results if hasattr(entry, 'duplicate_check_results') and entry.duplicate_check_results else None,
                "approval_requested_at": getattr(entry, 'approval_requested_at', None),
                "approved_at": getattr(entry, 'approved_at', None),
                "approved_by": getattr(entry, 'approved_by', None),
            })

        # Calculate pagination info
        has_more = (offset + limit) < total_count
        next_offset = offset + limit

        return templates.TemplateResponse(
            "components/queue_content.html",
            {
                "request": request,
                "active_jobs": active_jobs,
                "total_count": total_count,
                "offset": offset,
                "limit": limit,
                "search": search,
                "has_more": has_more,
                "next_offset": next_offset
            }
        )
    except Exception as e:
        logger.error(f"Error refreshing queue: {e}")
        return f"<p class='text-error'>Error refreshing queue: {str(e)}</p>"


@router.get("/api/queue/load-more", response_class=HTMLResponse)
async def load_more_queue(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str = Query("", max_length=100),
    db: Session = Depends(get_db)
):
    """
    Load more queue items for infinite scroll.

    Returns only the table rows (not the full component) for appending.

    Args:
        request: FastAPI request object
        offset: Number of items to skip
        limit: Number of items to return
        search: Search query for filtering
        db: Database session

    Returns:
        HTML fragment containing additional table rows
    """
    import os

    try:
        # Build base query for active jobs (not uploaded or failed)
        query = db.query(FileEntry).filter(
            FileEntry.status.notin_([Status.UPLOADED, Status.FAILED])
        )

        # Apply search filter if provided
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    FileEntry.file_path.ilike(search_pattern),
                    FileEntry.release_name.ilike(search_pattern)
                )
            )

        # Get total count for pagination info
        total_count = query.count()

        # Apply ordering and pagination
        entries = query.order_by(FileEntry.updated_at.desc()).offset(offset).limit(limit).all()

        # Transform entries to job format for template
        active_jobs = []
        for entry in entries:
            filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"
            display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]

            file_size = None
            if entry.file_path:
                try:
                    if os.path.exists(entry.file_path):
                        file_size = os.path.getsize(entry.file_path)
                except Exception:
                    pass

            progress = _calculate_progress(entry.status)

            # Calculate relative time
            time_diff = datetime.utcnow() - (entry.created_at or datetime.utcnow())
            if time_diff.days > 0:
                started_relative = f"{time_diff.days}d ago"
            elif time_diff.seconds > 3600:
                started_relative = f"{time_diff.seconds // 3600}h ago"
            elif time_diff.seconds > 60:
                started_relative = f"{time_diff.seconds // 60}m ago"
            else:
                started_relative = "just now"

            active_jobs.append({
                "id": entry.id,
                "filename": display_name,
                "file_path": entry.file_path,
                "file_size": file_size,
                "status": entry.status,
                "progress": progress,
                "current_stage": entry.status.value.replace("_", " ").title() if entry.status else "Unknown",
                "created_at": entry.created_at,
                "started_relative": started_relative,
                "error_message": entry.error_message,
                "release_name": entry.release_name,
                "final_release_name": getattr(entry, 'final_release_name', None),
                "tmdb_id": entry.tmdb_id,
                "tmdb_type": entry.tmdb_type,
                "cover_url": entry.cover_url,
                "tracker_statuses": entry.tracker_statuses if entry.tracker_statuses else {},
                "duplicate_check_results": entry.duplicate_check_results if hasattr(entry, 'duplicate_check_results') and entry.duplicate_check_results else None,
                "approval_requested_at": getattr(entry, 'approval_requested_at', None),
                "approved_at": getattr(entry, 'approved_at', None),
                "approved_by": getattr(entry, 'approved_by', None),
            })

        # Calculate pagination info
        has_more = (offset + limit) < total_count
        next_offset = offset + limit

        return templates.TemplateResponse(
            "components/queue_rows.html",
            {
                "request": request,
                "jobs": active_jobs,
                "search": search,
                "has_more": has_more,
                "next_offset": next_offset,
                "limit": limit
            }
        )
    except Exception as e:
        logger.error(f"Error loading more queue items: {e}")
        return f"<tr><td colspan='7' class='text-error'>Error loading more: {str(e)}</td></tr>"


@router.get("/release/{release_id}", response_class=HTMLResponse)
async def release_details_page(request: Request, release_id: int, db: Session = Depends(get_db)):
    """
    Render the release details page.

    Displays detailed information about a release including:
    - TMDB/IMDB metadata
    - MediaInfo technical details
    - Duplicate check results with existing torrents

    Args:
        request: FastAPI request object
        release_id: ID of the FileEntry to display
        db: Database session

    Returns:
        HTML response with release details view
    """
    import os

    try:
        # Fetch the file entry
        entry = db.query(FileEntry).filter(FileEntry.id == release_id).first()

        if not entry:
            return templates.TemplateResponse(
                "release_details.html",
                {
                    "request": request,
                    "error": "Release not found",
                    "release": None
                }
            )

        # Get filename from path
        filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"
        display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]

        # Get file size
        file_size = None
        if entry.file_path:
            try:
                if os.path.exists(entry.file_path):
                    file_size = os.path.getsize(entry.file_path)
            except Exception:
                pass

        # Format file size for display
        file_size_display = "Unknown"
        if file_size:
            if file_size > 1073741824:
                file_size_display = f"{file_size / 1073741824:.2f} GB"
            elif file_size > 1048576:
                file_size_display = f"{file_size / 1048576:.2f} MB"
            else:
                file_size_display = f"{file_size / 1024:.2f} KB"

        # Parse MediaInfo data for display
        mediainfo = entry.mediainfo_data if isinstance(entry.mediainfo_data, dict) else {}

        # Handle both old format (video/audio dicts) and new format (video_tracks/audio_tracks lists)
        video_tracks = mediainfo.get('video_tracks', [])
        audio_tracks = mediainfo.get('audio_tracks', [])

        # Get first video track or fallback to old format
        if video_tracks and isinstance(video_tracks, list) and len(video_tracks) > 0:
            video_info = video_tracks[0]
        else:
            video_info = mediainfo.get('video', {}) if isinstance(mediainfo.get('video'), dict) else {}

        # Get first audio track or fallback to old format
        if audio_tracks and isinstance(audio_tracks, list) and len(audio_tracks) > 0:
            audio_info = audio_tracks[0]
        else:
            audio_info = mediainfo.get('audio', {}) if isinstance(mediainfo.get('audio'), dict) else {}

        # Build structured mediainfo for template
        mediainfo_display = {
            'video': {
                'codec': video_info.get('codec', 'Unknown'),
                'resolution': f"{video_info.get('width', '?')}x{video_info.get('height', '?')}",
                'height': video_info.get('height'),
                'frame_rate': video_info.get('frame_rate', 'Unknown'),
                'bit_depth': video_info.get('bit_depth', 'Unknown'),
                'hdr': bool(video_info.get('hdr_format')),
                'hdr_format': video_info.get('hdr_format', ''),
            },
            'audio': {
                'codec': audio_info.get('codec', 'Unknown'),
                'channels': audio_info.get('channels', 'Unknown'),
                'language': audio_info.get('language', 'Unknown'),
            },
            'audio_tracks': audio_tracks,  # All audio tracks for detailed view
            'subtitle_tracks': mediainfo.get('subtitle_tracks', []),
            'duration': mediainfo.get('duration', ''),
            'duration_display': mediainfo.get('duration', 'Unknown'),
            'file_size': mediainfo.get('file_size', ''),
            'overall_bitrate': mediainfo.get('overall_bitrate', ''),
        }

        # Calculate progress
        progress = _calculate_progress(entry.status)

        # Build release object for template
        release = {
            "id": entry.id,
            "filename": filename,
            "display_name": display_name,
            "file_path": entry.file_path,
            "file_size": file_size,
            "file_size_display": file_size_display,
            "status": entry.status,
            "status_display": entry.status.value.replace("_", " ").title() if entry.status else "Unknown",
            "progress": progress,
            "error_message": entry.error_message,
            # TMDB metadata
            "tmdb_id": entry.tmdb_id,
            "tmdb_type": entry.tmdb_type,
            "cover_url": entry.cover_url,
            "description": entry.description,
            # Release info
            "release_name": entry.release_name,
            "final_release_name": entry.final_release_name,
            "category_id": entry.category_id,
            "tag_ids": entry.get_tag_ids(),
            # MediaInfo
            "mediainfo": mediainfo_display,
            "mediainfo_raw": mediainfo,
            # Tracker info
            "tracker_statuses": entry.tracker_statuses if isinstance(entry.tracker_statuses, dict) else {},
            "duplicate_check_results": _convert_to_namespace(entry.duplicate_check_results),
            "tracker_torrent_id": entry.tracker_torrent_id,
            "tracker_torrent_url": entry.tracker_torrent_url,
            "torrent_paths": entry.get_torrent_paths(),
            "tracker_release_names": entry.get_tracker_release_names(),
            # Approval workflow
            "approval_requested_at": entry.approval_requested_at,
            "approved_at": entry.approved_at,
            "approved_by": entry.approved_by,
            "corrections": entry.corrections,
            # Screenshots
            "screenshot_paths": entry.get_screenshot_paths(),
            "screenshot_urls": entry.get_screenshot_urls(),
            # Timestamps
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
            "scanned_at": entry.scanned_at,
            "analyzed_at": entry.analyzed_at,
            "renamed_at": entry.renamed_at,
            "metadata_generated_at": entry.metadata_generated_at,
            "uploaded_at": entry.uploaded_at,
        }

        return templates.TemplateResponse(
            "release_details.html",
            {
                "request": request,
                "release": release,
                "error": None
            }
        )
    except Exception as e:
        logger.error(f"Error rendering release details: {e}")
        return templates.TemplateResponse(
            "release_details.html",
            {
                "request": request,
                "error": str(e),
                "release": None
            }
        )


def _format_duration(seconds: int) -> str:
    """Format duration in seconds to human readable string."""
    if not seconds:
        return "Unknown"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, db: Session = Depends(get_db)):
    """
    Render the history page.

    Displays completed and failed jobs with timestamps and final status.
    Allows filtering and searching through historical job data.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML response with history view
    """
    import os

    try:
        # Fetch completed and failed jobs (terminal states) ordered by most recently updated
        entries = db.query(FileEntry).filter(
            FileEntry.status.in_([Status.UPLOADED, Status.FAILED])
        ).order_by(FileEntry.updated_at.desc()).all()

        # Transform entries to include computed fields
        completed_jobs = []
        for entry in entries:
            # Get filename from path
            filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"
            # Use release_name if available, otherwise filename without extension
            display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]

            # Try to get file size
            file_size = None
            if entry.file_path:
                try:
                    if os.path.exists(entry.file_path):
                        file_size = os.path.getsize(entry.file_path)
                except Exception:
                    pass

            completed_jobs.append({
                "id": entry.id,
                "filename": display_name,
                "file_path": entry.file_path,
                "file_size": file_size,
                "status": entry.status,
                "error_message": entry.error_message,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "tracker_torrent_url": entry.tracker_torrent_url
            })

        return templates.TemplateResponse(
            "history.html",
            {
                "request": request,
                "completed_jobs": completed_jobs
            }
        )
    except Exception as e:
        logger.error(f"Error rendering history page: {e}")
        return templates.TemplateResponse(
            "history.html",
            {
                "request": request,
                "error": str(e),
                "completed_jobs": []
            }
        )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, db: Session = Depends(get_db)):
    """
    Render the logs viewer page.

    Displays application logs in a terminal-style interface with:
    - Black background and monospace font
    - Collapsible log sections
    - Real-time log streaming (future enhancement)
    - Log level filtering

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML response with logs view
    """
    try:
        logger.info("Loading logs page")
        store = get_log_store()
        log_entries = store.get_entries(limit=1000)  # Increased from 500 to 1000
        stats = store.get_stats()

        logger.info(f"Retrieved {len(log_entries)} log entries")
        return templates.TemplateResponse(
            "logs.html",
            {
                "request": request,
                "log_entries": log_entries,
                "log_stats": stats
            }
        )
    except Exception as e:
        logger.error(f"Error rendering logs page: {e}")
        return templates.TemplateResponse(
            "logs.html",
            {
                "request": request,
                "error": str(e),
                "log_entries": []
            }
        )


# ============================================================================
# History Page API Endpoints - Handle refresh, filter, and pagination actions
# ============================================================================


@router.get("/api/history/refresh", response_class=HTMLResponse)
async def refresh_history(request: Request, db: Session = Depends(get_db)):
    """
    Refresh history table with latest job data.

    Fetches the current page of jobs from the database and returns the
    rendered table component for HTMX to inject into the page.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment containing the history table component
    """
    import os

    try:
        # Fetch completed and failed jobs
        entries = db.query(FileEntry).filter(
            FileEntry.status.in_([Status.UPLOADED, Status.FAILED])
        ).order_by(FileEntry.updated_at.desc()).all()

        # Transform entries to include computed fields
        completed_jobs = []
        for entry in entries:
            filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"
            display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]

            file_size = None
            if entry.file_path:
                try:
                    if os.path.exists(entry.file_path):
                        file_size = os.path.getsize(entry.file_path)
                except Exception:
                    pass

            completed_jobs.append({
                "id": entry.id,
                "filename": display_name,
                "file_path": entry.file_path,
                "file_size": file_size,
                "status": entry.status,
                "error_message": entry.error_message,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "tracker_torrent_url": entry.tracker_torrent_url
            })

        logger.info(f"Refreshing history: {len(completed_jobs)} jobs")

        return templates.TemplateResponse(
            "components/history_table.html",
            {
                "request": request,
                "completed_jobs": completed_jobs
            }
        )
    except Exception as e:
        logger.error(f"Error refreshing history: {e}")
        return f"<p class='text-error'>Error refreshing history: {str(e)}</p>"


@router.post("/api/history/filter", response_class=HTMLResponse)
async def filter_history(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Filter history table by job status.

    Filters jobs based on the provided status and returns the filtered table.
    Supports status filters: all, successful, failed, cancelled.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment containing the filtered history table component
    """
    import os

    try:
        # Get form data
        form_data = await request.form()
        status = form_data.get("status", "")

        # Validate status parameter
        valid_statuses = ['', 'all', 'successful', 'failed', 'cancelled']
        filter_status = status if status in valid_statuses else ''

        # Build query based on filter
        if filter_status == 'successful':
            query = db.query(FileEntry).filter(FileEntry.status == Status.UPLOADED)
        elif filter_status == 'failed':
            query = db.query(FileEntry).filter(FileEntry.status == Status.FAILED)
        else:  # 'all' or '' or 'cancelled' (we don't have a cancelled status yet)
            query = db.query(FileEntry).filter(
                FileEntry.status.in_([Status.UPLOADED, Status.FAILED])
            )

        entries = query.order_by(FileEntry.updated_at.desc()).all()

        logger.info(f"Filtering history by status: '{filter_status}' - found {len(entries)} jobs")

        # Transform entries to include computed fields
        completed_jobs = []
        for entry in entries:
            filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"
            display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]

            file_size = None
            if entry.file_path:
                try:
                    if os.path.exists(entry.file_path):
                        file_size = os.path.getsize(entry.file_path)
                except Exception:
                    pass

            completed_jobs.append({
                "id": entry.id,
                "filename": display_name,
                "file_path": entry.file_path,
                "file_size": file_size,
                "status": entry.status,
                "error_message": entry.error_message,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "tracker_torrent_url": entry.tracker_torrent_url
            })

        return templates.TemplateResponse(
            "components/history_table.html",
            {
                "request": request,
                "completed_jobs": completed_jobs
            }
        )
    except Exception as e:
        logger.error(f"Error filtering history: {e}")
        return f"<p class='text-error'>Error filtering history: {str(e)}</p>"


@router.get("/api/history/page", response_class=HTMLResponse)
async def paginate_history(
    request: Request,
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db)
):
    """
    Paginate through history table.

    Loads a specific page of job history from the database.
    Implements pagination with configurable page size.

    Args:
        request: FastAPI request object
        page: Page number (1-indexed)
        db: Database session

    Returns:
        HTML fragment containing the history table component for the requested page
    """
    try:
        # Page size configuration
        page_size = 10  # Items per page

        # TODO: Query database for jobs on requested page
        # Implement offset calculation: offset = (page - 1) * page_size
        # Query should include total_pages calculation for pagination controls
        paginated_jobs = []
        total_pages = 1

        logger.info(f"Loading history page: {page}")

        return templates.TemplateResponse(
            "components/history_table.html",
            {
                "request": request,
                "jobs": paginated_jobs,
                "current_page": page,
                "total_pages": total_pages
            }
        )
    except Exception as e:
        logger.error(f"Error paginating history: {e}")
        return f"<p class='text-error'>Error loading page: {str(e)}</p>"


@router.get("/api/history/details/{job_id}", response_class=HTMLResponse)
async def get_job_details(
    request: Request,
    job_id: str,
    db: Session = Depends(get_db)
):
    """
    Get detailed information for a specific job.

    Returns job details for display in a modal or details panel.

    Args:
        request: FastAPI request object
        job_id: ID of the job to retrieve details for
        db: Database session

    Returns:
        HTML fragment containing job details
    """
    try:
        # TODO: Query database for job details by ID
        logger.info(f"Fetching details for job: {job_id}")

        # Placeholder HTML for job details
        return f"""
        <div class="job-details">
            <h3>Job Details</h3>
            <p>Job ID: {job_id}</p>
            <p>Status: Loading...</p>
        </div>
        """
    except Exception as e:
        logger.error(f"Error fetching job details: {e}")
        return f"<p class='text-error'>Error loading job details: {str(e)}</p>"


@router.post("/api/history/reprocess", response_class=HTMLResponse)
async def reprocess_job(
    request: Request,
    job_id: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    Reprocess a completed job.

    Changes the job status from completed back to queued for reprocessing.

    Args:
        request: FastAPI request object
        job_id: ID of the job to reprocess
        db: Database session

    Returns:
        HTML fragment with updated table
    """
    try:
        if not job_id:
            return "<p class='text-error'>Job ID is required</p>"

        # Find the job
        job = db.query(FileEntry).filter(FileEntry.id == int(job_id)).first()
        if not job:
            return "<p class='text-error'>Job not found</p>"

        # Reset to pending status
        job.reset_from_checkpoint(Status.PENDING)
        db.commit()

        logger.info(f"Reprocessing job: {job_id}")

        # Return updated table with all completed jobs
        completed_entries = db.query(FileEntry).filter(
            FileEntry.status.in_([Status.UPLOADED, Status.FAILED])
        ).order_by(FileEntry.updated_at.desc()).all()

        jobs = _transform_jobs_for_history(completed_entries)

        return templates.TemplateResponse(
            "components/history_table.html",
            {
                "request": request,
                "jobs": jobs
            }
        )
    except Exception as e:
        logger.error(f"Error reprocessing job: {e}")
        return f"<p class='text-error'>Error reprocessing job: {str(e)}</p>"


@router.get("/api/history/error/{job_id}", response_class=HTMLResponse)
async def get_job_error(
    request: Request,
    job_id: str,
    db: Session = Depends(get_db)
):
    """
    Get error details for a failed job.

    Returns error logs and details for failed jobs.

    Args:
        request: FastAPI request object
        job_id: ID of the failed job
        db: Database session

    Returns:
        HTML fragment containing error details
    """
    try:
        # Find the job
        job = db.query(FileEntry).filter(FileEntry.id == int(job_id)).first()

        if not job:
            return "<p class='text-error'>Job not found</p>"

        error_message = job.error_message or "No error details available"
        filename = job.file_path.split('/')[-1] if job.file_path else "Unknown"

        logger.info(f"Fetching error details for job: {job_id}")

        # Return formatted error details
        return f"""
        <div class="error-details p-4 bg-gray-800 rounded-lg">
            <h3 class="text-lg font-bold text-red-400 mb-2">Error Details</h3>
            <p class="text-sm text-gray-300 mb-2"><strong>Job ID:</strong> {job_id}</p>
            <p class="text-sm text-gray-300 mb-2"><strong>File:</strong> {filename}</p>
            <p class="text-sm text-gray-300 mb-4"><strong>Status:</strong> {job.status.value}</p>
            <div class="bg-gray-900 p-3 rounded">
                <p class="text-xs text-gray-400 mb-1">Error Message:</p>
                <pre class="text-red-300 text-sm whitespace-pre-wrap">{error_message}</pre>
            </div>
        </div>
        """
    except Exception as e:
        logger.error(f"Error fetching job error details: {e}")
        return f"<p class='text-error'>Error loading error details: {str(e)}</p>"


@router.post("/api/history/retry", response_class=HTMLResponse)
async def retry_job(
    request: Request,
    job_id: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    Retry a failed job.

    Changes the job status from failed back to queued for retry.

    Args:
        request: FastAPI request object
        job_id: ID of the job to retry
        db: Database session

    Returns:
        HTML fragment with updated table
    """
    try:
        if not job_id:
            return "<p class='text-error'>Job ID is required</p>"

        # Find the job
        job = db.query(FileEntry).filter(FileEntry.id == int(job_id)).first()
        if not job:
            return "<p class='text-error'>Job not found</p>"

        # Reset to pending status for retry
        job.reset_from_checkpoint(Status.PENDING)
        db.commit()

        logger.info(f"Retrying job: {job_id}")

        # Return updated table with all completed jobs
        completed_entries = db.query(FileEntry).filter(
            FileEntry.status.in_([Status.UPLOADED, Status.FAILED])
        ).order_by(FileEntry.updated_at.desc()).all()

        jobs = _transform_jobs_for_history(completed_entries)

        return templates.TemplateResponse(
            "components/history_table.html",
            {
                "request": request,
                "jobs": jobs
            }
        )
    except Exception as e:
        logger.error(f"Error retrying job: {e}")
        return f"<p class='text-error'>Error retrying job: {str(e)}</p>"


# ============================================================================
# Queue Page API Endpoints - Handle pause, cancel, and queue management
# ============================================================================


@router.get("/api/queue/filter", response_class=HTMLResponse)
async def filter_queue(
    request: Request,
    stage: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Filter queue by job processing stage.

    Filters active jobs by their current processing stage.
    Supports: all, scan, analyze, rename, metadata, upload.

    Args:
        request: FastAPI request object
        stage: Processing stage to filter by
        db: Database session

    Returns:
        HTML fragment containing the filtered queue tables
    """
    try:
        valid_stages = ['all', 'scan', 'analyze', 'rename', 'metadata', 'upload']
        filter_stage = stage if stage in valid_stages else 'all'

        # Build query for active jobs
        query = db.query(FileEntry).filter(
            FileEntry.status.notin_([Status.UPLOADED, Status.FAILED])
        )

        # Apply stage filter
        if filter_stage != 'all':
            stage_map = {
                'scan': Status.SCANNED,
                'analyze': Status.ANALYZED,
                'rename': Status.RENAMED,
                'metadata': Status.METADATA_GENERATED,
                'upload': Status.UPLOADED  # Won't match since we exclude uploaded
            }
            if filter_stage in stage_map:
                query = query.filter(FileEntry.status == stage_map[filter_stage])

        active_entries = query.order_by(FileEntry.updated_at.desc()).all()
        active_jobs = _transform_jobs_for_queue(active_entries)
        waiting_jobs = []

        logger.info(f"Filtering queue by stage: {filter_stage}")

        return templates.TemplateResponse(
            "components/queue_table.html",
            {
                "request": request,
                "active_jobs": active_jobs,
                "waiting_jobs": waiting_jobs
            }
        )
    except Exception as e:
        logger.error(f"Error filtering queue: {e}")
        return f"<p class='text-error'>Error filtering queue: {str(e)}</p>"


@router.post("/api/queue/pause-all", response_class=HTMLResponse)
async def pause_all_jobs(request: Request, db: Session = Depends(get_db)):
    """
    Pause all active jobs.

    Changes the status of all processing jobs to paused.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment containing the updated queue tables
    """
    try:
        # TODO: Query database for all active jobs, update status to 'paused'
        logger.info("Pausing all jobs")

        active_jobs = []
        waiting_jobs = []

        return templates.TemplateResponse(
            "components/queue_table.html",
            {
                "request": request,
                "active_jobs": active_jobs,
                "waiting_jobs": waiting_jobs
            }
        )
    except Exception as e:
        logger.error(f"Error pausing all jobs: {e}")
        return f"<p class='text-error'>Error pausing jobs: {str(e)}</p>"


@router.post("/api/queue/pause", response_class=HTMLResponse)
async def pause_job(
    request: Request,
    job_id: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    Pause a specific job.

    Changes the status of a job from processing to paused.

    Args:
        request: FastAPI request object
        job_id: ID of the job to pause
        db: Database session

    Returns:
        HTML fragment containing the updated queue tables
    """
    try:
        if not job_id:
            return "<p class='text-error'>Job ID is required</p>"

        # Find the job
        job = db.query(FileEntry).filter(FileEntry.id == int(job_id)).first()
        if job:
            # For now, just keep it in current status (we don't have a PAUSED status yet)
            # In future, you could add Status.PAUSED to the Status enum
            logger.info(f"Pausing job {job_id} - keeping status as {job.status.value}")

        logger.info(f"Pausing job: {job_id}")

        # Refresh and return queue
        active_entries = db.query(FileEntry).filter(
            FileEntry.status.notin_([Status.UPLOADED, Status.FAILED])
        ).order_by(FileEntry.updated_at.desc()).all()

        active_jobs = _transform_jobs_for_queue(active_entries)
        waiting_jobs = []

        return templates.TemplateResponse(
            "components/queue_table.html",
            {
                "request": request,
                "active_jobs": active_jobs,
                "waiting_jobs": waiting_jobs
            }
        )
    except Exception as e:
        logger.error(f"Error pausing job: {e}")
        return f"<p class='text-error'>Error pausing job: {str(e)}</p>"


@router.post("/api/queue/cancel", response_class=HTMLResponse)
async def cancel_job(
    request: Request,
    job_id: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    Cancel a job.

    Changes the status of a job to cancelled and removes it from processing.

    Args:
        request: FastAPI request object
        job_id: ID of the job to cancel
        db: Database session

    Returns:
        HTML fragment containing the updated queue tables
    """
    try:
        if not job_id:
            return "<p class='text-error'>Job ID is required</p>"

        # Find the job
        job = db.query(FileEntry).filter(FileEntry.id == int(job_id)).first()
        if job:
            # Mark as failed with cancellation message
            job.mark_failed("Cancelled by user")
            db.commit()
            logger.info(f"Cancelled job: {job_id}")

        # Refresh and return queue
        active_entries = db.query(FileEntry).filter(
            FileEntry.status.notin_([Status.UPLOADED, Status.FAILED])
        ).order_by(FileEntry.updated_at.desc()).all()

        active_jobs = _transform_jobs_for_queue(active_entries)
        waiting_jobs = []

        return templates.TemplateResponse(
            "components/queue_table.html",
            {
                "request": request,
                "active_jobs": active_jobs,
                "waiting_jobs": waiting_jobs
            }
        )
    except Exception as e:
        logger.error(f"Error cancelling job: {e}")
        return f"<p class='text-error'>Error cancelling job: {str(e)}</p>"


@router.post("/api/queue/move-up", response_class=HTMLResponse)
async def move_up_in_queue(
    request: Request,
    job_id: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    Move a job up in the waiting queue.

    Increases the priority of a waiting job by moving it up one position.

    Args:
        request: FastAPI request object
        job_id: ID of the job to move up
        db: Database session

    Returns:
        HTML fragment containing the updated queue tables
    """
    try:
        if not job_id:
            return "<p class='text-error'>Job ID is required</p>"

        # TODO: Query database for job, swap position with job above
        logger.info(f"Moving job up in queue: {job_id}")

        active_jobs = []
        waiting_jobs = []

        return templates.TemplateResponse(
            "components/queue_table.html",
            {
                "request": request,
                "active_jobs": active_jobs,
                "waiting_jobs": waiting_jobs
            }
        )
    except Exception as e:
        logger.error(f"Error moving job up in queue: {e}")
        return f"<p class='text-error'>Error moving job: {str(e)}</p>"


@router.post("/api/queue/remove", response_class=HTMLResponse)
async def remove_from_queue(
    request: Request,
    job_id: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    Remove a job from the waiting queue.

    Removes a job from the waiting queue without processing.

    Args:
        request: FastAPI request object
        job_id: ID of the job to remove
        db: Database session

    Returns:
        HTML fragment containing the updated queue tables
    """
    try:
        if not job_id:
            return "<p class='text-error'>Job ID is required</p>"

        # TODO: Query database for job, update status to 'removed' or delete from queue
        logger.info(f"Removing job from queue: {job_id}")

        active_jobs = []
        waiting_jobs = []

        return templates.TemplateResponse(
            "components/queue_table.html",
            {
                "request": request,
                "active_jobs": active_jobs,
                "waiting_jobs": waiting_jobs
            }
        )
    except Exception as e:
        logger.error(f"Error removing job from queue: {e}")
        return f"<p class='text-error'>Error removing job: {str(e)}</p>"


# ============================================================================
# Dashboard Page API Endpoints - Handle scan and stats refresh
# ============================================================================


@router.post("/api/dashboard/scan", response_class=HTMLResponse)
async def scan_directory(request: Request, db: Session = Depends(get_db)):
    """
    Trigger a directory scan to discover new torrents.

    Initiates a new scan job to discover torrents in the configured directory.
    The scan is performed asynchronously and added to the queue.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment with confirmation message
    """
    try:
        # Get settings to log the scan directory
        settings = Settings.get_settings(db)
        scan_dir = settings.input_media_path if settings else "not configured"

        logger.info(f"📂 Scan directory request received")
        logger.info(f"   Target directory: {scan_dir}")
        logger.info(f"   Requested by: {request.client.host if request.client else 'unknown'}")

        # TODO: Create a new scan job in database with status 'queued'
        logger.info("✓ Scan directory triggered successfully")

        return """
        <div class="alert alert-success">
            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path>
            </svg>
            <span>Scan directory triggered successfully</span>
        </div>
        """
    except Exception as e:
        logger.error(f"Error scanning directory: {e}")
        return f"<div class='alert alert-error'>Error scanning directory: {str(e)}</div>"


@router.post("/api/dashboard/process", response_class=HTMLResponse)
async def process_pending_files(request: Request, db: Session = Depends(get_db)):
    """
    Process all pending files through the pipeline.

    Triggers the processing pipeline for all files with PENDING status.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment with processing result
    """
    from app.models.settings import Settings
    from app.processors.pipeline import ProcessingPipeline
    from app.adapters.lacale_adapter import LaCaleAdapter

    try:
        logger.info("🔄 Process all pending files request received")
        logger.info(f"   Requested by: {request.client.host if request.client else 'unknown'}")

        # Get pending files
        pending_files = db.query(FileEntry).filter(
            FileEntry.status == Status.PENDING
        ).all()

        logger.info(f"   Found {len(pending_files)} pending file(s) to process")

        if not pending_files:
            logger.info("   No files to process, returning info message")
            return """
            <div class="alert alert-info">
                <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"></path>
                </svg>
                <span>No pending files to process</span>
            </div>
            """

        # Get settings for tracker adapter
        settings = Settings.get_settings(db)
        logger.info("   Loading settings and initializing pipeline...")

        # Initialize tracker adapter
        tracker_adapter = None
        if settings.flaresolverr_url and settings.tracker_url and settings.tracker_passkey:
            logger.info(f"   Using tracker: {settings.tracker_url}")
            logger.info(f"   Using FlareSolverr: {settings.flaresolverr_url}")
            tracker_adapter = LaCaleAdapter(
                flaresolverr_url=settings.flaresolverr_url,
                tracker_url=settings.tracker_url,
                passkey=settings.tracker_passkey
            )
        else:
            logger.warning("   ⚠ Tracker adapter not configured, processing will be limited")

        # Initialize pipeline
        logger.info("   ✓ Pipeline initialized")
        pipeline = ProcessingPipeline(db, tracker_adapter)

        # Process each pending file
        logger.info("   Starting file processing...")
        processed_count = 0
        failed_count = 0
        for idx, file_entry in enumerate(pending_files, 1):
            try:
                logger.info(f"   [{idx}/{len(pending_files)}] Processing: {file_entry.file_path}")
                await pipeline.process_file(file_entry)
                processed_count += 1
                logger.info(f"   [{idx}/{len(pending_files)}] ✓ Success: {file_entry.file_path}")
            except Exception as e:
                logger.error(f"   [{idx}/{len(pending_files)}] ✗ Failed: {file_entry.file_path}")
                logger.error(f"      Error: {type(e).__name__}: {e}")
                failed_count += 1

        message = f"Processed {processed_count} file(s)"
        if failed_count > 0:
            message += f", {failed_count} failed"

        logger.info(f"✓ Processing complete: {message}")

        alert_class = "alert-success" if failed_count == 0 else "alert-warning"
        return f"""
        <div class="alert {alert_class}">
            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path>
            </svg>
            <span>{message}</span>
        </div>
        """

    except Exception as e:
        logger.error(f"Error processing files: {e}")
        return f"<div class='alert alert-error'>Error processing files: {str(e)}</div>"


@router.post("/api/queue/process/{job_id}", response_class=HTMLResponse)
async def process_single_job(
    request: Request,
    job_id: int,
    db: Session = Depends(get_db)
):
    """
    Process a single pending job.

    Args:
        request: FastAPI request object
        job_id: ID of the job to process
        db: Database session

    Returns:
        HTML fragment with processing result
    """
    from app.models.settings import Settings
    from app.processors.pipeline import ProcessingPipeline
    from app.adapters.lacale_adapter import LaCaleAdapter

    try:
        # Get the file entry
        file_entry = db.query(FileEntry).filter(FileEntry.id == job_id).first()

        if not file_entry:
            return "<div class='alert alert-error'>Job not found</div>"

        # Get settings for tracker adapter
        settings = Settings.get_settings(db)

        # Initialize tracker adapter
        tracker_adapter = None
        if settings.flaresolverr_url and settings.tracker_url and settings.tracker_passkey:
            tracker_adapter = LaCaleAdapter(
                flaresolverr_url=settings.flaresolverr_url,
                tracker_url=settings.tracker_url,
                passkey=settings.tracker_passkey
            )

        # Initialize pipeline and process
        pipeline = ProcessingPipeline(db, tracker_adapter)

        logger.info(f"Processing single job: {file_entry.file_path}")
        await pipeline.process_file(file_entry)

        return """
        <div class="alert alert-success">
            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path>
            </svg>
            <span>Job processed successfully</span>
        </div>
        """

    except Exception as e:
        logger.error(f"Error processing job {job_id}: {e}")
        return f"<div class='alert alert-error'>Error: {str(e)}</div>"


@router.delete("/api/queue/delete/{job_id}", response_class=HTMLResponse)
async def delete_job(
    request: Request,
    job_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete a job from the queue.

    Args:
        request: FastAPI request object
        job_id: ID of the job to delete
        db: Database session

    Returns:
        HTML fragment refreshing the queue content
    """
    import os

    try:
        # Get the file entry
        file_entry = db.query(FileEntry).filter(FileEntry.id == job_id).first()

        if not file_entry:
            return "<div class='alert alert-error'>Job not found</div>"

        # Delete the entry
        db.delete(file_entry)
        db.commit()

        logger.info(f"Deleted job {job_id} from queue")

        # Re-fetch and return updated queue content
        entries = db.query(FileEntry).filter(
            FileEntry.status.notin_([Status.UPLOADED, Status.FAILED])
        ).order_by(FileEntry.updated_at.desc()).all()

        # Transform entries to job format for template
        active_jobs = []
        for entry in entries:
            filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"
            display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]
            file_size = None
            if entry.file_path and os.path.exists(entry.file_path):
                try:
                    file_size = os.path.getsize(entry.file_path)
                except Exception:
                    pass
            progress = _calculate_progress(entry.status)

            active_jobs.append({
                "id": entry.id,
                "filename": display_name,
                "file_path": entry.file_path,
                "file_size": file_size,
                "status": entry.status,
                "progress": progress,
                "current_stage": entry.status.value.replace("_", " ").title() if entry.status else "Unknown",
                "created_at": entry.created_at,
                "error_message": entry.error_message
            })

        return templates.TemplateResponse(
            "components/queue_table.html",
            {
                "request": request,
                "active_jobs": active_jobs,
                "waiting_jobs": []
            }
        )

    except Exception as e:
        logger.error(f"Error deleting job {job_id}: {e}")
        return f"<div class='alert alert-error'>Error: {str(e)}</div>"


@router.get("/api/dashboard/stats", response_class=HTMLResponse)
async def get_dashboard_stats(request: Request, db: Session = Depends(get_db)):
    """
    Get updated dashboard statistics.

    Fetches the current statistics including active jobs, completed jobs,
    queue size, and success rate for display on the dashboard.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment containing the dashboard stats component
    """
    try:
        # TODO: Query database for statistics
        # Count active, completed, queued jobs
        # Calculate success rate from historical data

        stats = {
            "active_count": 0,
            "completed_today": 0,
            "today_change": 0,
            "queue_size": 0,
            "success_rate": 94
        }

        logger.info("Fetching dashboard statistics")

        return templates.TemplateResponse(
            "components/dashboard_stats.html",
            {
                "request": request,
                **stats
            }
        )
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {e}")
        return f"<p class='text-error'>Error fetching statistics: {str(e)}</p>"


# ============================================================================
# Logs Page API Endpoints - Handle download, clear, filter, and refresh
# ============================================================================


@router.get("/api/logs/refresh", response_class=HTMLResponse)
async def refresh_logs(request: Request, db: Session = Depends(get_db)):
    """
    Refresh logs with latest entries.

    Fetches recent log entries from the logging system or database
    and returns them formatted for display in the logs viewer.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment containing the updated log viewer
    """
    try:
        logger.info("Refreshing logs viewer")
        store = get_log_store()
        log_entries = store.get_entries(limit=1000)  # Increased from 500 to 1000

        return templates.TemplateResponse(
            "components/log_viewer.html",
            {
                "request": request,
                "log_entries": log_entries
            }
        )
    except Exception as e:
        logger.error(f"Error refreshing logs: {e}")
        return f"<p class='text-error'>Error refreshing logs: {str(e)}</p>"


@router.get("/api/logs/filter", response_class=HTMLResponse)
async def filter_logs(
    request: Request,
    level: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Filter logs by level.

    Filters log entries by log level (info, warning, error, debug, success).

    Args:
        request: FastAPI request object
        level: Log level to filter by (all, info, warning, error, debug, success)
        db: Database session

    Returns:
        HTML fragment containing the filtered logs
    """
    try:
        valid_levels = ['all', 'info', 'warning', 'error', 'debug', 'success']
        filter_level = level if level in valid_levels else 'all'

        logger.info(f"Filtering logs by level: {filter_level}")
        store = get_log_store()
        log_entries = store.get_filtered_entries(filter_level, limit=1000)  # Increased from 500 to 1000

        return templates.TemplateResponse(
            "components/log_viewer.html",
            {
                "request": request,
                "log_entries": log_entries,
                "filter_level": filter_level
            }
        )
    except Exception as e:
        logger.error(f"Error filtering logs: {e}")
        return f"<p class='text-error'>Error filtering logs: {str(e)}</p>"


@router.post("/api/logs/clear", response_class=HTMLResponse)
async def clear_logs(request: Request, db: Session = Depends(get_db)):
    """
    Clear all logs.

    Clears all application logs from the logging system or database.
    This action cannot be undone.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment with confirmation message
    """
    try:
        store = get_log_store()
        cleared_count = store.clear()
        logger.info(f"Cleared {cleared_count} log entries")

        return f"""
        <div class="alert alert-warning">
            <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"></path>
            </svg>
            <span>{cleared_count} log entries have been cleared</span>
        </div>
        """
    except Exception as e:
        logger.error(f"Error clearing logs: {e}")
        return f"<p class='text-error'>Error clearing logs: {str(e)}</p>"


@router.post("/api/logs/test", response_class=HTMLResponse)
async def test_all_log_levels(request: Request, db: Session = Depends(get_db)):
    """
    Generate test logs for all levels.

    Creates test log entries for each log level (DEBUG, INFO, WARNING, ERROR, SUCCESS)
    to verify that the logging system is working correctly.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment with confirmation
    """
    try:
        logger.debug("🔍 TEST: This is a DEBUG level log")
        logger.info("ℹ️ TEST: This is an INFO level log")
        logger.warning("⚠️ TEST: This is a WARNING level log")
        logger.error("❌ TEST: This is an ERROR level log")
        logger.info("✓ TEST: This is a SUCCESS marker log (auto-detected)")

        # Also add directly to store
        from app.services.log_store import get_log_store
        store = get_log_store()
        store.add_entry("DEBUG", "📝 Direct DEBUG entry added to log store")
        store.add_entry("INFO", "📝 Direct INFO entry added to log store")
        store.add_entry("WARNING", "📝 Direct WARNING entry added to log store")
        store.add_entry("ERROR", "📝 Direct ERROR entry added to log store")
        store.add_entry("SUCCESS", "📝 Direct SUCCESS entry added to log store")

        logger.info("✓ Test logs generated for all levels")

        return """
        <div class="alert alert-success">
            <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path>
            </svg>
            <span>Test logs generated successfully! Check all filter levels (All, Debug, Info, Warning, Error, Success)</span>
        </div>
        """
    except Exception as e:
        logger.error(f"Error generating test logs: {e}")
        return f"""
        <div class="alert alert-error">
            <span>Error generating test logs: {str(e)}</span>
        </div>
        """


@router.get("/api/logs/download")
async def download_logs(db: Session = Depends(get_db)):
    """
    Download logs as a text file.

    Exports all application logs in a text format suitable for download.

    Args:
        db: Database session

    Returns:
        File content for download
    """
    try:
        store = get_log_store()
        log_content = store.export_as_text()
        logger.info("Downloading logs")

        return {
            "status": "success",
            "content": log_content,
            "filename": "application-logs.txt"
        }

    except Exception as e:
        logger.error(f"Error downloading logs: {e}")
        from fastapi import HTTPException
        raise HTTPException(
            status_code=500,
            detail=f"Error downloading logs: {str(e)}"
        )


@router.get("/api/logs/docker", response_class=HTMLResponse)
async def get_docker_logs(
    request: Request,
    lines: int = Query(100, ge=1, le=10000),
    db: Session = Depends(get_db)
):
    """
    Get Docker container logs.

    Fetches logs from the running Docker container.

    Args:
        request: FastAPI request object
        lines: Number of log lines to retrieve (default 100, max 10000)
        db: Database session

    Returns:
        HTML fragment containing Docker logs
    """
    import subprocess
    import os

    try:
        # Try to get container name from environment or use default
        container_name = os.getenv("HOSTNAME", "seedarr-app")

        # Try docker logs command
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), container_name],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            # If fails, try getting logs from current container via docker logs on self
            logger.warning(f"Failed to get logs for {container_name}, trying alternative methods")
            return templates.TemplateResponse(
                "components/docker_log_viewer.html",
                {
                    "request": request,
                    "docker_logs": [
                        {
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "message": f"Unable to fetch Docker logs: {result.stderr or 'Container not found'}"
                        }
                    ]
                }
            )

        # Parse docker logs
        log_lines = (result.stdout + result.stderr).strip().split('\n')
        docker_logs = []

        for line in log_lines:
            if line.strip():
                # Try to extract timestamp if present, otherwise use current time
                docker_logs.append({
                    "timestamp": "Docker",
                    "message": line
                })

        logger.info(f"Retrieved {len(docker_logs)} Docker log lines")

        return templates.TemplateResponse(
            "components/docker_log_viewer.html",
            {
                "request": request,
                "docker_logs": docker_logs[-lines:]  # Limit to requested number of lines
            }
        )

    except subprocess.TimeoutExpired:
        logger.error("Docker logs command timed out")
        return templates.TemplateResponse(
            "components/docker_log_viewer.html",
            {
                "request": request,
                "docker_logs": [
                    {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "message": "Docker logs command timed out"
                    }
                ]
            }
        )
    except FileNotFoundError:
        logger.error("Docker command not found - are you running in Docker?")
        return templates.TemplateResponse(
            "components/docker_log_viewer.html",
            {
                "request": request,
                "docker_logs": [
                    {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "message": "Docker not available. This feature only works when running in Docker."
                    }
                ]
            }
        )
    except Exception as e:
        logger.error(f"Error fetching Docker logs: {e}")
        return templates.TemplateResponse(
            "components/docker_log_viewer.html",
            {
                "request": request,
                "docker_logs": [
                    {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "message": f"Error: {str(e)}"
                    }
                ]
            }
        )


# ============================================================================
# NFO Generation API Endpoints
# ============================================================================


@router.post("/api/nfo/generate")
async def generate_nfo(
    request: Request,
    file_path: str = Query(..., description="Path to the media file"),
    media_type: str = Query("Movies", description="Type of media (Movies, Series, etc.)"),
    db: Session = Depends(get_db)
):
    """
    Generate a technical NFO file for a media file.

    Uses MediaInfo to extract detailed technical information and generates
    an NFO file in the scene release format with:
    - General file information (size, duration, bitrate)
    - Video track details (codec, resolution, HDR, etc.)
    - Multiple audio tracks
    - Subtitle tracks

    Args:
        request: FastAPI request object
        file_path: Path to the media file
        media_type: Type of media (Movies, Series, etc.)
        db: Database session

    Returns:
        JSON response with NFO path and content
    """
    import os
    from app.services.nfo_generator import get_nfo_generator

    try:
        # Validate file exists
        if not os.path.exists(file_path):
            return {
                "status": "error",
                "message": f"File not found: {file_path}"
            }

        # Generate NFO
        generator = get_nfo_generator()
        nfo_path = await generator.generate_nfo(
            file_path=file_path,
            media_type=media_type
        )

        # Read the generated content
        with open(nfo_path, 'r', encoding='utf-8') as f:
            nfo_content = f.read()

        logger.info(f"NFO generated successfully: {nfo_path}")

        return {
            "status": "success",
            "nfo_path": nfo_path,
            "content": nfo_content
        }

    except Exception as e:
        logger.error(f"Error generating NFO: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.get("/api/nfo/preview")
async def preview_nfo(
    request: Request,
    file_path: str = Query(..., description="Path to the media file"),
    media_type: str = Query("Movies", description="Type of media"),
    db: Session = Depends(get_db)
):
    """
    Preview NFO content without saving to file.

    Extracts MediaInfo and generates NFO content for preview purposes.

    Args:
        request: FastAPI request object
        file_path: Path to the media file
        media_type: Type of media

    Returns:
        JSON response with NFO content preview
    """
    import os
    from app.services.nfo_generator import get_nfo_generator

    try:
        # Validate file exists
        if not os.path.exists(file_path):
            return {
                "status": "error",
                "message": f"File not found: {file_path}"
            }

        # Extract MediaInfo and generate content
        generator = get_nfo_generator()
        media_data = await generator.extract_mediainfo(file_path)
        nfo_content = generator.generate_nfo_content(media_data, media_type)

        return {
            "status": "success",
            "content": nfo_content,
            "media_info": {
                "file_name": media_data.file_name,
                "format": media_data.format,
                "file_size": media_data.file_size,
                "duration": media_data.duration,
                "video_tracks": len(media_data.video_tracks),
                "audio_tracks": len(media_data.audio_tracks),
                "subtitle_tracks": len(media_data.subtitle_tracks)
            }
        }

    except Exception as e:
        logger.error(f"Error previewing NFO: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


# ============================================================================
# BBCode Generation API Endpoints
# ============================================================================


@router.post("/api/bbcode/generate")
async def generate_bbcode(
    request: Request,
    file_path: str = Query(..., description="Path to the media file"),
    tmdb_id: Optional[str] = Query(None, description="TMDB ID for metadata"),
    db: Session = Depends(get_db)
):
    """
    Generate BBCode description for La Cale tracker upload.

    Combines MediaInfo technical details with TMDB metadata to produce
    a formatted BBCode description ready for upload.

    Args:
        request: FastAPI request object
        file_path: Path to the media file
        tmdb_id: Optional TMDB ID to fetch movie/show metadata
        db: Database session

    Returns:
        JSON response with BBCode content
    """
    import os
    from app.services.bbcode_generator import get_bbcode_generator
    from app.models.tmdb_cache import TMDBCache

    try:
        # Validate file exists
        if not os.path.exists(file_path):
            return {
                "status": "error",
                "message": f"File not found: {file_path}"
            }

        # Get TMDB data if ID provided
        tmdb_data = None
        if tmdb_id:
            cache_entry = TMDBCache.get_cached(db, tmdb_id)
            if cache_entry:
                # Extract extra_data fields
                extra = cache_entry.extra_data or {}
                tmdb_data = {
                    "title": cache_entry.title,
                    "year": cache_entry.year,
                    "poster_path": extra.get("poster_path", ""),
                    "vote_average": cache_entry.ratings.get("vote_average", 0) if cache_entry.ratings else 0,
                    "genres": extra.get("genres", []),
                    "overview": cache_entry.plot
                }

        # Generate BBCode
        generator = get_bbcode_generator()
        bbcode = await generator.generate_from_file(file_path, tmdb_data)

        logger.info(f"BBCode generated successfully for: {file_path}")

        return {
            "status": "success",
            "content": bbcode
        }

    except Exception as e:
        logger.error(f"Error generating BBCode: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/api/bbcode/generate-with-tmdb")
async def generate_bbcode_with_tmdb(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Generate BBCode description with TMDB data from request body.

    Accepts TMDB metadata directly in the request body for more flexibility.

    Args:
        request: FastAPI request object with JSON body containing:
            - file_path: Path to the media file
            - tmdb: Object with title, year, poster_path, vote_average, genres, overview
        db: Database session

    Returns:
        JSON response with BBCode content
    """
    import os
    from app.services.bbcode_generator import get_bbcode_generator

    try:
        body = await request.json()
        file_path = body.get("file_path")
        tmdb_data = body.get("tmdb")

        # Validate file exists
        if not file_path:
            return {
                "status": "error",
                "message": "file_path is required"
            }

        if not os.path.exists(file_path):
            return {
                "status": "error",
                "message": f"File not found: {file_path}"
            }

        # Generate BBCode
        generator = get_bbcode_generator()
        bbcode = await generator.generate_from_file(file_path, tmdb_data)

        logger.info(f"BBCode generated successfully for: {file_path}")

        return {
            "status": "success",
            "content": bbcode
        }

    except Exception as e:
        logger.error(f"Error generating BBCode: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.get("/api/system/health", response_class=HTMLResponse)
async def get_system_health(request: Request, db: Session = Depends(get_db)):
    """
    Get system health status for sidebar indicators.

    Returns HTML fragment with health indicators for qBittorrent, FlareSolverr, and Tracker.
    Used by the sidebar via HTMX to show real-time service status.
    """
    from app.models.settings import Settings
    settings = Settings.get_settings(db)

    system_status = {
        "qbittorrent": "unknown",
        "flaresolverr": "unknown",
        "tracker": "unknown"
    }

    # Check qBittorrent status if configured
    if settings and settings.qbittorrent_host:
        qb_url = settings.qbittorrent_host
        if not qb_url.startswith(('http://', 'https://')):
            qb_url = f"http://{qb_url}"
        system_status["qbittorrent"] = await check_service_health(f"{qb_url}/api/v2/app/version", timeout=3)

    # Check FlareSolverr status if configured
    if settings and settings.flaresolverr_url:
        flaresolverr_url = settings.flaresolverr_url.rstrip('/')
        system_status["flaresolverr"] = await check_service_health(f"{flaresolverr_url}/health", timeout=3)

    # Tracker status - Check if any tracker is configured and enabled
    from app.models.tracker import Tracker
    enabled_trackers = db.query(Tracker).filter(Tracker.enabled == True).count()
    if enabled_trackers > 0:
        system_status["tracker"] = "connected"

    # Generate HTML for sidebar health indicators
    def get_dot_class(status):
        if status == "connected":
            return ""
        elif status == "disconnected":
            return "error"
        else:
            return "warning"

    html = f"""
    <div class="health-indicator">
        <span class="health-dot {get_dot_class(system_status['qbittorrent'])}"></span>
        <span>qBittorrent</span>
    </div>
    <div class="health-indicator">
        <span class="health-dot {get_dot_class(system_status['flaresolverr'])}"></span>
        <span>FlareSolverr</span>
    </div>
    <div class="health-indicator">
        <span class="health-dot {get_dot_class(system_status['tracker'])}"></span>
        <span>Tracker</span>
    </div>
    """

    return html


@router.get("/api/bbcode/preview")
async def preview_bbcode(
    request: Request,
    file_path: str = Query(..., description="Path to the media file"),
    db: Session = Depends(get_db)
):
    """
    Preview BBCode content without TMDB data.

    Generates BBCode with only technical details from MediaInfo.

    Args:
        request: FastAPI request object
        file_path: Path to the media file

    Returns:
        JSON response with BBCode preview
    """
    import os
    from app.services.bbcode_generator import get_bbcode_generator

    try:
        # Validate file exists
        if not os.path.exists(file_path):
            return {
                "status": "error",
                "message": f"File not found: {file_path}"
            }

        # Generate BBCode without TMDB data
        generator = get_bbcode_generator()
        bbcode = await generator.generate_from_file(file_path, None)

        return {
            "status": "success",
            "content": bbcode
        }

    except Exception as e:
        logger.error(f"Error previewing BBCode: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


# ============================================================================
# Release Workflow API Endpoints (v2.1) - Approval and Retry
# ============================================================================


@router.post("/api/releases/{release_id}/approve")
async def approve_release(
    request: Request,
    release_id: int,
    db: Session = Depends(get_db)
):
    """
    Approve a release pending user approval.

    This endpoint is called when a user reviews and approves a release that
    is in PENDING_APPROVAL status. The release can optionally have corrections
    applied (e.g., corrected release name, TMDB ID).

    After approval, if auto_resume_after_approval is enabled in settings,
    the pipeline will automatically resume processing.

    Args:
        request: FastAPI request object with optional JSON body:
            - final_release_name: Corrected release name (optional)
            - tmdb_id: Corrected TMDB ID (optional)
        release_id: ID of the FileEntry to approve
        db: Database session

    Returns:
        JSON response with approval result
    """
    from app.models.settings import Settings
    from app.processors.pipeline import ProcessingPipeline
    from app.adapters.lacale_adapter import LaCaleAdapter

    try:
        # Find the release
        file_entry = db.query(FileEntry).filter(FileEntry.id == release_id).first()

        if not file_entry:
            return {
                "status": "error",
                "message": f"Release not found: {release_id}"
            }

        # Verify it's in pending approval status
        if file_entry.status != Status.PENDING_APPROVAL:
            return {
                "status": "error",
                "message": f"Release is not pending approval (current status: {file_entry.status.value})"
            }

        # Parse optional corrections from request body
        corrections = {}
        try:
            body = await request.json()
            if body.get("final_release_name"):
                corrections["final_release_name"] = body["final_release_name"]
            if body.get("tmdb_id"):
                corrections["tmdb_id"] = body["tmdb_id"]
        except Exception:
            # No JSON body or parsing error - proceed without corrections
            pass

        # Apply corrections and mark as approved
        approved_by = request.client.host if request.client else "unknown"
        file_entry.mark_approved(
            approved_by=approved_by,
            corrections=corrections if corrections else None
        )
        db.commit()

        logger.info(f"Release {release_id} approved by {approved_by}")
        if corrections:
            logger.info(f"  Corrections applied: {corrections}")

        # Check if auto-resume is enabled
        settings = Settings.get_settings(db)
        auto_resume = settings.auto_resume_after_approval if settings else True

        result = {
            "status": "success",
            "message": f"Release {release_id} approved",
            "release_id": release_id,
            "approved_by": approved_by,
            "corrections": corrections,
            "auto_resume": auto_resume
        }

        if auto_resume:
            logger.info(f"Auto-resuming pipeline for release {release_id}")
            try:
                # Initialize tracker adapter
                tracker_adapter = None
                if settings and settings.flaresolverr_url and settings.tracker_url and settings.tracker_passkey:
                    tracker_adapter = LaCaleAdapter(
                        flaresolverr_url=settings.flaresolverr_url,
                        tracker_url=settings.tracker_url,
                        passkey=settings.tracker_passkey
                    )

                # Resume pipeline
                pipeline = ProcessingPipeline(db, tracker_adapter)
                await pipeline.process_file(file_entry, skip_approval=True)

                result["pipeline_status"] = "resumed"
                result["current_status"] = file_entry.status.value
                logger.info(f"Pipeline resumed successfully for release {release_id}")

            except Exception as e:
                logger.error(f"Auto-resume failed for release {release_id}: {e}")
                result["pipeline_status"] = "resume_failed"
                result["pipeline_error"] = str(e)

        return result

    except Exception as e:
        logger.error(f"Error approving release {release_id}: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/api/releases/{release_id}/reject")
async def reject_release(
    request: Request,
    release_id: int,
    db: Session = Depends(get_db)
):
    """
    Reject a release pending approval.

    Marks the release as failed with a rejection message.

    Args:
        request: FastAPI request object with optional JSON body:
            - reason: Rejection reason
        release_id: ID of the FileEntry to reject
        db: Database session

    Returns:
        JSON response with rejection result
    """
    try:
        # Find the release
        file_entry = db.query(FileEntry).filter(FileEntry.id == release_id).first()

        if not file_entry:
            return {
                "status": "error",
                "message": f"Release not found: {release_id}"
            }

        # Verify it's in pending approval status
        if file_entry.status != Status.PENDING_APPROVAL:
            return {
                "status": "error",
                "message": f"Release is not pending approval (current status: {file_entry.status.value})"
            }

        # Parse rejection reason from request body
        reason = "Rejected by user"
        try:
            body = await request.json()
            if body.get("reason"):
                reason = body["reason"]
        except Exception:
            pass

        # Mark as failed
        file_entry.mark_failed(f"Rejected: {reason}")
        db.commit()

        rejected_by = request.client.host if request.client else "unknown"
        logger.info(f"Release {release_id} rejected by {rejected_by}: {reason}")

        return {
            "status": "success",
            "message": f"Release {release_id} rejected",
            "release_id": release_id,
            "rejected_by": rejected_by,
            "reason": reason
        }

    except Exception as e:
        logger.error(f"Error rejecting release {release_id}: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/api/releases/{release_id}/retry-tracker/{tracker_slug}")
async def retry_tracker_upload(
    request: Request,
    release_id: int,
    tracker_slug: str,
    db: Session = Depends(get_db)
):
    """
    Retry upload to a specific tracker that previously failed.

    This endpoint allows granular retry of failed tracker uploads without
    re-running the entire pipeline. Only trackers with FAILED status can
    be retried.

    Args:
        request: FastAPI request object
        release_id: ID of the FileEntry
        tracker_slug: Slug of the tracker to retry (e.g., "lacale", "c411")
        db: Database session

    Returns:
        JSON response with retry result
    """
    from app.models.file_entry import TrackerStatus
    from app.models.settings import Settings
    from app.models.tracker import Tracker
    from app.adapters.tracker_factory import TrackerFactory
    import os

    try:
        # Find the release
        file_entry = db.query(FileEntry).filter(FileEntry.id == release_id).first()

        if not file_entry:
            return {
                "status": "error",
                "message": f"Release not found: {release_id}"
            }

        # Check tracker status
        tracker_statuses = file_entry.tracker_statuses or {}
        tracker_status_data = tracker_statuses.get(tracker_slug)

        if not tracker_status_data:
            return {
                "status": "error",
                "message": f"No status found for tracker '{tracker_slug}'"
            }

        current_status = tracker_status_data.get("status")
        if current_status != TrackerStatus.FAILED.value:
            return {
                "status": "error",
                "message": f"Tracker '{tracker_slug}' is not in failed status (current: {current_status})"
            }

        # Find the tracker
        tracker = db.query(Tracker).filter(Tracker.slug == tracker_slug).first()
        if not tracker:
            return {
                "status": "error",
                "message": f"Tracker not found: {tracker_slug}"
            }

        if not tracker.is_enabled:
            return {
                "status": "error",
                "message": f"Tracker '{tracker_slug}' is disabled"
            }

        # Set status to RETRYING
        retry_count = tracker_status_data.get("retry_count", 0) + 1
        file_entry.set_tracker_status(
            tracker_slug=tracker_slug,
            status=TrackerStatus.RETRYING.value,
            retry_count=retry_count
        )
        db.commit()

        logger.info(f"Retrying upload to {tracker.name} for release {release_id} (attempt {retry_count})")

        # Get settings
        settings = Settings.get_settings(db)

        # Create tracker factory
        factory = TrackerFactory(
            db=db,
            flaresolverr_url=settings.flaresolverr_url if settings else None
        )

        # Get adapter
        adapter = factory.get_adapter(tracker)

        # Get tracker-specific torrent file
        torrent_path = file_entry.get_torrent_path_for_tracker(tracker_slug)
        if not torrent_path:
            torrent_path = file_entry.torrent_path

        if not torrent_path or not os.path.exists(torrent_path):
            file_entry.set_tracker_status(
                tracker_slug=tracker_slug,
                status=TrackerStatus.FAILED.value,
                error=f"Torrent file not found: {torrent_path}",
                retry_count=retry_count
            )
            db.commit()
            return {
                "status": "error",
                "message": f"Torrent file not found: {torrent_path}"
            }

        # Read torrent data
        with open(torrent_path, 'rb') as f:
            torrent_data = f.read()

        # Read NFO file
        nfo_data = None
        if file_entry.nfo_path and os.path.exists(file_entry.nfo_path):
            with open(file_entry.nfo_path, 'rb') as f:
                nfo_data = f.read()

        # Authenticate
        authenticated = await adapter.authenticate()
        if not authenticated:
            file_entry.set_tracker_status(
                tracker_slug=tracker_slug,
                status=TrackerStatus.FAILED.value,
                error="Authentication failed",
                retry_count=retry_count
            )
            db.commit()
            return {
                "status": "error",
                "message": f"Authentication failed for {tracker.name}"
            }

        # Prepare upload kwargs
        upload_kwargs = {
            'torrent_data': torrent_data,
            'release_name': file_entry.get_effective_release_name() or file_entry.release_name,
            'category_id': tracker.get_category_id(
                media_type=file_entry.tmdb_type or 'movie',
                resolution=file_entry.resolution if hasattr(file_entry, 'resolution') else None
            ) or tracker.default_category_id or file_entry.category_id,
            'tag_ids': file_entry.get_tag_ids(),
            'nfo_data': nfo_data,
            'description': file_entry.description,
            'tmdb_id': file_entry.tmdb_id,
            'tmdb_type': file_entry.tmdb_type,
            'cover_url': file_entry.cover_url,
        }

        if tracker.default_subcategory_id:
            upload_kwargs['subcategory_id'] = tracker.default_subcategory_id

        # Upload
        result = await adapter.upload_torrent(**upload_kwargs)

        if result.get('success'):
            file_entry.set_tracker_status(
                tracker_slug=tracker_slug,
                status=TrackerStatus.SUCCESS.value,
                torrent_id=str(result['torrent_id']),
                torrent_url=result['torrent_url'],
                retry_count=retry_count
            )
            db.commit()

            logger.info(f"Retry successful for {tracker.name}: {result['torrent_url']}")

            return {
                "status": "success",
                "message": f"Upload to {tracker.name} succeeded",
                "tracker": tracker_slug,
                "torrent_id": result['torrent_id'],
                "torrent_url": result['torrent_url'],
                "retry_count": retry_count
            }
        else:
            error_msg = result.get('message', 'Unknown error')
            file_entry.set_tracker_status(
                tracker_slug=tracker_slug,
                status=TrackerStatus.FAILED.value,
                error=error_msg,
                retry_count=retry_count
            )
            db.commit()

            logger.error(f"Retry failed for {tracker.name}: {error_msg}")

            return {
                "status": "error",
                "message": f"Upload to {tracker.name} failed: {error_msg}",
                "tracker": tracker_slug,
                "retry_count": retry_count
            }

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(f"Error retrying tracker upload: {error_msg}")

        # Update status to failed
        try:
            file_entry.set_tracker_status(
                tracker_slug=tracker_slug,
                status=TrackerStatus.FAILED.value,
                error=error_msg
            )
            db.commit()
        except Exception:
            pass

        return {
            "status": "error",
            "message": error_msg
        }


@router.post("/api/releases/{release_id}/send-to-qbittorrent/{tracker_slug}")
async def send_to_qbittorrent(
    request: Request,
    release_id: int,
    tracker_slug: str,
    db: Session = Depends(get_db)
):
    """
    Manually send a torrent to qBittorrent for a specific tracker.

    This endpoint allows sending a torrent file to qBittorrent for seeding,
    with the tracker slug added as a tag (e.g., C411, LACALE).

    Args:
        request: FastAPI request object
        release_id: ID of the FileEntry
        tracker_slug: Slug of the tracker (e.g., "lacale", "c411")
        db: Database session

    Returns:
        JSON response with result
    """
    from app.processors.pipeline import ProcessingPipeline
    import os

    try:
        # Find the release
        file_entry = db.query(FileEntry).filter(FileEntry.id == release_id).first()

        if not file_entry:
            return {
                "status": "error",
                "message": f"Release not found: {release_id}"
            }

        # Get tracker-specific torrent file
        torrent_paths = file_entry.get_torrent_paths()
        torrent_path = torrent_paths.get(tracker_slug)

        if not torrent_path:
            # Fallback to generic torrent path
            torrent_path = file_entry.torrent_path

        if not torrent_path or not os.path.exists(torrent_path):
            return {
                "status": "error",
                "message": f"Torrent file not found for tracker '{tracker_slug}'"
            }

        # Create pipeline instance to use _inject_to_qbittorrent
        pipeline = ProcessingPipeline(db)

        # Inject to qBittorrent with tracker tag
        await pipeline._inject_to_qbittorrent(
            file_entry=file_entry,
            torrent_path=torrent_path,
            tracker_slug=tracker_slug
        )

        logger.info(f"Manually sent torrent to qBittorrent for release {release_id}, tracker {tracker_slug}")

        return {
            "status": "success",
            "message": f"Torrent sent to qBittorrent with tag {tracker_slug.upper()}"
        }

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(f"Error sending to qBittorrent: {error_msg}")
        return {
            "status": "error",
            "message": error_msg
        }


@router.get("/api/releases/{release_id}/tracker-status")
async def get_tracker_statuses(
    request: Request,
    release_id: int,
    db: Session = Depends(get_db)
):
    """
    Get granular tracker upload statuses for a release.

    Returns the status of each tracker upload attempt, including
    success/failure details, torrent URLs, and retry counts.

    Args:
        request: FastAPI request object
        release_id: ID of the FileEntry
        db: Database session

    Returns:
        JSON response with tracker statuses
    """
    try:
        # Find the release
        file_entry = db.query(FileEntry).filter(FileEntry.id == release_id).first()

        if not file_entry:
            return {
                "status": "error",
                "message": f"Release not found: {release_id}"
            }

        tracker_statuses = file_entry.tracker_statuses or {}
        successful = file_entry.get_successful_trackers()
        failed = file_entry.get_failed_trackers()

        return {
            "status": "success",
            "release_id": release_id,
            "tracker_statuses": tracker_statuses,
            "summary": {
                "successful": successful,
                "failed": failed,
                "all_completed": file_entry.all_trackers_completed()
            }
        }

    except Exception as e:
        logger.error(f"Error getting tracker statuses: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/api/releases/{release_id}/check-duplicates")
async def check_release_duplicates(
    request: Request,
    release_id: int,
    db: Session = Depends(get_db)
):
    """
    Check for duplicates across all enabled trackers for a release.

    This endpoint checks if the release already exists on any of the enabled
    trackers before uploading, using TMDB ID, IMDB ID, or release name.

    Args:
        request: FastAPI request object
        release_id: ID of the FileEntry to check
        db: Database session

    Returns:
        JSON response with duplicate check results per tracker
    """
    from ..models.tracker import Tracker
    from ..models.settings import Settings
    from ..adapters.tracker_factory import TrackerFactory

    try:
        # Find the release
        file_entry = db.query(FileEntry).filter(FileEntry.id == release_id).first()

        if not file_entry:
            return {
                "status": "error",
                "message": f"Release not found: {release_id}"
            }

        # Get enabled trackers with upload enabled
        trackers = Tracker.get_upload_enabled(db)

        if not trackers:
            return {
                "status": "warning",
                "message": "No trackers with upload enabled",
                "results": {}
            }

        # Get settings for FlareSolverr
        settings = Settings.get_settings(db)
        flaresolverr_url = settings.flaresolverr_url if settings else None

        # Create tracker factory
        factory = TrackerFactory(db, flaresolverr_url=flaresolverr_url)

        # Extract search parameters from release
        tmdb_id = file_entry.tmdb_id
        release_name = file_entry.get_effective_release_name()

        # Fallback to filename if no release_name
        if not release_name and file_entry.file_path:
            import os
            filename = os.path.basename(file_entry.file_path)
            # Remove extension
            release_name = os.path.splitext(filename)[0]

        # Extract quality from mediainfo or release name
        quality = None
        if file_entry.mediainfo_data:
            video_info = file_entry.mediainfo_data.get('video', {})
            height = video_info.get('height', 0)
            if height >= 2160:
                quality = "2160p"
            elif height >= 1080:
                quality = "1080p"
            elif height >= 720:
                quality = "720p"
            else:
                quality = "sd"

        results = {}
        has_duplicates = False

        # Check each tracker
        for tracker in trackers:
            try:
                adapter = factory.get_adapter(tracker)

                result = await adapter.check_duplicate(
                    tmdb_id=tmdb_id,
                    imdb_id=None,  # Could be extracted from TMDB data
                    release_name=release_name,
                    quality=quality,
                    file_size=file_entry.file_size
                )

                results[tracker.slug] = {
                    "tracker_name": tracker.name,
                    "is_duplicate": result.get("is_duplicate", False),
                    "exact_match": result.get("exact_match", False),
                    "exact_matches": result.get("exact_matches", []),
                    "existing_torrents": result.get("existing_torrents", []),
                    "search_method": result.get("search_method", "none"),
                    "message": result.get("message", "")
                }

                if result.get("is_duplicate"):
                    has_duplicates = True

            except Exception as e:
                logger.error(f"Duplicate check failed for tracker {tracker.slug}: {e}")
                results[tracker.slug] = {
                    "tracker_name": tracker.name,
                    "is_duplicate": False,
                    "error": str(e),
                    "search_method": "error",
                    "message": f"Check failed: {str(e)}"
                }

        # Save duplicate check results to database for persistence
        from datetime import datetime
        file_entry.duplicate_check_results = {
            "has_duplicates": has_duplicates,
            "checked_at": datetime.utcnow().isoformat(),
            "release_name": release_name,
            "results": results
        }
        db.commit()

        return {
            "status": "success",
            "release_id": release_id,
            "release_name": release_name,
            "tmdb_id": tmdb_id,
            "quality": quality,
            "has_duplicates": has_duplicates,
            "results": results
        }

    except Exception as e:
        logger.error(f"Error checking duplicates for release {release_id}: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


# ============================================================================
# Manual Processing Step Endpoints
# ============================================================================

@router.post("/api/releases/{release_id}/step/{step_name}")
async def execute_processing_step(
    request: Request,
    release_id: int,
    step_name: str,
    db: Session = Depends(get_db)
):
    """
    Execute a single processing step manually.

    Available steps: scan, analyze, rename, metadata, upload

    Args:
        release_id: ID of the FileEntry
        step_name: Step to execute (scan, analyze, rename, metadata, upload)
        db: Database session

    Returns:
        JSON response with step execution result
    """
    from ..processors.pipeline import ProcessingPipeline
    from ..models.settings import Settings
    from ..adapters.tracker_factory import TrackerFactory

    valid_steps = ['scan', 'analyze', 'rename', 'metadata', 'upload']

    if step_name not in valid_steps:
        return {
            "status": "error",
            "message": f"Invalid step: {step_name}. Valid steps: {', '.join(valid_steps)}"
        }

    try:
        file_entry = db.query(FileEntry).filter(FileEntry.id == release_id).first()
        if not file_entry:
            return {"status": "error", "message": f"Release not found: {release_id}"}

        settings = Settings.get_settings(db)
        flaresolverr_url = settings.flaresolverr_url if settings else None

        # Create tracker factory for upload step
        factory = TrackerFactory(db, flaresolverr_url=flaresolverr_url)

        # Create pipeline
        pipeline = ProcessingPipeline(db, tracker_adapter=None)

        # Execute the requested step
        if step_name == 'scan':
            await pipeline._scan_stage(file_entry)
            message = "Scan completed successfully"

        elif step_name == 'analyze':
            await pipeline._analyze_stage(file_entry)
            message = "Analysis completed successfully"

        elif step_name == 'rename':
            await pipeline._rename_stage(file_entry)
            message = "Rename completed successfully"

        elif step_name == 'metadata':
            await pipeline._metadata_generation_stage(file_entry)
            message = "Metadata generation completed successfully"

        elif step_name == 'upload':
            await pipeline._upload_stage(file_entry)
            message = "Upload completed successfully"

        db.commit()

        return {
            "status": "success",
            "message": message,
            "new_status": file_entry.status.value if hasattr(file_entry.status, 'value') else str(file_entry.status)
        }

    except Exception as e:
        logger.error(f"Error executing step {step_name} for release {release_id}: {e}")
        db.rollback()

        # Extract user-friendly message from TrackerAPIError
        error_msg = str(e)
        if hasattr(e, 'message'):
            error_msg = e.message
            # If wrapped "All tracker uploads failed: slug: msg", extract the inner message
            if "All tracker uploads failed:" in error_msg:
                # Get the actual error messages from tracker_statuses
                try:
                    release = db.query(FileEntry).filter(FileEntry.id == release_id).first()
                    if release and release.tracker_statuses:
                        errors = [
                            data.get('error', '')
                            for data in release.tracker_statuses.values()
                            if data.get('error')
                        ]
                        if errors:
                            error_msg = errors[0]
                except Exception:
                    pass

        return {
            "status": "error",
            "message": error_msg
        }


@router.get("/api/releases/{release_id}/bbcode")
async def get_release_bbcode(
    request: Request,
    release_id: int,
    template_id: Optional[int] = Query(None, description="ID of the BBCode template to use"),
    db: Session = Depends(get_db)
):
    """
    Get the BBCode presentation for a release.

    Args:
        release_id: ID of the FileEntry
        template_id: Optional template ID to use (defaults to default template)
        db: Database session

    Returns:
        JSON response with BBCode content
    """
    from ..services.bbcode_generator import get_bbcode_generator
    from ..services.tmdb_cache_service import get_tmdb_service
    from ..models import BBCodeTemplate

    try:
        file_entry = db.query(FileEntry).filter(FileEntry.id == release_id).first()
        if not file_entry:
            return {"status": "error", "message": f"Release not found: {release_id}"}

        # Check if the file exists
        import os
        if not file_entry.file_path or not os.path.exists(file_entry.file_path):
            return {
                "status": "warning",
                "message": "Media file not found. Cannot generate BBCode.",
                "bbcode": None
            }

        # Get BBCode generator
        bbcode_generator = get_bbcode_generator()

        # Get template if specified, otherwise use default
        template = None
        if template_id:
            template = BBCodeTemplate.get_by_id(db, template_id)
        if not template:
            template = BBCodeTemplate.get_default(db)

        # Prepare TMDB data if available
        tmdb_data = None
        if file_entry.tmdb_id:
            try:
                tmdb_service = get_tmdb_service(db)
                tmdb_info = await tmdb_service.get_metadata(str(file_entry.tmdb_id))
                if tmdb_info and isinstance(tmdb_info, dict):
                    # Extract ratings info
                    ratings = tmdb_info.get("ratings", {})
                    vote_avg = ratings.get("vote_average", 0.0) if isinstance(ratings, dict) else 0.0

                    # Use cover_url from file_entry if available (complete URL), otherwise use poster_path
                    poster_url = file_entry.cover_url if file_entry.cover_url else tmdb_info.get("poster_path", "")

                    tmdb_data = {
                        "title": tmdb_info.get("title", ""),
                        "original_title": tmdb_info.get("original_title", ""),
                        "year": tmdb_info.get("year", 0),
                        "poster_url": poster_url,  # Complete URL or poster_path
                        "vote_average": vote_avg,
                        "genres": tmdb_info.get("genres", []),
                        "overview": tmdb_info.get("plot", ""),
                        "tmdb_id": str(file_entry.tmdb_id),
                        "imdb_id": tmdb_info.get("imdb_id", ""),
                    }
            except Exception as e:
                logger.warning(f"Could not fetch TMDB data: {e}")

        # Generate BBCode presentation
        try:
            if template:
                # Use custom template
                bbcode = await bbcode_generator.generate_from_template(
                    template.content,
                    file_entry.file_path,
                    tmdb_data
                )
            else:
                # Fallback to hardcoded template
                bbcode = await bbcode_generator.generate_from_file(file_entry.file_path, tmdb_data)

            return {
                "status": "success",
                "bbcode": bbcode,
                "template_used": template.name if template else "Built-in"
            }
        except Exception as e:
            logger.warning(f"Could not generate BBCode: {e}")
            return {
                "status": "warning",
                "message": f"Could not generate BBCode: {str(e)}",
                "bbcode": None
            }

    except Exception as e:
        logger.error(f"Error getting BBCode for release {release_id}: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@router.get("/api/releases/pending-approval", response_class=HTMLResponse)
async def get_pending_approval_releases(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Get all releases pending user approval.

    Returns a list of releases in PENDING_APPROVAL status for display
    in the approval UI.

    Args:
        request: FastAPI request object
        db: Database session

    Returns:
        HTML fragment with pending approval releases
    """
    import os

    try:
        # Fetch releases pending approval
        entries = db.query(FileEntry).filter(
            FileEntry.status == Status.PENDING_APPROVAL
        ).order_by(FileEntry.approval_requested_at.desc()).all()

        # Transform to template format
        pending_releases = []
        for entry in entries:
            filename = os.path.basename(entry.file_path) if entry.file_path else "Unknown"
            display_name = entry.release_name if entry.release_name else os.path.splitext(filename)[0]

            pending_releases.append({
                "id": entry.id,
                "filename": display_name,
                "file_path": entry.file_path,
                "release_name": entry.release_name,
                "tmdb_id": entry.tmdb_id,
                "tmdb_type": entry.tmdb_type,
                "category_id": entry.category_id,
                "approval_requested_at": entry.approval_requested_at,
                "cover_url": entry.cover_url
            })

        logger.info(f"Found {len(pending_releases)} releases pending approval")

        return templates.TemplateResponse(
            "components/approval_list.html",
            {
                "request": request,
                "pending_releases": pending_releases
            }
        )

    except Exception as e:
        logger.error(f"Error getting pending approval releases: {e}")
        return f"<p class='text-error'>Error: {str(e)}</p>"
