"""
NFO Generator Service for Seedarr v2.0

This module generates technical NFO files with detailed MediaInfo data,
following the scene release format with support for:
- Multiple audio tracks
- Multiple subtitle tracks
- Detailed video information (codec, HDR, resolution, etc.)

The generated NFO format matches professional scene releases.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VideoTrack:
    """Video track information."""
    format: str = ""
    format_profile: str = ""
    codec_id: str = ""
    bitrate: str = ""
    resolution: str = ""
    width: int = 0
    height: int = 0
    frame_rate: str = ""
    frame_rate_mode: str = ""
    color_space: str = ""
    chroma_subsampling: str = ""
    bit_depth: str = ""
    stream_size: str = ""
    writing_library: str = ""
    hdr_format: str = ""


@dataclass
class AudioTrack:
    """Audio track information."""
    format: str = ""
    codec_id: str = ""
    bitrate_mode: str = ""
    bitrate: str = ""
    channels: int = 0
    channel_layout: str = ""
    sampling_rate: str = ""
    stream_size: str = ""
    language: str = ""
    title: str = ""


@dataclass
class SubtitleTrack:
    """Subtitle track information."""
    format: str = ""
    language: str = ""
    elements: int = 0
    title: str = ""


@dataclass
class MediaInfoData:
    """Complete MediaInfo data for a file."""
    file_name: str = ""
    format: str = ""
    file_size: str = ""
    duration: str = ""
    overall_bitrate: str = ""
    video_tracks: List[VideoTrack] = field(default_factory=list)
    audio_tracks: List[AudioTrack] = field(default_factory=list)
    subtitle_tracks: List[SubtitleTrack] = field(default_factory=list)


class NFOGenerator:
    """
    Technical NFO file generator using MediaInfo.

    This class extracts detailed technical information from media files
    and generates NFO files in the scene release format.

    Example output:
        -------------------------------------------------------------------------------
                                     INFORMATION GENERALE
        -------------------------------------------------------------------------------
        Type.................: Movies

        -------------------------------------------------------------------------------
                                      DETAILS TECHNIQUES
        -------------------------------------------------------------------------------
        File Name............: Movie.2024.1080p.BluRay.x264.mkv
        Format...............: Matroska
        ...
    """

    def __init__(self):
        """Initialize NFOGenerator."""
        self._mediainfo_available = None

    def _check_mediainfo(self) -> bool:
        """Check if MediaInfo library is available."""
        if self._mediainfo_available is not None:
            return self._mediainfo_available

        try:
            from pymediainfo import MediaInfo
            # Test if MediaInfo can be loaded
            MediaInfo.can_parse()
            self._mediainfo_available = True
            logger.info("MediaInfo library is available")
        except Exception as e:
            self._mediainfo_available = False
            logger.warning(f"MediaInfo library not available: {e}")

        return self._mediainfo_available

    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        if size_bytes >= 1024**3:
            return f"{size_bytes / (1024**3):.2f} Go"
        elif size_bytes >= 1024**2:
            return f"{size_bytes / (1024**2):.2f} Mo"
        elif size_bytes >= 1024:
            return f"{size_bytes / 1024:.2f} Ko"
        return f"{size_bytes} bytes"

    def _format_duration(self, ms: int) -> str:
        """Format duration from milliseconds to human-readable format."""
        if not ms:
            return ""

        total_seconds = ms // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes:02d}mn")
        if seconds > 0:
            parts.append(f"{seconds:02d}s")

        return " ".join(parts)

    def _format_bitrate(self, bitrate: int) -> str:
        """Format bitrate in Kbps."""
        if not bitrate:
            return ""
        return f"{bitrate // 1000} Kbps"

    def _get_channel_positions(self, channels: int, layout: str) -> str:
        """Get channel positions description."""
        if layout:
            return layout

        channel_maps = {
            1: "Mono",
            2: "Stereo",
            6: "Front: L C R, Side: L R, LFE",
            8: "Front: L C R, Side: L R, Rear: L R, LFE"
        }
        return channel_maps.get(channels, f"{channels} channels")

    async def extract_mediainfo(self, file_path: str) -> MediaInfoData:
        """
        Extract MediaInfo data from a media file.

        Args:
            file_path: Path to the media file

        Returns:
            MediaInfoData object with all track information
        """
        if not self._check_mediainfo():
            logger.warning("MediaInfo not available, returning empty data")
            return MediaInfoData(file_name=Path(file_path).name)

        try:
            from pymediainfo import MediaInfo

            # Run MediaInfo parsing in thread pool to avoid blocking
            media_info = await asyncio.to_thread(MediaInfo.parse, file_path)

            data = MediaInfoData()

            for track in media_info.tracks:
                if track.track_type == "General":
                    data.file_name = track.file_name or Path(file_path).name
                    data.format = track.format or ""
                    data.file_size = self._format_size(track.file_size or 0)
                    data.duration = self._format_duration(track.duration or 0)
                    data.overall_bitrate = self._format_bitrate(track.overall_bit_rate or 0)

                elif track.track_type == "Video":
                    video = VideoTrack(
                        format=track.format or "",
                        format_profile=track.format_profile or "",
                        codec_id=track.codec_id or "",
                        bitrate=self._format_bitrate(track.bit_rate or 0),
                        width=track.width or 0,
                        height=track.height or 0,
                        resolution=f"{track.width}x{track.height}" if track.width and track.height else "",
                        frame_rate=f"{track.frame_rate} FPS" if track.frame_rate else "",
                        frame_rate_mode=track.frame_rate_mode or "",
                        color_space=track.color_space or "",
                        chroma_subsampling=track.chroma_subsampling or "",
                        bit_depth=f"{track.bit_depth} bits" if track.bit_depth else "",
                        stream_size=self._format_size(track.stream_size or 0),
                        writing_library=track.writing_library or track.encoded_library_string or "",
                        hdr_format=track.hdr_format or track.hdr_format_commercial or ""
                    )
                    data.video_tracks.append(video)

                elif track.track_type == "Audio":
                    audio = AudioTrack(
                        format=track.format or "",
                        codec_id=track.codec_id or "",
                        bitrate_mode=track.bit_rate_mode or "",
                        bitrate=self._format_bitrate(track.bit_rate or 0),
                        channels=track.channel_s or 0,
                        channel_layout=self._get_channel_positions(
                            track.channel_s or 0,
                            track.channel_layout or ""
                        ),
                        sampling_rate=f"{track.sampling_rate / 1000:.1f} KHz" if track.sampling_rate else "",
                        stream_size=self._format_size(track.stream_size or 0),
                        language=track.language or "",
                        title=track.title or ""
                    )
                    data.audio_tracks.append(audio)

                elif track.track_type == "Text":
                    subtitle = SubtitleTrack(
                        format=track.format or "UTF-8",
                        language=track.language or "",
                        elements=track.count_of_elements or 0,
                        title=track.title or ""
                    )
                    data.subtitle_tracks.append(subtitle)

            logger.info(f"Extracted MediaInfo: {len(data.video_tracks)} video, "
                       f"{len(data.audio_tracks)} audio, {len(data.subtitle_tracks)} subtitle tracks")

            return data

        except Exception as e:
            logger.error(f"Failed to extract MediaInfo from {file_path}: {e}")
            return MediaInfoData(file_name=Path(file_path).name)

    def generate_nfo_content(
        self,
        media_data: MediaInfoData,
        media_type: str = "Movies",
        release_name: Optional[str] = None
    ) -> str:
        """
        Generate NFO content from MediaInfo data.

        Args:
            media_data: MediaInfoData object with extracted information
            media_type: Type of media (Movies, Series, etc.)
            release_name: Optional release name to display as filename in NFO

        Returns:
            Formatted NFO content string
        """
        lines = []
        separator = "-" * 79

        # Use release_name if provided, otherwise use original filename
        display_filename = release_name if release_name else media_data.file_name

        # Extract technical summary from MediaInfo for C411 compatibility
        video_codec = ""
        video_resolution = ""
        audio_codec = ""
        source = ""

        if media_data.video_tracks:
            video = media_data.video_tracks[0]
            # Show actual codec format (like Plex does)
            v_format = (video.format or "").upper()
            if "HEVC" in v_format:
                video_codec = "HEVC"
            elif "AVC" in v_format:
                video_codec = "H264"
            elif "H265" in v_format:
                video_codec = "H265"
            elif "H264" in v_format:
                video_codec = "H264"
            elif "AV1" in v_format:
                video_codec = "AV1"
            elif "VP9" in v_format:
                video_codec = "VP9"
            else:
                video_codec = video.format or ""

            # Resolution
            if video.width and video.height:
                if video.height >= 2160 or video.width >= 3840:
                    video_resolution = "2160p"
                elif video.height >= 1080 or video.width >= 1920:
                    video_resolution = "1080p"
                elif video.height >= 720 or video.width >= 1280:
                    video_resolution = "720p"
                else:
                    video_resolution = f"{video.height}p"

        if media_data.audio_tracks:
            audio = media_data.audio_tracks[0]
            a_format = (audio.format or "").upper()
            if "E-AC-3" in a_format or "EAC3" in a_format:
                audio_codec = "EAC3"
            elif "AC-3" in a_format or "AC3" in a_format:
                audio_codec = "AC3"
            elif "DTS-HD MA" in a_format:
                audio_codec = "DTS-HD.MA"
            elif "TRUEHD" in a_format:
                audio_codec = "TrueHD"
            elif "DTS" in a_format:
                audio_codec = "DTS"
            elif "AAC" in a_format:
                audio_codec = "AAC"
            elif "FLAC" in a_format:
                audio_codec = "FLAC"
            else:
                audio_codec = audio.format or ""

        # Detect source from release name (check specific patterns first)
        if release_name:
            import re
            rn_upper = release_name.upper()
            if "REMUX" in rn_upper:
                source = "REMUX"
            elif "BLURAY" in rn_upper or "BLU-RAY" in rn_upper:
                source = "BluRay"
            elif re.search(r'WEB[\.\-]?DL', rn_upper):
                source = "WEB-DL"
            elif "WEBRIP" in rn_upper:
                source = "WEBRip"
            elif "HDTV" in rn_upper:
                source = "HDTV"
            elif "DVDRIP" in rn_upper:
                source = "DVDRip"
            elif "HDRIP" in rn_upper:
                source = "HDRip"
            elif re.search(r'\.WEB\.', rn_upper):
                source = "WEB"

        # General Information
        lines.append(separator)
        lines.append("                             INFORMATION GENERALE")
        lines.append(separator)
        lines.append(f"Type.................: {media_type}")
        lines.append("")

        # Technical Summary (for C411 parser compatibility)
        # Using ASCII-safe field names to avoid encoding issues
        lines.append(separator)
        lines.append("                               RESUME TECHNIQUE")
        lines.append(separator)
        lines.append(f"Source...............: {source}")
        lines.append(f"Resolution...........: {video_resolution}")
        lines.append(f"Codec Video..........: {video_codec}")
        lines.append(f"Codec Audio..........: {audio_codec}")
        lines.append("")

        # Technical Details Header
        lines.append(separator)
        lines.append("                              DETAILS TECHNIQUES")
        lines.append(separator)

        # General Info
        lines.append(separator)
        lines.append("                                 GENERAL INFO")
        lines.append(separator)
        lines.append(f"File Name............: {display_filename}")
        lines.append(f"Format...............: {media_data.format}")
        lines.append(f"File Size............: {media_data.file_size}")
        lines.append(f"Duration.............: {media_data.duration}")
        lines.append(f"Overall Bitrate......: {media_data.overall_bitrate}")
        lines.append("")

        # Video Tracks
        for i, video in enumerate(media_data.video_tracks, 1):
            lines.append(separator)
            lines.append(f"                                 VIDEO INFO #{i}")
            lines.append(separator)
            lines.append(f"Format...............: {video.format}")
            if video.format_profile:
                lines.append(f"Format Profile.......: {video.format_profile}")
            if video.codec_id:
                lines.append(f"Codec ID.............: {video.codec_id}")
            if video.bitrate:
                lines.append(f"Bitrate..............: {video.bitrate}")
            if video.resolution:
                lines.append(f"Resolution...........: {video.resolution}")
            if video.frame_rate:
                lines.append(f"Frame Rate...........: {video.frame_rate}")
            if video.frame_rate_mode:
                lines.append(f"Frame Rate Mode......: {video.frame_rate_mode}")
            if video.color_space:
                lines.append(f"Color Space..........: {video.color_space}")
            if video.chroma_subsampling:
                lines.append(f"Chroma Subsampling...: {video.chroma_subsampling}")
            if video.bit_depth:
                lines.append(f"Bit Depth............: {video.bit_depth}")
            if video.stream_size:
                lines.append(f"Stream Size..........: {video.stream_size}")
            if video.writing_library:
                lines.append(f"Writing Library......: {video.writing_library}")
            if video.hdr_format:
                lines.append(f"HDR Format...........: {video.hdr_format}")
            lines.append("")

        # Audio Tracks
        for i, audio in enumerate(media_data.audio_tracks, 1):
            lines.append(separator)
            lines.append(f"                                 AUDIO INFO #{i}")
            lines.append(separator)
            lines.append(f"Format...............: {audio.format}")
            if audio.codec_id:
                lines.append(f"Codec ID.............: {audio.codec_id}")
            if audio.bitrate_mode:
                lines.append(f"Bitrate Mode.........: {audio.bitrate_mode}")
            if audio.bitrate:
                lines.append(f"Bitrate..............: {audio.bitrate}")
            if audio.channels:
                lines.append(f"Channels.............: {audio.channels}")
            if audio.channel_layout:
                lines.append(f"Channel Positions....: {audio.channel_layout}")
            if audio.sampling_rate:
                lines.append(f"Sampling Rate........: {audio.sampling_rate}")
            if audio.stream_size:
                lines.append(f"Stream Size..........: {audio.stream_size}")
            if audio.language:
                lines.append(f"Language.............: {audio.language}")
            if audio.title:
                lines.append(f"Title................: {audio.title}")
            lines.append("")

        # Subtitle Tracks
        if media_data.subtitle_tracks:
            lines.append(separator)
            lines.append("                                   SUBTITLES")
            lines.append(separator)
            for i, sub in enumerate(media_data.subtitle_tracks, 1):
                lang = sub.language or "und"
                fmt = f"({sub.format})" if sub.format else "(UTF-8)"
                elements = sub.elements if sub.elements else ""
                lines.append(f"Subtitle #{i}..........: {lang} {fmt} {elements}")
            lines.append("")

        # Footer
        lines.append("")
        lines.append(separator)
        lines.append("                             Partager & Preserver")
        lines.append(separator)
        lines.append("")

        return "\n".join(lines)

    async def generate_nfo(
        self,
        file_path: str,
        output_path: Optional[str] = None,
        media_type: str = "Movies",
        release_name: Optional[str] = None
    ) -> str:
        """
        Generate a complete NFO file for a media file.

        Args:
            file_path: Path to the media file
            output_path: Optional path to save the NFO file
                        (defaults to release_name.nfo or same name with .nfo extension)
            media_type: Type of media (Movies, Series, etc.)
            release_name: Optional release name for NFO filename and content

        Returns:
            Path to the generated NFO file
        """
        logger.info(f"Generating NFO for: {file_path}")

        # Extract MediaInfo
        media_data = await self.extract_mediainfo(file_path)

        # Generate NFO content with release name for display
        nfo_content = self.generate_nfo_content(media_data, media_type, release_name)

        # Determine output path
        if not output_path:
            if release_name:
                # Use release_name for NFO filename (in same directory as media file)
                output_path = str(Path(file_path).parent / f"{release_name}.nfo")
            else:
                output_path = str(Path(file_path).with_suffix('.nfo'))

        # Write NFO file
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(nfo_content)
            logger.info(f"NFO generated successfully: {output_path}")
        except Exception as e:
            logger.error(f"Failed to write NFO file: {e}")
            raise

        return output_path


# Singleton instance for convenience
_nfo_generator: Optional[NFOGenerator] = None


def get_nfo_generator() -> NFOGenerator:
    """Get the singleton NFOGenerator instance."""
    global _nfo_generator
    if _nfo_generator is None:
        _nfo_generator = NFOGenerator()
    return _nfo_generator
