"""
Presentation Generator API Routes for Seedarr v2.0

This module provides endpoints for generating BBCode presentations
from TMDB metadata and MediaInfo data, designed for quick one-off
presentations without database storage.

Features:
    - GET /presentations: Render presentation generator UI
    - GET /api/presentations/search: Autocomplete TMDB movie search
    - POST /api/presentations/generate: Generate BBCode from template
    - GET /api/presentations/browse: File browser for media files
"""

import logging
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.database import get_db
from app.models import BBCodeTemplate, Settings
from app.services.tmdb_cache_service import TMDBCacheService
from app.services.bbcode_generator import get_bbcode_generator, normalize_genres
from app.services.exceptions import TrackerAPIError
from app.services.nfo_generator import get_nfo_generator

logger = logging.getLogger(__name__)

router = APIRouter()
# Use relative path when running from backend directory
templates_dir = "templates" if os.path.exists("templates") else "backend/templates"
templates = Jinja2Templates(directory=templates_dir)


# Pydantic models for request/response
class GeneratePresentationRequest(BaseModel):
    """Request model for generating a presentation."""
    tmdb_id: str = Field(..., description="TMDB movie ID")
    template_id: int = Field(..., description="BBCode template ID")
    file_path: str = Field(..., description="Path to media file for MediaInfo extraction")


# ============== Page Routes ==============

@router.get("/presentations", response_class=HTMLResponse)
async def presentations_page(request: Request, db: Session = Depends(get_db)):
    """Render the presentation generator page."""
    # Get all available templates
    all_templates = BBCodeTemplate.get_all(db)

    # Get settings for file browser
    settings = Settings.get_settings(db)

    return templates.TemplateResponse(
        "presentations.html",
        {
            "request": request,
            "templates": all_templates,
            "settings": settings,
        }
    )


# ============== API Routes ==============

@router.get("/api/presentations/search")
async def search_movies(
    query: str = Query(..., min_length=2, max_length=100),
    db: Session = Depends(get_db)
):
    """
    Search TMDB for movies by title with autocomplete results.

    Args:
        query: Movie title to search (min 2 chars)

    Returns:
        List of movies with basic info (id, title, year, poster)
    """
    if not query or len(query.strip()) < 2:
        return {
            "status": "error",
            "message": "Query must be at least 2 characters",
            "results": []
        }

    try:
        # Create TMDB service
        tmdb_service = TMDBCacheService(db)

        # Search for movies
        results = await tmdb_service.search_movies_autocomplete(query.strip(), limit=8)

        # Add full poster URLs
        for movie in results:
            if movie['poster_path']:
                movie['poster_url'] = f"https://image.tmdb.org/t/p/w500{movie['poster_path']}"
            else:
                movie['poster_url'] = "https://via.placeholder.com/500x750?text=No+Poster"

        logger.info(f"Search '{query}' returned {len(results)} results")

        return {
            "status": "success",
            "results": results
        }

    except TrackerAPIError as e:
        # TMDB API key not configured or authentication error
        logger.warning(f"TMDB API error during search: {e}")
        return {
            "status": "error",
            "message": f"Configuration TMDB manquante: {str(e)}",
            "results": []
        }
    except Exception as e:
        logger.error(f"Error searching TMDB: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Erreur de recherche: {str(e)}",
            "results": []
        }


@router.get("/api/presentations/suggest-movies")
async def suggest_movies(
    filename: str = Query(..., description="Filename to extract movie title from"),
    db: Session = Depends(get_db)
):
    """
    Suggest movies based on filename parsing.
    Extracts title and year from filename, then searches TMDB.

    Returns list of movies on success, or error object with details.
    """
    import re

    try:
        # Check TMDB API key is configured
        settings = Settings.get_settings(db)
        if not settings or not settings.tmdb_api_key:
            logger.warning("TMDB API key not configured in settings")
            return {"error": "TMDB API key not configured", "movies": []}

        # Extract title and year from filename
        name = Path(filename).stem
        logger.info(f"Parsing filename for TMDB search: '{name}'")

        # Try to extract year - improved regex that also matches year at end
        year_match = re.search(r'[\._\(\[\s]((?:19|20)\d{2})(?:[\._\)\]\s]|$)', name)
        year = year_match.group(1) if year_match else None

        # Clean title
        if year:
            idx = name.find(year)
            title = name[:idx] if idx > 0 else name
        else:
            title = re.split(r'[\._\(\[\s](?:1080p|720p|2160p|1440p|4k|bluray|bdrip|webrip|hdtv|x264|x265|hevc|aac|dts)', name, flags=re.IGNORECASE)[0]

        # Replace separators with spaces and strip brackets/parentheses
        title = re.sub(r'[\._\-\(\)\[\]]', ' ', title).strip()
        title = re.sub(r'\s+', ' ', title)  # Collapse multiple spaces

        logger.info(f"Extracted title='{title}', year={year}")

        if not title or len(title) < 2:
            logger.warning(f"Title too short after parsing: '{title}'")
            return {"error": f"Could not extract title from filename: {filename}", "movies": []}

        # Search TMDB - use title only, pass year as separate parameter
        logger.info(f"TMDB search: title='{title}', year={year}")

        tmdb_service = TMDBCacheService(db)

        # First try with year filter if available
        year_int = int(year) if year else None
        results = await tmdb_service.search_movies_autocomplete(title.strip(), limit=5, year=year_int)

        # If no results with year filter, retry without year
        if not results and year_int:
            logger.info(f"No results with year filter, retrying without year...")
            results = await tmdb_service.search_movies_autocomplete(title.strip(), limit=5, year=None)

        movies = []
        for movie in results:
            poster_url = f"https://image.tmdb.org/t/p/w500{movie['poster_path']}" if movie.get('poster_path') else "https://via.placeholder.com/500x750?text=?"
            movies.append({
                "id": movie['tmdb_id'],
                "title": movie['title'],
                "year": movie.get('year'),
                "poster": poster_url
            })

        logger.info(f"TMDB search returned {len(movies)} results for '{title}'")
        return movies

    except TrackerAPIError as e:
        logger.error(f"TMDB API error in suggest_movies: {e}")
        return {"error": str(e), "movies": []}
    except Exception as e:
        logger.error(f"Error in suggest_movies: {e}", exc_info=True)
        return {"error": f"Search failed: {str(e)}", "movies": []}


@router.get("/api/presentations/movie-details/{tmdb_id}")
async def get_movie_details(
    tmdb_id: int,
    db: Session = Depends(get_db)
):
    """
    Get detailed movie information from TMDB.
    """
    try:
        tmdb_service = TMDBCacheService(db)
        metadata = await tmdb_service.get_metadata(str(tmdb_id))

        # Extract extra data
        extra = metadata.get('extra_data', {})

        return {
            "id": tmdb_id,
            "title": metadata.get('title', ''),
            "year": metadata.get('year', ''),
            "rating": metadata.get('ratings', {}).get('vote_average'),
            "runtime": extra.get('runtime'),
            "genres": extra.get('genres', []),
            "overview": metadata.get('plot', ''),
            "poster": f"https://image.tmdb.org/t/p/w500{extra.get('poster_path')}" if extra.get('poster_path') else None,
            "backdrop": f"https://image.tmdb.org/t/p/original{extra.get('backdrop_path')}" if extra.get('backdrop_path') else None
        }

    except Exception as e:
        logger.error(f"Error fetching movie details for {tmdb_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/presentations/search-movies")
async def search_movies_simple(
    query: str = Query(..., min_length=2),
    db: Session = Depends(get_db)
):
    """
    Search movies by query - simplified response for manual search.
    """
    try:
        tmdb_service = TMDBCacheService(db)
        results = await tmdb_service.search_movies_autocomplete(query.strip(), limit=10)

        # search_movies_autocomplete returns tmdb_id, not id
        movies = []
        for movie in results:
            poster_url = f"https://image.tmdb.org/t/p/w500{movie['poster_path']}" if movie.get('poster_path') else "https://via.placeholder.com/500x750?text=?"
            movies.append({
                "id": movie['tmdb_id'],
                "title": movie['title'],
                "year": movie.get('year'),
                "poster": poster_url
            })

        return movies

    except Exception as e:
        logger.error(f"Error searching movies: {e}", exc_info=True)
        return []


@router.post("/api/presentations/generate")
async def generate_presentation(
    data: GeneratePresentationRequest,
    db: Session = Depends(get_db)
):
    """
    Generate BBCode presentation from TMDB metadata and MediaInfo.

    Args:
        data: Generation request with tmdb_id, template_id, and file_path

    Returns:
        Generated BBCode and optional HTML preview
    """
    try:
        # Validate inputs
        if not data.tmdb_id or not data.tmdb_id.isdigit():
            raise HTTPException(status_code=400, detail="Invalid TMDB ID")

        # Validate file path security
        settings = Settings.get_settings(db)
        if not _is_path_allowed(data.file_path, settings):
            raise HTTPException(
                status_code=403,
                detail="Access denied: File path is outside allowed directories"
            )

        # Check if file exists
        if not os.path.isfile(data.file_path):
            raise HTTPException(status_code=404, detail="Media file not found")

        # Get template
        template = BBCodeTemplate.get_by_id(db, data.template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        # Fetch TMDB metadata (with caching)
        logger.info(f"Fetching TMDB metadata for ID: {data.tmdb_id}")
        tmdb_service = TMDBCacheService(db)
        tmdb_metadata = await tmdb_service.get_metadata(data.tmdb_id)

        # Check if cast data has profile_path (old cached data might not have it)
        # If missing, force refresh to get complete cast data with photos
        cast_list = tmdb_metadata.get('cast', [])
        if cast_list and len(cast_list) > 0:
            first_cast = cast_list[0]
            if isinstance(first_cast, dict) and 'profile_path' not in first_cast:
                logger.info(f"Cast data missing profile_path, forcing refresh for tmdb_id={data.tmdb_id}")
                tmdb_metadata = await tmdb_service.get_metadata(data.tmdb_id, force_refresh=True)

        # Add extra TMDB fields needed for templates
        if 'extra_data' in tmdb_metadata:
            extra_data = tmdb_metadata['extra_data']
            tmdb_metadata['original_title'] = extra_data.get('original_title', '')
            tmdb_metadata['runtime'] = extra_data.get('runtime', 0)
            # Normalize genres to list of strings (handles both old and new format)
            tmdb_metadata['genres'] = normalize_genres(extra_data.get('genres', []))
            tmdb_metadata['poster_path'] = extra_data.get('poster_path', '')
            tmdb_metadata['backdrop_path'] = extra_data.get('backdrop_path', '')

        # Add full URLs for poster and backdrop
        if tmdb_metadata.get('poster_path'):
            tmdb_metadata['poster_url'] = f"https://image.tmdb.org/t/p/w500{tmdb_metadata['poster_path']}"
        else:
            tmdb_metadata['poster_url'] = "https://via.placeholder.com/500x750?text=No+Image"

        if tmdb_metadata.get('backdrop_path'):
            tmdb_metadata['backdrop_url'] = f"https://image.tmdb.org/t/p/original{tmdb_metadata['backdrop_path']}"
        else:
            tmdb_metadata['backdrop_url'] = ""

        # Add ratings
        if 'ratings' in tmdb_metadata:
            tmdb_metadata['vote_average'] = tmdb_metadata['ratings'].get('vote_average', 0)

        # Extract overview from plot
        tmdb_metadata['overview'] = tmdb_metadata.get('plot', '')

        logger.info(f"Generating BBCode for file: {data.file_path}")

        # Generate BBCode using template
        generator = get_bbcode_generator()
        bbcode = await generator.generate_from_template(
            template_content=template.content,
            file_path=data.file_path,
            tmdb_data=tmdb_metadata
        )

        logger.info(f"Successfully generated BBCode presentation ({len(bbcode)} chars)")

        return {
            "status": "success",
            "bbcode": bbcode,
            "preview_html": None,  # Could optionally convert BBCode to HTML
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating presentation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")


@router.get("/api/presentations/browse", response_class=HTMLResponse)
async def browse_files(
    request: Request,
    path: Optional[str] = Query(None, description="Directory path to browse"),
    db: Session = Depends(get_db)
):
    """
    Browse directory for media files. Returns HTML for HTMX.

    Args:
        path: Optional directory path (defaults to input_media_path from settings)

    Returns:
        HTML fragment with file browser content
    """
    try:
        settings = Settings.get_settings(db)

        # Determine starting path
        if path:
            browse_path = path
            logger.info(f"Browsing specified path: {browse_path}")
        elif settings and settings.input_media_path:
            browse_path = settings.input_media_path
            logger.info(f"Browsing default input_media_path: {browse_path}")
        else:
            logger.warning("No path specified and no input_media_path configured")
            return _render_browser_error("Aucun chemin specifie et input_media_path non configure dans les parametres.")

        # Validate path security
        if not _is_path_allowed(browse_path, settings):
            logger.warning(f"Access denied for path: {browse_path}")
            return _render_browser_error("Acces refuse: Le chemin est en dehors des repertoires autorises")

        # Check if path exists
        if not os.path.exists(browse_path):
            logger.warning(f"Path not found: {browse_path}")
            return _render_browser_error(f"Chemin introuvable: {browse_path}")

        if not os.path.isdir(browse_path):
            logger.warning(f"Path is not a directory: {browse_path}")
            return _render_browser_error(f"Le chemin n'est pas un dossier: {browse_path}")

        # List directory contents
        items = []
        try:
            entries = sorted(os.listdir(browse_path))
            logger.debug(f"Found {len(entries)} entries in {browse_path}")
        except PermissionError:
            logger.error(f"Permission denied for path: {browse_path}")
            return _render_browser_error(f"Permission refusee pour acceder au dossier")
        except Exception as e:
            logger.error(f"Error listing directory {browse_path}: {e}")
            return _render_browser_error(f"Erreur lors de la lecture du dossier: {str(e)}")

        # Add parent directory link if not at root
        parent_path = str(Path(browse_path).parent)
        if browse_path != parent_path and _is_path_allowed(parent_path, settings):
            items.append({
                'name': '..',
                'path': parent_path,
                'type': 'directory',
                'size': None,
            })

        # Video extensions to filter
        VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'}

        # Process directory entries
        for entry_name in entries:
            entry_path = os.path.join(browse_path, entry_name)

            # Skip hidden files/folders
            if entry_name.startswith('.'):
                continue

            try:
                if os.path.isdir(entry_path):
                    items.append({
                        'name': entry_name,
                        'path': entry_path,
                        'type': 'directory',
                        'size': None,
                    })
                elif os.path.isfile(entry_path):
                    # Only include video files
                    ext = Path(entry_name).suffix.lower()
                    if ext in VIDEO_EXTENSIONS:
                        size = os.path.getsize(entry_path)
                        items.append({
                            'name': entry_name,
                            'path': entry_path,
                            'type': 'file',
                            'size': _format_size(size),
                        })
            except (PermissionError, OSError) as e:
                # Skip files/dirs we can't access
                logger.debug(f"Skipping inaccessible entry: {entry_name} - {e}")
                continue

        logger.info(f"Browse successful: {browse_path} - Found {len(items)} items")

        # Render HTML response
        return _render_browser_content(browse_path, items)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error browsing directory: {e}", exc_info=True)
        return _render_browser_error(f"Erreur inattendue: {str(e)}")


# ============== Helper Functions ==============

def _render_browser_error(message: str) -> HTMLResponse:
    """Render an error message for the file browser."""
    html = f'''
    <div class="flex flex-col items-center justify-center p-8 text-center">
        <svg class="w-16 h-16 text-error opacity-50 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
        </svg>
        <p class="text-error">{escape(message)}</p>
    </div>
    '''
    return HTMLResponse(content=html)


def _render_browser_content(current_path: str, items: List[Dict]) -> HTMLResponse:
    """Render the file browser content as HTML."""
    # Escape path for safe display
    escaped_path = escape(current_path)

    html_parts = [
        f'''
        <div class="mb-4 p-3 bg-base-300 rounded-lg">
            <div class="text-xs text-gray-400 mb-1">Chemin actuel:</div>
            <div class="text-sm font-mono truncate" title="{escaped_path}">{escaped_path}</div>
        </div>
        <div class="max-h-96 overflow-y-auto space-y-1">
        '''
    ]

    if not items:
        html_parts.append('''
            <div class="flex flex-col items-center justify-center p-8 text-center text-gray-400">
                <svg class="w-12 h-12 opacity-50 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 19a2 2 0 01-2-2V7a2 2 0 012-2h4l2 2h4a2 2 0 012 2v1M5 19h14a2 2 0 002-2v-5a2 2 0 00-2-2H9a2 2 0 00-2 2v5a2 2 0 01-2 2z"></path>
                </svg>
                <p>Dossier vide ou aucun fichier video</p>
            </div>
        ''')
    else:
        for item in items:
            escaped_name = escape(item['name'])
            escaped_item_path = escape(item['path'])
            # Escape for JavaScript string
            js_path = item['path'].replace('\\', '\\\\').replace("'", "\\'")
            js_name = item['name'].replace('\\', '\\\\').replace("'", "\\'")

            if item['type'] == 'directory':
                # Directory - clicking navigates into it
                html_parts.append(f'''
                <div class="flex items-center gap-3 p-3 rounded-lg cursor-pointer hover:bg-base-300 transition-colors"
                     hx-get="/api/presentations/browse?path={escaped_item_path}"
                     hx-target="#file-browser-content"
                     hx-swap="innerHTML">
                    <div class="w-10 h-10 rounded-lg bg-yellow-500/20 flex items-center justify-center flex-shrink-0">
                        <svg class="w-5 h-5 text-yellow-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path>
                        </svg>
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="font-medium truncate">{escaped_name}</div>
                        <div class="text-xs text-gray-400">Dossier</div>
                    </div>
                    <svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path>
                    </svg>
                </div>
                ''')
            else:
                # Video file - clicking selects it
                size_text = item.get('size', '')
                html_parts.append(f'''
                <div class="flex items-center gap-3 p-3 rounded-lg cursor-pointer hover:bg-primary/20 hover:border-primary border-2 border-transparent transition-all"
                     onclick="selectFile('{js_path}', '{js_name}')">
                    <div class="w-10 h-10 rounded-lg bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                        <svg class="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z"></path>
                        </svg>
                    </div>
                    <div class="flex-1 min-w-0">
                        <div class="font-medium truncate" title="{escaped_name}">{escaped_name}</div>
                        <div class="text-xs text-gray-400">{size_text}</div>
                    </div>
                    <svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                    </svg>
                </div>
                ''')

    html_parts.append('</div>')

    return HTMLResponse(content=''.join(html_parts))


def _is_path_allowed(path: str, settings: Settings) -> bool:
    """
    Check if path is within allowed directories.

    Security check to prevent directory traversal attacks.
    """
    if not path or not settings:
        return False

    try:
        # Resolve to absolute path
        abs_path = os.path.abspath(path)

        # Check against input_media_path
        if settings.input_media_path:
            allowed = os.path.abspath(settings.input_media_path)
            if abs_path.startswith(allowed):
                return True

        # Check against output_dir
        if settings.output_dir:
            allowed = os.path.abspath(settings.output_dir)
            if abs_path.startswith(allowed):
                return True

        return False

    except Exception as e:
        logger.warning(f"Path validation error: {e}")
        return False


def _format_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _get_resolution_label(width: int, height: int) -> str:
    """Get common resolution label from dimensions."""
    if height >= 2160 or width >= 3840:
        return "2160p (4K)"
    elif height >= 1080 or width >= 1920:
        return "1080p"
    elif height >= 720 or width >= 1280:
        return "720p"
    elif height >= 576:
        return "576p"
    elif height >= 480:
        return "480p"
    else:
        return f"{height}p"


def _get_channel_label(channels: int) -> str:
    """Get audio channel layout label."""
    channel_labels = {
        1: "Mono",
        2: "Stereo",
        6: "5.1",
        8: "7.1"
    }
    return channel_labels.get(channels, f"{channels}ch")


@router.get("/api/presentations/analyze")
async def analyze_file(
    path: str = Query(..., description="Path to the media file to analyze"),
    db: Session = Depends(get_db)
):
    """
    Analyze a media file and return MediaInfo data.

    Args:
        path: Path to the media file

    Returns:
        MediaInfo data including video, audio tracks, subtitles, file size, duration
    """
    try:
        # Validate path security
        settings = Settings.get_settings(db)
        if not _is_path_allowed(path, settings):
            return {
                "status": "error",
                "message": "Access denied: File path is outside allowed directories"
            }

        # Check if file exists
        if not os.path.isfile(path):
            return {
                "status": "error",
                "message": f"File not found: {path}"
            }

        # Extract MediaInfo using NFOGenerator
        nfo_generator = get_nfo_generator()
        media_data = await nfo_generator.extract_mediainfo(path)

        # Get file size
        file_size = os.path.getsize(path)

        # Build response
        video_info = None
        if media_data.video_tracks:
            v = media_data.video_tracks[0]
            video_info = {
                "resolution": v.resolution,
                "resolution_label": _get_resolution_label(v.width, v.height),
                "codec": v.format,
                "codec_profile": v.format_profile,
                "bitrate": v.bitrate,
                "frame_rate": v.frame_rate,
                "hdr": v.hdr_format if v.hdr_format else None,
                "bit_depth": v.bit_depth
            }

        audio_tracks = []
        for audio in media_data.audio_tracks:
            audio_tracks.append({
                "format": audio.format,
                "channels": audio.channels,
                "channels_label": _get_channel_label(audio.channels),
                "language": audio.language or "Unknown",
                "bitrate": audio.bitrate,
                "title": audio.title
            })

        subtitles = []
        for sub in media_data.subtitle_tracks:
            subtitles.append({
                "format": sub.format,
                "language": sub.language or "Unknown",
                "title": sub.title
            })

        logger.info(f"Analyzed file: {path} - {len(audio_tracks)} audio, {len(subtitles)} subtitles")

        return {
            "status": "success",
            "file_name": media_data.file_name,
            "file_size": _format_size(file_size),
            "file_size_bytes": file_size,
            "duration": media_data.duration,
            "format": media_data.format,
            "overall_bitrate": media_data.overall_bitrate,
            "video": video_info,
            "audio_tracks": audio_tracks,
            "subtitles": subtitles
        }

    except Exception as e:
        logger.error(f"Error analyzing file: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Analysis failed: {str(e)}"
        }
