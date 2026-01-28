"""
ScreenshotGenerator Service for Seedarr v2.1

This module provides functionality for generating screenshots from video files
using FFmpeg. Screenshots are captured at configurable timestamps (default: 15%,
40%, 60%, 85% of video duration) to showcase different parts of the content.

Features:
    - Automatic video duration detection via ffprobe
    - Configurable number of screenshots (default: 4)
    - Dynamic timestamp calculation based on video length
    - Async subprocess execution for non-blocking operation
    - Graceful degradation if FFmpeg is not available

Requirements:
    - FFmpeg and FFprobe must be installed and accessible via PATH
    - Or set FFMPEG_PATH and FFPROBE_PATH environment variables

Usage Example:
    generator = ScreenshotGenerator()

    # Generate 4 screenshots
    paths = await generator.generate_screenshots(
        video_path="/media/movie.mkv",
        output_dir="/output/screens",
        release_name="Movie.2024.1080p.BluRay"
    )
    # paths = [
    #     "/output/screens/Movie.2024.1080p.BluRay_001.png",
    #     "/output/screens/Movie.2024.1080p.BluRay_002.png",
    #     ...
    # ]
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class ScreenshotError(Exception):
    """Exception raised when screenshot generation fails."""
    pass


class ScreenshotGenerator:
    """
    Generator for video screenshots using FFmpeg.

    This class handles the extraction of screenshots from video files at
    specific timestamps. It uses ffprobe to determine video duration and
    ffmpeg to capture frames.

    Timestamp Strategy:
        By default, screenshots are captured at 15%, 40%, 60%, and 85%
        of the video duration. This avoids intro/credits and shows
        representative content from throughout the video.

    Attributes:
        ffmpeg_path: Path to ffmpeg executable
        ffprobe_path: Path to ffprobe executable
        default_timestamps: Default timestamp percentages for screenshots
    """

    # Default timestamp percentages (avoiding intro and credits)
    DEFAULT_TIMESTAMPS = [0.15, 0.40, 0.60, 0.85]

    def __init__(
        self,
        ffmpeg_path: Optional[str] = None,
        ffprobe_path: Optional[str] = None
    ):
        """
        Initialize ScreenshotGenerator.

        Args:
            ffmpeg_path: Path to ffmpeg (auto-detected if not specified)
            ffprobe_path: Path to ffprobe (auto-detected if not specified)
        """
        self.ffmpeg_path = ffmpeg_path or os.getenv('FFMPEG_PATH') or self._find_executable('ffmpeg')
        self.ffprobe_path = ffprobe_path or os.getenv('FFPROBE_PATH') or self._find_executable('ffprobe')

        if not self.ffmpeg_path:
            logger.warning("FFmpeg not found - screenshot generation will be unavailable")
        if not self.ffprobe_path:
            logger.warning("FFprobe not found - video duration detection will be unavailable")

    def _find_executable(self, name: str) -> Optional[str]:
        """
        Find executable in PATH.

        Args:
            name: Executable name (e.g., 'ffmpeg')

        Returns:
            Full path to executable or None if not found
        """
        path = shutil.which(name)
        if path:
            logger.debug(f"Found {name} at: {path}")
        return path

    def is_available(self) -> bool:
        """
        Check if screenshot generation is available.

        Returns:
            True if both ffmpeg and ffprobe are available
        """
        return bool(self.ffmpeg_path and self.ffprobe_path)

    async def get_video_duration(self, video_path: str) -> float:
        """
        Get video duration in seconds using ffprobe.

        Args:
            video_path: Path to the video file

        Returns:
            Duration in seconds

        Raises:
            ScreenshotError: If duration cannot be determined
        """
        if not self.ffprobe_path:
            raise ScreenshotError("FFprobe not available")

        if not Path(video_path).exists():
            raise ScreenshotError(f"Video file not found: {video_path}")

        try:
            cmd = [
                self.ffprobe_path,
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ]

            logger.debug(f"Running ffprobe: {' '.join(cmd)}")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode().strip() if stderr else "Unknown error"
                raise ScreenshotError(f"FFprobe failed: {error_msg}")

            duration_str = stdout.decode().strip()
            duration = float(duration_str)

            logger.info(f"Video duration: {duration:.2f}s ({duration/60:.1f}m)")
            return duration

        except ValueError as e:
            raise ScreenshotError(f"Could not parse duration: {e}") from e

        except Exception as e:
            raise ScreenshotError(f"Error getting video duration: {e}") from e

    async def generate_screenshots(
        self,
        video_path: str,
        output_dir: str,
        release_name: str,
        count: int = 4,
        timestamps: Optional[List[float]] = None,
        format: str = 'png'
    ) -> List[str]:
        """
        Generate screenshots from a video file.

        Screenshots are captured at specific timestamps (percentages of duration)
        and saved to the output directory with sequential naming.

        Args:
            video_path: Path to the video file
            output_dir: Directory to save screenshots
            release_name: Base name for screenshot files
            count: Number of screenshots to generate (default: 4)
            timestamps: Custom timestamp percentages (0.0-1.0), overrides count
            format: Output format ('png' or 'jpg')

        Returns:
            List of paths to generated screenshot files

        Raises:
            ScreenshotError: If screenshot generation fails
        """
        if not self.is_available():
            raise ScreenshotError(
                "Screenshot generation unavailable - FFmpeg/FFprobe not found. "
                "Screenshots are optional; pipeline will continue without them."
            )

        video_path = Path(video_path)
        output_path = Path(output_dir)

        if not video_path.exists():
            raise ScreenshotError(f"Video file not found: {video_path}")

        # Create output directory
        output_path.mkdir(parents=True, exist_ok=True)

        # Get video duration
        duration = await self.get_video_duration(str(video_path))

        # Determine timestamps
        if timestamps:
            pct_timestamps = timestamps[:count]
        else:
            # Use default timestamps, adjusted for count
            if count == 4:
                pct_timestamps = self.DEFAULT_TIMESTAMPS
            else:
                # Evenly distribute timestamps
                pct_timestamps = [
                    (i + 1) / (count + 1)
                    for i in range(count)
                ]

        # Calculate actual timestamps in seconds
        time_timestamps = [pct * duration for pct in pct_timestamps]

        logger.info(
            f"Generating {len(time_timestamps)} screenshots at "
            f"{', '.join([f'{t:.0f}s' for t in time_timestamps])}"
        )

        # Generate screenshots
        screenshot_paths = []
        for i, timestamp in enumerate(time_timestamps, 1):
            filename = f"{release_name}_{i:03d}.{format}"
            output_file = output_path / filename

            try:
                await self._capture_frame(
                    video_path=str(video_path),
                    output_file=str(output_file),
                    timestamp=timestamp
                )
                screenshot_paths.append(str(output_file))
                logger.debug(f"Generated screenshot {i}/{len(time_timestamps)}: {filename}")

            except Exception as e:
                logger.error(f"Failed to capture screenshot at {timestamp}s: {e}")
                # Continue with remaining screenshots

        if not screenshot_paths:
            raise ScreenshotError("No screenshots were generated successfully")

        logger.info(f"✓ Generated {len(screenshot_paths)} screenshots")
        return screenshot_paths

    async def _capture_frame(
        self,
        video_path: str,
        output_file: str,
        timestamp: float
    ) -> None:
        """
        Capture a single frame from video.

        Args:
            video_path: Path to the video file
            output_file: Path for output screenshot
            timestamp: Time position in seconds

        Raises:
            ScreenshotError: If capture fails
        """
        cmd = [
            self.ffmpeg_path,
            '-y',  # Overwrite output
            '-ss', str(timestamp),  # Seek position
            '-i', video_path,
            '-vframes', '1',  # Single frame
            '-q:v', '2',  # Quality (2 = high for JPEG, ignored for PNG)
            output_file
        ]

        logger.debug(f"Capturing frame at {timestamp}s -> {Path(output_file).name}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        _, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip() if stderr else "Unknown error"
            raise ScreenshotError(f"FFmpeg capture failed: {error_msg}")

        # Verify file was created
        if not Path(output_file).exists():
            raise ScreenshotError(f"Screenshot file not created: {output_file}")

    async def generate_thumbnails(
        self,
        screenshot_paths: List[str],
        thumb_size: Tuple[int, int] = (320, 180)
    ) -> List[str]:
        """
        Generate thumbnail versions of screenshots.

        Args:
            screenshot_paths: List of screenshot file paths
            thumb_size: Thumbnail dimensions (width, height)

        Returns:
            List of thumbnail file paths
        """
        if not self.ffmpeg_path:
            raise ScreenshotError("FFmpeg not available for thumbnail generation")

        thumbnail_paths = []

        for screenshot in screenshot_paths:
            path = Path(screenshot)
            thumb_path = path.parent / f"{path.stem}_thumb{path.suffix}"

            cmd = [
                self.ffmpeg_path,
                '-y',
                '-i', screenshot,
                '-vf', f'scale={thumb_size[0]}:{thumb_size[1]}',
                str(thumb_path)
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            await process.communicate()

            if process.returncode == 0 and thumb_path.exists():
                thumbnail_paths.append(str(thumb_path))
                logger.debug(f"Generated thumbnail: {thumb_path.name}")

        logger.info(f"Generated {len(thumbnail_paths)} thumbnails")
        return thumbnail_paths

    def cleanup_screenshots(self, output_dir: str) -> int:
        """
        Remove all screenshots from a directory.

        Args:
            output_dir: Directory containing screenshots

        Returns:
            Number of files removed
        """
        output_path = Path(output_dir)
        if not output_path.exists():
            return 0

        removed = 0
        for file in output_path.glob("*.png"):
            try:
                file.unlink()
                removed += 1
            except Exception as e:
                logger.warning(f"Failed to remove {file}: {e}")

        for file in output_path.glob("*.jpg"):
            try:
                file.unlink()
                removed += 1
            except Exception as e:
                logger.warning(f"Failed to remove {file}: {e}")

        logger.info(f"Cleaned up {removed} screenshot files")
        return removed


# Singleton instance
_screenshot_generator: Optional[ScreenshotGenerator] = None


def get_screenshot_generator(
    ffmpeg_path: Optional[str] = None,
    ffprobe_path: Optional[str] = None
) -> ScreenshotGenerator:
    """
    Get the ScreenshotGenerator singleton instance.

    Args:
        ffmpeg_path: Path to ffmpeg (used on first call)
        ffprobe_path: Path to ffprobe (used on first call)

    Returns:
        ScreenshotGenerator instance
    """
    global _screenshot_generator
    if _screenshot_generator is None:
        _screenshot_generator = ScreenshotGenerator(ffmpeg_path, ffprobe_path)
    return _screenshot_generator
