"""
HardlinkManager Service for Seedarr v2.1

This module provides functionality for creating release folder structures
with hardlinks (or fallback copies) for media files. Hardlinks save disk space
by pointing to the same data blocks as the original file.

Features:
    - Create release folder structure with hardlinks
    - Automatic fallback to copy if cross-filesystem
    - Support for NFO and screenshot subfolder creation
    - Cleanup utilities for old release structures

Folder Structure:
    OUTPUT_DIR/{release_name}/
        {release_name}.mkv  (hardlink or copy)
        {release_name}.nfo
        screens/
            screen_001.png
            screen_002.png
            ...

Usage Example:
    manager = HardlinkManager()
    result = manager.create_release_structure(
        source_file="/media/movie.mkv",
        release_name="Movie.2024.1080p.BluRay.x264-GROUP",
        output_dir="/output"
    )
    # result = {
    #     'release_dir': '/output/Movie.2024.1080p.BluRay.x264-GROUP',
    #     'media_file': '/output/Movie.2024.1080p.BluRay.x264-GROUP/Movie.2024.1080p.BluRay.x264-GROUP.mkv',
    #     'nfo_path': '/output/Movie.2024.1080p.BluRay.x264-GROUP/Movie.2024.1080p.BluRay.x264-GROUP.nfo',
    #     'screens_dir': '/output/Movie.2024.1080p.BluRay.x264-GROUP/screens',
    #     'hardlink_used': True
    # }
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class HardlinkError(Exception):
    """Exception raised when hardlink operation fails."""
    pass


class HardlinkManager:
    """
    Manager for creating release folder structures with hardlinks.

    This class handles the creation of properly formatted release folders
    including the media file (hardlinked or copied), NFO placeholder, and
    screenshots directory.

    Hardlink Strategy:
        1. First attempt to create a hardlink (same filesystem, saves space)
        2. If hardlink fails (cross-filesystem), fall back to copy
        3. Log warnings when copy is used so user is aware of space usage

    Attributes:
        default_output_dir: Default output directory if not specified
    """

    def __init__(self, default_output_dir: Optional[str] = None):
        """
        Initialize HardlinkManager.

        Args:
            default_output_dir: Default directory for release structures
        """
        self.default_output_dir = default_output_dir

    def create_release_structure(
        self,
        source_file: str,
        release_name: str,
        output_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a complete release folder structure with hardlinked media file.

        Creates the following structure:
            {output_dir}/{release_name}/
                {release_name}.{ext}  (hardlink or copy of source)
                {release_name}.nfo    (placeholder path, not created)
                screens/              (empty directory for screenshots)

        Args:
            source_file: Path to the source media file
            release_name: The release name (folder and file name)
            output_dir: Output directory (uses default_output_dir if not specified)

        Returns:
            Dictionary with paths:
                {
                    'release_dir': str,      # Path to release folder
                    'media_file': str,       # Path to hardlinked/copied media
                    'nfo_path': str,         # Path where NFO should be saved
                    'screens_dir': str,      # Path to screenshots directory
                    'hardlink_used': bool,   # True if hardlink, False if copy
                    'source_file': str       # Original source file path
                }

        Raises:
            HardlinkError: If neither hardlink nor copy succeeds
            FileNotFoundError: If source file doesn't exist
        """
        source_path = Path(source_file)

        # Validate source file exists
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_file}")

        # Determine output directory
        base_output = output_dir or self.default_output_dir
        if not base_output:
            # Default to parent directory of source file
            base_output = str(source_path.parent)

        # Create release directory
        release_dir = Path(base_output) / release_name
        release_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created release directory: {release_dir}")

        # Create screens directory
        screens_dir = release_dir / "screens"
        screens_dir.mkdir(exist_ok=True)
        logger.debug(f"Created screens directory: {screens_dir}")

        # Determine target media file path
        extension = source_path.suffix.lower()
        media_file = release_dir / f"{release_name}{extension}"
        nfo_path = release_dir / f"{release_name}.nfo"

        # Create hardlink or copy
        hardlink_used = self._create_hardlink_or_copy(source_path, media_file)

        result = {
            'release_dir': str(release_dir),
            'media_file': str(media_file),
            'nfo_path': str(nfo_path),
            'screens_dir': str(screens_dir),
            'hardlink_used': hardlink_used,
            'source_file': str(source_path)
        }

        logger.info(
            f"✓ Release structure created: {release_name} "
            f"(hardlink={'yes' if hardlink_used else 'no (copy)'})"
        )

        return result

    def _create_hardlink_or_copy(self, source: Path, target: Path) -> bool:
        """
        Create hardlink or fall back to copy.

        Args:
            source: Source file path
            target: Target file path

        Returns:
            True if hardlink was used, False if copy was used

        Raises:
            HardlinkError: If both hardlink and copy fail
        """
        # Skip if target already exists
        if target.exists():
            # Check if it's already linked to source
            if self._is_same_file(source, target):
                logger.info(f"Target already linked to source: {target}")
                return True
            else:
                # Remove existing file to replace
                logger.warning(f"Removing existing file to replace: {target}")
                target.unlink()

        # Try hardlink first
        try:
            os.link(str(source), str(target))
            logger.info(f"✓ Created hardlink: {target.name}")
            return True

        except OSError as e:
            # Hardlink failed (likely cross-filesystem)
            logger.warning(
                f"Hardlink failed ({e}), falling back to copy. "
                f"This will use additional disk space."
            )

        # Fallback to copy
        try:
            shutil.copy2(str(source), str(target))
            logger.info(f"✓ Created copy (hardlink unavailable): {target.name}")
            return False

        except Exception as e:
            error_msg = f"Both hardlink and copy failed for {target}: {e}"
            logger.error(error_msg)
            raise HardlinkError(error_msg) from e

    def _is_same_file(self, file1: Path, file2: Path) -> bool:
        """
        Check if two files are the same (hardlinked).

        Args:
            file1: First file path
            file2: Second file path

        Returns:
            True if files have the same inode (are hardlinked)
        """
        try:
            stat1 = file1.stat()
            stat2 = file2.stat()
            # Same device and inode means same file
            return (stat1.st_dev == stat2.st_dev and
                    stat1.st_ino == stat2.st_ino)
        except OSError:
            return False

    def cleanup_release(self, release_dir: str, keep_source: bool = True) -> bool:
        """
        Clean up a release directory structure.

        Removes the release directory and all its contents. If the media file
        is a hardlink, the source file is preserved.

        Args:
            release_dir: Path to the release directory
            keep_source: Whether to verify source file preservation (default True)

        Returns:
            True if cleanup succeeded

        Note:
            This will NOT delete the original source file if it was hardlinked.
            Hardlinks share the same inode, so deleting one copy doesn't affect
            the other.
        """
        release_path = Path(release_dir)

        if not release_path.exists():
            logger.warning(f"Release directory not found: {release_dir}")
            return False

        try:
            shutil.rmtree(str(release_path))
            logger.info(f"✓ Cleaned up release directory: {release_dir}")
            return True

        except Exception as e:
            logger.error(f"Failed to cleanup release directory: {e}")
            return False

    def verify_release_structure(self, release_dir: str) -> Dict[str, bool]:
        """
        Verify that a release structure is complete.

        Args:
            release_dir: Path to the release directory

        Returns:
            Dictionary with verification results:
                {
                    'exists': bool,          # Release dir exists
                    'has_media': bool,       # Media file exists
                    'has_nfo': bool,         # NFO file exists
                    'has_screens_dir': bool, # Screens directory exists
                    'screen_count': int      # Number of screenshots
                }
        """
        release_path = Path(release_dir)

        result = {
            'exists': release_path.exists(),
            'has_media': False,
            'has_nfo': False,
            'has_screens_dir': False,
            'screen_count': 0
        }

        if not result['exists']:
            return result

        # Check for media file (any video extension)
        video_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mov', '.wmv'}
        for file in release_path.iterdir():
            if file.suffix.lower() in video_extensions:
                result['has_media'] = True
                break

        # Check for NFO
        nfo_files = list(release_path.glob("*.nfo"))
        result['has_nfo'] = len(nfo_files) > 0

        # Check screens directory
        screens_dir = release_path / "screens"
        result['has_screens_dir'] = screens_dir.exists()

        if result['has_screens_dir']:
            # Count screenshots
            screen_files = list(screens_dir.glob("*.png")) + list(screens_dir.glob("*.jpg"))
            result['screen_count'] = len(screen_files)

        return result

    def get_disk_usage(self, release_dir: str) -> Dict[str, int]:
        """
        Get disk usage statistics for a release directory.

        Args:
            release_dir: Path to the release directory

        Returns:
            Dictionary with disk usage:
                {
                    'total_size': int,      # Total size in bytes
                    'media_size': int,      # Media file size
                    'nfo_size': int,        # NFO file size
                    'screens_size': int,    # Screenshots total size
                    'file_count': int       # Total file count
                }
        """
        release_path = Path(release_dir)

        result = {
            'total_size': 0,
            'media_size': 0,
            'nfo_size': 0,
            'screens_size': 0,
            'file_count': 0
        }

        if not release_path.exists():
            return result

        video_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mov', '.wmv'}

        for file in release_path.rglob("*"):
            if file.is_file():
                size = file.stat().st_size
                result['total_size'] += size
                result['file_count'] += 1

                if file.suffix.lower() in video_extensions:
                    result['media_size'] += size
                elif file.suffix.lower() == '.nfo':
                    result['nfo_size'] += size
                elif file.parent.name == 'screens':
                    result['screens_size'] += size

        return result


# Singleton instance for convenience
_hardlink_manager: Optional[HardlinkManager] = None


def get_hardlink_manager(default_output_dir: Optional[str] = None) -> HardlinkManager:
    """
    Get the HardlinkManager singleton instance.

    Args:
        default_output_dir: Default output directory (used on first call)

    Returns:
        HardlinkManager instance
    """
    global _hardlink_manager
    if _hardlink_manager is None:
        _hardlink_manager = HardlinkManager(default_output_dir)
    return _hardlink_manager
