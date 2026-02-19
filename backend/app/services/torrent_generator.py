"""
TorrentGenerator Service for Seedarr v2.0

This module provides torrent file generation with support for multiple trackers.
Each tracker can have its own piece size strategy and source flag, resulting
in unique torrent hashes per tracker.

Features:
    - Multi-tracker torrent generation
    - Per-tracker piece size strategies (auto, c411, standard)
    - Source flag support for unique hashes
    - Async torrent creation to avoid blocking
    - Batch generation for all enabled trackers

Piece Size Strategies:
    - "auto": Automatic based on file size (torf defaults)
    - "c411": C411-specific piece sizes
    - "standard": Conservative piece sizes (max 16MB)

Usage Example:
    generator = TorrentGenerator()

    # Generate for a single tracker
    path = await generator.generate_for_tracker(file_path, tracker, release_name)

    # Generate for all enabled trackers
    paths = await generator.generate_all(db, file_path, release_name)
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

import torf

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from ..models.tracker import Tracker

logger = logging.getLogger(__name__)


# Piece size constants (in bytes)
KiB = 1024
MiB = 1024 * 1024
GiB = 1024 * 1024 * 1024


class TorrentGenerator:
    """
    Service for generating torrent files with multi-tracker support.

    This service creates .torrent files with tracker-specific configurations:
    - Unique source flags for different hashes per tracker
    - Custom piece size strategies per tracker
    - Private flag always enabled for private trackers

    Piece Size Strategies:
        - "auto": Uses torf's automatic calculation
        - "c411": C411's specific piece size table
        - "standard": Conservative sizes (max 16MB)

    Example:
        >>> generator = TorrentGenerator()
        >>>
        >>> # Generate for one tracker
        >>> tracker = Tracker.get_by_slug(db, "lacale")
        >>> torrent_path = await generator.generate_for_tracker(
        ...     file_path="/media/Movie.mkv",
        ...     tracker=tracker,
        ...     release_name="Movie.2024.1080p.BluRay.x264-TP"
        ... )
        >>>
        >>> # Generate for all enabled trackers
        >>> paths = await generator.generate_all(
        ...     db=db,
        ...     file_path="/media/Movie.mkv",
        ...     release_name="Movie.2024.1080p.BluRay.x264-TP"
        ... )
        >>> # Returns: {"lacale": "/path/to_LaCale.torrent", "c411": "/path/to_C411.torrent"}
    """

    # C411 piece size table (file size thresholds in bytes, piece size in bytes)
    C411_PIECE_SIZES = [
        (1 * GiB, 1024 * KiB),    # < 1 GB: 1024 KiB
        (2 * GiB, 2048 * KiB),    # < 2 GB: 2048 KiB
        (3 * GiB, 4096 * KiB),    # < 3 GB: 4096 KiB
        (8 * GiB, 8192 * KiB),    # < 8 GB: 8192 KiB
        (float('inf'), 16384 * KiB),  # >= 8 GB: 16384 KiB
    ]

    # Standard piece sizes (more conservative)
    STANDARD_PIECE_SIZES = [
        (512 * MiB, 512 * KiB),     # < 512 MB: 512 KiB
        (1 * GiB, 1024 * KiB),      # < 1 GB: 1024 KiB
        (2 * GiB, 2048 * KiB),      # < 2 GB: 2048 KiB
        (4 * GiB, 4096 * KiB),      # < 4 GB: 4096 KiB
        (8 * GiB, 8192 * KiB),      # < 8 GB: 8192 KiB
        (float('inf'), 16384 * KiB),  # >= 8 GB: 16384 KiB
    ]

    def __init__(self, output_dir: Optional[str] = None):
        """
        Initialize TorrentGenerator.

        Args:
            output_dir: Default output directory for generated torrents.
                       If None, torrents are saved next to the source file.
        """
        self.output_dir = output_dir

    def calculate_piece_size(self, file_size: int, strategy: str = "auto") -> Optional[int]:
        """
        Calculate piece size based on file size and strategy.

        Args:
            file_size: Size of the file in bytes
            strategy: Piece size strategy ("auto", "c411", "standard")

        Returns:
            Piece size in bytes, or None to use torf's automatic calculation
        """
        if strategy == "auto":
            # Let torf calculate automatically
            return None

        elif strategy == "c411":
            # Use C411's specific piece size table
            for threshold, piece_size in self.C411_PIECE_SIZES:
                if file_size < threshold:
                    return piece_size
            return 16384 * KiB  # Fallback

        elif strategy == "standard":
            # Use standard/conservative piece sizes
            for threshold, piece_size in self.STANDARD_PIECE_SIZES:
                if file_size < threshold:
                    return piece_size
            return 16384 * KiB  # Fallback

        else:
            logger.warning(f"Unknown piece size strategy: {strategy}, using auto")
            return None

    async def generate_for_tracker(
        self,
        file_path: str,
        tracker: 'Tracker',
        release_name: str,
        output_dir: Optional[str] = None,
        tracker_release_name: Optional[str] = None
    ) -> str:
        """
        Generate a .torrent file for a specific tracker.

        This method creates a torrent file with the tracker's specific configuration:
        - Announce URL from tracker
        - Source flag for unique hash
        - Piece size based on tracker's strategy
        - Tracker-specific release name (for torrent filename)

        Args:
            file_path: Path to the media file
            tracker: Tracker model instance
            release_name: Default release name (used if no tracker-specific name)
            output_dir: Output directory (defaults to file's directory)
            tracker_release_name: Tracker-specific release name (from naming_template).
                                 If provided, used for torrent filename instead of release_name.

        Returns:
            Path to the generated .torrent file

        Raises:
            TorrentGenerationError: If torrent generation fails
        """
        # Use tracker-specific name if provided, otherwise use default
        effective_release_name = tracker_release_name or release_name
        file_path = Path(file_path)

        if not file_path.exists():
            raise TorrentGenerationError(f"File does not exist: {file_path}")

        # Determine output directory
        out_dir = Path(output_dir or self.output_dir or file_path.parent)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Generate torrent filename: {release_name}_{TrackerName}.torrent
        tracker_suffix = tracker.name.replace(" ", "")
        torrent_filename = f"{effective_release_name}_{tracker_suffix}.torrent"
        torrent_path = out_dir / torrent_filename

        # Get announce URL
        announce_url = tracker.announce_url
        if not announce_url:
            raise TorrentGenerationError(
                f"Tracker {tracker.name} has no announce URL configured"
            )

        # Get file size for piece size calculation
        file_size = file_path.stat().st_size

        # Calculate piece size based on tracker's strategy
        piece_size = self.calculate_piece_size(
            file_size,
            tracker.piece_size_strategy or "auto"
        )

        logger.info(
            f"Generating torrent for {tracker.name}: "
            f"file={file_path.name}, "
            f"size={file_size / GiB:.2f} GB, "
            f"piece_size={piece_size / KiB if piece_size else 'auto'} KiB, "
            f"source={tracker.source_flag}"
        )

        def create_torrent():
            """Create torrent in thread to avoid blocking event loop."""
            torrent_kwargs = {
                'path': str(file_path),
                'trackers': [announce_url],
                'private': True,
                'comment': "Uploaded by Seedarr v2.0"
            }

            # Only set source flag if it's a non-empty string
            if tracker.source_flag and tracker.source_flag.strip():
                torrent_kwargs['source'] = tracker.source_flag.strip()

            torrent = torf.Torrent(**torrent_kwargs)

            # Set piece size if specified
            if piece_size:
                torrent.piece_size = piece_size

            # Generate torrent (hashes the file - can be slow)
            torrent.generate()

            # Write to file
            torrent.write(str(torrent_path), overwrite=True)

            return torrent

        # Run torrent creation in thread pool
        logger.info(f"Hashing file for {tracker.name} torrent (async)...")
        torrent = await asyncio.to_thread(create_torrent)

        logger.info(
            f"Generated torrent for {tracker.name}: "
            f"{torrent_path.name} "
            f"(infohash: {torrent.infohash})"
        )

        return str(torrent_path)

    async def generate_all(
        self,
        db: 'Session',
        file_path: str,
        release_name: str,
        output_dir: Optional[str] = None,
        tracker_slugs: Optional[List[str]] = None,
        tracker_release_names: Optional[Dict[str, str]] = None,
        tracker_output_dirs: Optional[Dict[str, str]] = None,
        tracker_file_paths: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """
        Generate torrent files for all enabled trackers.

        This method generates a .torrent file for each enabled tracker,
        with tracker-specific configurations (piece size, source flag, release name).

        Args:
            db: SQLAlchemy database session
            file_path: Path to the media file (default for all trackers)
            release_name: Default release name for the torrents
            output_dir: Output directory (defaults to file's directory)
            tracker_slugs: Optional list of tracker slugs to generate for.
                          If None, generates for all enabled trackers.
            tracker_release_names: Optional dict mapping tracker slugs to tracker-specific
                                  release names (from naming_templates).
                                  Example: {"c411": "Custom.Name.For.C411-FW"}
            tracker_output_dirs: Optional dict mapping tracker slugs to per-tracker
                                output directories. Takes precedence over output_dir.
                                Example: {"lacale": "/torrents/lacale", "c411": "/torrents/c411"}
            tracker_file_paths: Optional dict mapping tracker slugs to per-tracker
                               media file paths (from per-tracker hardlinks).
                               Takes precedence over file_path for that tracker.

        Returns:
            Dictionary mapping tracker slugs to torrent file paths:
            {"lacale": "/path/to_LaCale.torrent", "c411": "/path/to_C411.torrent"}

        Raises:
            TorrentGenerationError: If generation fails for any tracker
        """
        from ..models.tracker import Tracker

        # Get trackers to generate for
        if tracker_slugs:
            trackers = [
                Tracker.get_by_slug(db, slug)
                for slug in tracker_slugs
            ]
            trackers = [t for t in trackers if t and t.enabled]
        else:
            trackers = Tracker.get_enabled(db)

        if not trackers:
            logger.warning("No enabled trackers found, no torrents generated")
            return {}

        logger.info(
            f"Generating torrents for {len(trackers)} tracker(s): "
            f"{[t.name for t in trackers]}"
        )

        # Generate torrents for each tracker
        torrent_paths = {}
        errors = []

        for tracker in trackers:
            try:
                # Get tracker-specific release name if provided
                tracker_specific_name = None
                if tracker_release_names:
                    tracker_specific_name = tracker_release_names.get(tracker.slug)

                # Use per-tracker output dir if provided, otherwise fallback to output_dir
                tracker_out_dir = output_dir
                if tracker_output_dirs and tracker.slug in tracker_output_dirs:
                    tracker_out_dir = tracker_output_dirs[tracker.slug]

                # Use per-tracker file path if provided (from per-tracker hardlinks)
                tracker_file_path = file_path
                if tracker_file_paths and tracker.slug in tracker_file_paths:
                    tracker_file_path = tracker_file_paths[tracker.slug]

                path = await self.generate_for_tracker(
                    file_path=tracker_file_path,
                    tracker=tracker,
                    release_name=release_name,
                    output_dir=tracker_out_dir,
                    tracker_release_name=tracker_specific_name
                )
                torrent_paths[tracker.slug] = path
            except Exception as e:
                error_msg = f"Failed to generate torrent for {tracker.name}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        if errors:
            logger.warning(
                f"Torrent generation completed with {len(errors)} error(s): "
                f"{errors}"
            )

        logger.info(
            f"Generated {len(torrent_paths)} torrent(s): "
            f"{list(torrent_paths.keys())}"
        )

        return torrent_paths

    async def generate_single_tracker_torrent(
        self,
        file_path: str,
        announce_url: str,
        release_name: str,
        source_flag: str = "lacale",
        piece_size_strategy: str = "auto",
        output_dir: Optional[str] = None
    ) -> str:
        """
        Generate a single torrent file without a Tracker model.

        This is a convenience method for backward compatibility with the
        existing single-tracker pipeline.

        Args:
            file_path: Path to the media file
            announce_url: Tracker announce URL
            release_name: Release name for the torrent
            source_flag: Source flag for torrent hash
            piece_size_strategy: Piece size calculation strategy
            output_dir: Output directory

        Returns:
            Path to the generated .torrent file
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise TorrentGenerationError(f"File does not exist: {file_path}")

        # Determine output directory
        out_dir = Path(output_dir or self.output_dir or file_path.parent)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Generate torrent filename
        torrent_filename = f"{release_name}.torrent"
        torrent_path = out_dir / torrent_filename

        # Get file size for piece size calculation
        file_size = file_path.stat().st_size
        piece_size = self.calculate_piece_size(file_size, piece_size_strategy)

        logger.info(
            f"Generating single torrent: "
            f"file={file_path.name}, "
            f"size={file_size / GiB:.2f} GB, "
            f"piece_size={piece_size / KiB if piece_size else 'auto'} KiB, "
            f"source={source_flag}"
        )

        def create_torrent():
            """Create torrent in thread to avoid blocking event loop."""
            torrent = torf.Torrent(
                path=str(file_path),
                trackers=[announce_url],
                private=True,
                source=source_flag,
                comment=f"Uploaded by Seedarr v2.0"
            )

            if piece_size:
                torrent.piece_size = piece_size

            torrent.generate()
            torrent.write(str(torrent_path), overwrite=True)
            return torrent

        torrent = await asyncio.to_thread(create_torrent)

        logger.info(
            f"Generated torrent: {torrent_path.name} "
            f"(infohash: {torrent.infohash})"
        )

        return str(torrent_path)


class TorrentGenerationError(Exception):
    """Exception raised when torrent generation fails."""
    pass


# Singleton instance for convenience
_generator_instance: Optional[TorrentGenerator] = None


def get_torrent_generator() -> TorrentGenerator:
    """
    Get the singleton TorrentGenerator instance.

    Returns:
        TorrentGenerator instance
    """
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = TorrentGenerator()
    return _generator_instance
