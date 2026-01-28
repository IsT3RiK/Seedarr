"""
Media Analyzer Service for Seedarr v2.0

This module handles MediaInfo analysis and .torrent file creation with
non-blocking async performance. File hashing operations are offloaded to
ProcessPoolExecutor to prevent blocking the event loop.

Key Features:
    - MediaInfo technical analysis extraction
    - Non-blocking .torrent file creation using ProcessPoolExecutor
    - TMDB metadata validation
    - Async-friendly design with no blocking operations >100ms

Performance Optimization:
    File hashing (CPU-intensive) is offloaded to ProcessPoolExecutor using
    asyncio's run_in_executor(). This ensures the async event loop remains
    responsive during large file processing.

Critical Requirements:
    - .torrent files MUST include source="lacale" flag (prevents re-download)
    - File hashing operations MUST NOT block event loop
    - All operations should be async-friendly

Usage Example:
    >>> from app.services.media_analyzer import MediaAnalyzer
    >>> from app.database import SessionLocal
    >>>
    >>> db = SessionLocal()
    >>> analyzer = MediaAnalyzer(db)
    >>>
    >>> # Create .torrent file (non-blocking)
    >>> torrent_path = await analyzer.create_torrent(
    ...     file_path="/media/Movie.2023.1080p.mkv",
    ...     announce_url="https://tracker.example.com/announce",
    ...     output_dir="/output"
    ... )
"""

import asyncio
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from .exceptions import TrackerAPIError
from .tmdb_cache_service import TMDBCacheService

logger = logging.getLogger(__name__)

# Global ProcessPoolExecutor for file hashing operations
# This is shared across all MediaAnalyzer instances for efficiency
_process_pool: Optional[ProcessPoolExecutor] = None


def _get_process_pool() -> ProcessPoolExecutor:
    """
    Get or create the global ProcessPoolExecutor for file hashing.

    Uses a global singleton pattern to avoid creating multiple process pools.
    The pool is sized based on CPU count for optimal performance.

    Returns:
        ProcessPoolExecutor instance
    """
    global _process_pool
    if _process_pool is None:
        # Use max_workers=None to auto-detect CPU count
        # For file hashing, we want one worker per CPU core
        _process_pool = ProcessPoolExecutor(max_workers=None)
        logger.info("✓ ProcessPoolExecutor initialized for file hashing operations")
    return _process_pool


def _create_torrent_sync(
    file_path: str,
    announce_url: str,
    output_path: str,
    source: str = "lacale"
) -> str:
    """
    Synchronous torrent creation function (runs in ProcessPoolExecutor).

    This function is executed in a separate process to avoid blocking the
    event loop during CPU-intensive file hashing operations.

    CRITICAL: This function MUST include source="lacale" flag to prevent
    torrent clients from re-downloading all content when the .torrent file
    is loaded. This is a tracker-specific requirement.

    Args:
        file_path: Path to file or directory to create torrent from
        announce_url: Tracker announce URL
        output_path: Path where .torrent file will be saved
        source: Source tag for torrent (CRITICAL - prevents re-download)

    Returns:
        Path to created .torrent file

    Raises:
        Exception: If torrent creation fails (will be wrapped in TrackerAPIError)

    Note:
        This function runs in a separate process, so it cannot access
        the main process's logger or database session.
    """
    try:
        import torf  # Import here to avoid issues with process serialization

        # CRITICAL: Include source flag to prevent re-download
        # Without this flag, torrent clients will re-download all content
        # when the .torrent file is loaded, even if files already exist
        torrent = torf.Torrent(
            path=file_path,
            trackers=[announce_url],
            source=source,  # CRITICAL - prevents re-download
            private=True
        )

        # Create .torrent file
        # This is CPU-intensive for large files (hashing all chunks)
        torrent.generate()

        # Write to output path
        torrent.write(output_path)

        return output_path

    except Exception as e:
        # Re-raise with context (will be caught and wrapped in calling function)
        raise Exception(f"Torrent creation failed: {type(e).__name__}: {e}") from e


class MediaAnalyzer:
    """
    Media analysis and .torrent creation service with async performance optimization.

    This class handles MediaInfo analysis and .torrent file creation while
    ensuring the async event loop remains responsive. CPU-intensive file
    hashing operations are offloaded to ProcessPoolExecutor.

    Architecture:
        - MediaInfo analysis: Extract technical metadata (codec, resolution, etc.)
        - TMDB validation: Verify metadata against TMDB API
        - .torrent creation: Non-blocking file hashing using ProcessPoolExecutor
        - Async-friendly: All public methods are async, no blocking >100ms

    Performance:
        File hashing is offloaded to ProcessPoolExecutor to prevent blocking
        the event loop. This allows the API to remain responsive even during
        processing of very large files (>10GB).

    Example:
        >>> analyzer = MediaAnalyzer(db)
        >>> torrent_path = await analyzer.create_torrent(
        ...     file_path="/media/Movie.mkv",
        ...     announce_url="https://tracker.example.com/announce",
        ...     output_dir="/output"
        ... )
    """

    def __init__(self, db: Session):
        """
        Initialize MediaAnalyzer.

        Args:
            db: SQLAlchemy database session for TMDB cache access
        """
        self.db = db
        self.tmdb_cache = TMDBCacheService(db)

    async def create_torrent(
        self,
        file_path: str,
        announce_url: str,
        output_dir: str,
        source: str = "lacale"
    ) -> str:
        """
        Create .torrent file from media file (non-blocking async).

        This method offloads CPU-intensive file hashing to ProcessPoolExecutor
        to ensure the async event loop remains responsive. For large files,
        hashing can take several minutes, so this is critical for performance.

        CRITICAL: The generated .torrent file includes source="lacale" flag
        to prevent torrent clients from re-downloading all content when the
        .torrent file is loaded.

        Args:
            file_path: Path to media file or directory
            announce_url: Tracker announce URL
            output_dir: Directory where .torrent file will be saved
            source: Source tag for torrent (default: "lacale")

        Returns:
            Path to created .torrent file

        Raises:
            TrackerAPIError: If torrent creation fails

        Example:
            >>> torrent_path = await analyzer.create_torrent(
            ...     file_path="/media/Movie.2023.1080p.mkv",
            ...     announce_url="https://tracker.example.com/announce",
            ...     output_dir="/output"
            ... )
            >>> # torrent_path: "/output/Movie.2023.1080p.torrent"
        """
        logger.info(f"Creating .torrent file for: {file_path}")

        # Validate input file exists
        if not os.path.exists(file_path):
            error_msg = f"Cannot create torrent: file does not exist: {file_path}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg)

        # Ensure output directory exists
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            error_msg = f"Cannot create output directory {output_dir}: {type(e).__name__}: {e}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg) from e

        # Determine output .torrent file path
        file_path_obj = Path(file_path)
        base_name = file_path_obj.stem if file_path_obj.is_file() else file_path_obj.name
        output_path = os.path.join(output_dir, f"{base_name}.torrent")

        logger.info(f"Output .torrent path: {output_path}")
        logger.info(f"Offloading file hashing to ProcessPoolExecutor (non-blocking)")

        try:
            # Get process pool for file hashing
            pool = _get_process_pool()

            # Run CPU-intensive torrent creation in separate process
            # This prevents blocking the async event loop during file hashing
            loop = asyncio.get_event_loop()
            result_path = await loop.run_in_executor(
                pool,
                _create_torrent_sync,
                file_path,
                announce_url,
                output_path,
                source
            )

            logger.info(f"✓ .torrent file created successfully: {result_path}")
            logger.info(f"✓ Source flag set to: {source} (prevents re-download)")

            return result_path

        except Exception as e:
            error_msg = f"Failed to create .torrent file: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg) from e

    async def extract_mediainfo(self, file_path: str) -> Dict[str, Any]:
        """
        Extract technical metadata using MediaInfo (non-blocking).

        This method extracts technical information from media files using
        the pymediainfo library. Operations are offloaded to a thread pool
        to prevent blocking the event loop.

        Args:
            file_path: Path to media file

        Returns:
            Dictionary containing technical metadata:
            - codec: Video codec (e.g., "x264", "HEVC")
            - resolution: Resolution (e.g., "1080p", "2160p")
            - audio: Audio codec (e.g., "AAC", "DTS")
            - duration: Duration in seconds
            - file_size: File size in bytes

        Raises:
            TrackerAPIError: If MediaInfo extraction fails

        Example:
            >>> metadata = await analyzer.extract_mediainfo("/media/Movie.mkv")
            >>> # metadata: {"codec": "x264", "resolution": "1080p", ...}

        Note:
            This is a placeholder implementation. Full MediaInfo integration
            requires pymediainfo library and additional parsing logic.
        """
        logger.info(f"Extracting MediaInfo for: {file_path}")

        # TODO: Implement full MediaInfo extraction
        # This requires:
        # 1. pymediainfo library integration
        # 2. Parsing MediaInfo output for codec, resolution, audio
        # 3. Offloading to thread pool (use asyncio.to_thread)
        # 4. Error handling for unsupported formats

        # Placeholder: return basic file info
        try:
            file_stat = await asyncio.to_thread(os.stat, file_path)
            return {
                "file_size": file_stat.st_size,
                "file_path": file_path,
                # TODO: Add actual MediaInfo fields
            }
        except Exception as e:
            error_msg = f"Failed to extract MediaInfo: {type(e).__name__}: {e}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg) from e

    async def validate_tmdb_metadata(
        self,
        tmdb_id: str,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Validate and fetch TMDB metadata using persistent cache.

        This method implements cache-first lookup strategy:
        1. Check database cache for tmdb_id
        2. If cached and not expired, return cached data (fast path)
        3. If cache miss or force_refresh, fetch from TMDB API
        4. Store in cache for future requests
        5. Return metadata

        This significantly reduces TMDB API calls and improves performance:
        - Expected cache hit rate: >90% for repeated lookups
        - Expected reduction in API calls: >80%
        - Cache persists across application restarts

        Args:
            tmdb_id: TMDB movie/TV show ID to validate
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            Dictionary with TMDB metadata:
                - tmdb_id: TMDB ID
                - title: Movie/TV show title
                - year: Release/first air year
                - cast: List of cast members with names and characters
                - plot: Plot summary/overview
                - ratings: Rating information (vote_average, vote_count)
                - cached_at: When data was cached (ISO format)
                - expires_at: When cache expires (ISO format)

        Raises:
            TrackerAPIError: If TMDB validation fails (invalid ID, API error)
            NetworkRetryableError: If API request fails (retryable with backoff)

        Example:
            >>> # First call - cache miss, fetches from API
            >>> metadata = await analyzer.validate_tmdb_metadata("550")
            >>> print(metadata['title'])  # "Fight Club"
            >>>
            >>> # Second call - cache hit, returns immediately
            >>> metadata = await analyzer.validate_tmdb_metadata("550")
            >>> # Returns cached data (no API call)
            >>>
            >>> # Force refresh - bypasses cache
            >>> metadata = await analyzer.validate_tmdb_metadata("550", force_refresh=True)
            >>> # Fetches fresh data from API and updates cache

        Note:
            Cache TTL is configurable via Settings.tmdb_cache_ttl_days (default: 30 days).
            Expired cache entries are automatically cleaned up on query.
        """
        logger.info(f"Validating TMDB metadata for tmdb_id={tmdb_id}")

        try:
            # Use cache service for cache-first lookup
            metadata = await self.tmdb_cache.get_metadata(tmdb_id, force_refresh)

            logger.info(
                f"✓ TMDB validation successful for tmdb_id={tmdb_id}: "
                f"{metadata.get('title')} ({metadata.get('year')})"
            )

            return metadata

        except Exception as e:
            logger.error(
                f"✗ TMDB validation failed for tmdb_id={tmdb_id}: {type(e).__name__}: {e}"
            )
            raise


def shutdown_process_pool():
    """
    Shutdown the global ProcessPoolExecutor gracefully.

    This should be called during application shutdown to ensure all
    pending file hashing operations complete before exit.

    Example:
        >>> from app.services.media_analyzer import shutdown_process_pool
        >>> # During application shutdown
        >>> shutdown_process_pool()
    """
    global _process_pool
    if _process_pool is not None:
        logger.info("Shutting down ProcessPoolExecutor...")
        _process_pool.shutdown(wait=True)
        _process_pool = None
        logger.info("✓ ProcessPoolExecutor shutdown complete")
