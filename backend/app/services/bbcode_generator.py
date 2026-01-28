"""
BBCode Generator Service for Seedarr v2.0

This module generates BBCode descriptions for tracker uploads,
combining TMDB metadata (poster, title, synopsis, ratings) with
MediaInfo technical details. Supports customizable templates.

Example output:
    [center]
    [img]https://image.tmdb.org/t/p/w500/poster.jpg[/img]

    [size=6][color=#eab308][b]Movie Title (2024)[/b][/color][/size]

    [b]Note :[/b] 7.5/10
    [b]Genre :[/b] Action, Adventure

    [quote]Movie synopsis here...[/quote]
    ...
"""

import re
import logging
from typing import Optional, Dict, Any, List, Union
from dataclasses import dataclass

from .nfo_generator import MediaInfoData, NFOGenerator, get_nfo_generator

logger = logging.getLogger(__name__)


def normalize_genres(genres: List[Union[str, Dict[str, Any]]]) -> List[str]:
    """
    Normalize genre list to string format.

    Handles both old format (list of strings) and new format (list of {id, name} dicts).

    Args:
        genres: List of genre strings or dicts

    Returns:
        List of genre name strings

    Examples:
        >>> normalize_genres(["Action", "Thriller"])
        ['Action', 'Thriller']
        >>> normalize_genres([{"id": 28, "name": "Action"}, {"id": 53, "name": "Thriller"}])
        ['Action', 'Thriller']
    """
    if not genres:
        return []

    result = []
    for g in genres:
        if isinstance(g, dict):
            name = g.get('name', '')
            if name:
                result.append(name)
        elif isinstance(g, str):
            result.append(g)
    return result


@dataclass
class CastMember:
    """Cast member data."""
    name: str = ""
    character: str = ""
    profile_path: str = ""  # TMDB profile path or full URL

    @property
    def photo_url(self) -> str:
        """Get full photo URL."""
        if not self.profile_path:
            return "https://via.placeholder.com/185x278?text=No+Photo"
        if self.profile_path.startswith("http"):
            return self.profile_path
        return f"https://image.tmdb.org/t/p/w185{self.profile_path}"


@dataclass
class TMDBData:
    """TMDB metadata for BBCode generation."""
    title: str = ""
    original_title: str = ""
    year: int = 0
    release_date: str = ""  # Formatted date like "mardi 13 janvier 2026"
    poster_url: str = ""  # Can be full URL or TMDB path
    backdrop_url: str = ""  # Backdrop image URL
    vote_average: float = 0.0
    genres: List[str] = None
    overview: str = ""
    tagline: str = ""  # Movie tagline/slogan
    runtime: int = 0  # Runtime in minutes
    country: str = ""  # Production country
    director: str = ""  # Director name(s)
    tmdb_id: str = ""
    imdb_id: str = ""
    tmdb_url: str = ""  # Full TMDB URL
    trailer_url: str = ""  # YouTube trailer URL
    cast: List[CastMember] = None  # Top 6 cast members

    def __post_init__(self):
        if self.genres is None:
            self.genres = []
        if self.cast is None:
            self.cast = []

    @property
    def runtime_formatted(self) -> str:
        """Format runtime as 'Xh et Ymin'."""
        if not self.runtime:
            return ""
        hours = self.runtime // 60
        minutes = self.runtime % 60
        if hours > 0 and minutes > 0:
            return f"{hours}h et {minutes}min"
        elif hours > 0:
            return f"{hours}h"
        else:
            return f"{minutes}min"


class BBCodeGenerator:
    """
    BBCode description generator for La Cale tracker.

    Combines TMDB metadata with MediaInfo technical details to produce
    formatted BBCode descriptions for torrent uploads.
    """

    # Language code to display name mapping
    LANGUAGE_MAP = {
        "fr": "Français",
        "fra": "Français",
        "fre": "Français",
        "french": "Français",
        "en": "Anglais",
        "eng": "Anglais",
        "english": "Anglais",
        "es": "Espagnol",
        "spa": "Espagnol",
        "spanish": "Espagnol",
        "de": "Allemand",
        "deu": "Allemand",
        "ger": "Allemand",
        "german": "Allemand",
        "it": "Italien",
        "ita": "Italien",
        "italian": "Italien",
        "pt": "Portugais",
        "por": "Portugais",
        "portuguese": "Portugais",
        "ja": "Japonais",
        "jpn": "Japonais",
        "japanese": "Japonais",
        "ko": "Coréen",
        "kor": "Coréen",
        "korean": "Coréen",
        "zh": "Chinois",
        "zho": "Chinois",
        "chi": "Chinois",
        "chinese": "Chinois",
        "ru": "Russe",
        "rus": "Russe",
        "russian": "Russe",
        "ar": "Arabe",
        "ara": "Arabe",
        "arabic": "Arabe",
    }

    # Audio version type detection
    AUDIO_VERSION_MAP = {
        "vff": "VFF",
        "vf": "VF",
        "vfq": "VFQ",
        "vfi": "VFI",
        "vo": "VO",
        "vost": "VOST",
        "vostfr": "VOSTFR",
    }

    def __init__(self):
        """Initialize BBCodeGenerator."""
        self.nfo_generator = get_nfo_generator()

    def _get_language_name(self, lang_code: str) -> str:
        """Convert language code to display name."""
        if not lang_code:
            return "Inconnu"
        lang_lower = lang_code.lower().strip()
        return self.LANGUAGE_MAP.get(lang_lower, lang_code.title())

    def _detect_audio_version(self, title: str, language: str) -> str:
        """Detect audio version type from title or language."""
        if title:
            title_lower = title.lower()
            for key, version in self.AUDIO_VERSION_MAP.items():
                if key in title_lower:
                    return version

        # Default based on language
        lang_lower = language.lower() if language else ""
        if lang_lower in ["fr", "fra", "fre", "french"]:
            return "VFF"
        elif lang_lower in ["en", "eng", "english"]:
            return "VO"
        return ""

    def _detect_resolution_from_filename(self, filename: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
        """
        Detect resolution from filename patterns.

        Returns:
            Tuple of (height, resolution_label, quality_label) or (None, None, None)
        """
        filename_lower = filename.lower()

        # Common resolution patterns in filenames
        if "2160p" in filename_lower or "4k" in filename_lower or "uhd" in filename_lower:
            return (2160, "2160p", "4K")
        elif "1080p" in filename_lower:
            return (1080, "1080p", "Full HD")
        elif "720p" in filename_lower:
            return (720, "720p", "HD")
        elif "576p" in filename_lower:
            return (576, "576p", "SD")
        elif "480p" in filename_lower:
            return (480, "480p", "SD")

        return (None, None, None)

    def _get_quality_string(self, media_data: MediaInfoData) -> str:
        """Generate quality string from video resolution."""
        filename_lower = media_data.file_name.lower()

        # First try to get resolution from filename (more reliable for scene releases)
        filename_height, filename_res, filename_quality = self._detect_resolution_from_filename(media_data.file_name)

        # Get MediaInfo dimensions
        mediainfo_height = 0
        mediainfo_width = 0
        if media_data.video_tracks:
            mediainfo_height = media_data.video_tracks[0].height
            mediainfo_width = media_data.video_tracks[0].width

        # Determine resolution from MediaInfo (check BOTH width and height for widescreen formats)
        # Cinémascope 2.39:1 has width 3840 but height ~1600, still 4K!
        if mediainfo_width >= 3840 or mediainfo_height >= 2160:
            mediainfo_res = "2160p"
            mediainfo_quality = "4K"
            effective_height = 2160
        elif mediainfo_width >= 1920 or mediainfo_height >= 1080:
            mediainfo_res = "1080p"
            mediainfo_quality = "Full HD"
            effective_height = 1080
        elif mediainfo_width >= 1280 or mediainfo_height >= 720:
            mediainfo_res = "720p"
            mediainfo_quality = "HD"
            effective_height = 720
        elif mediainfo_height >= 576:
            mediainfo_res = "576p"
            mediainfo_quality = "SD"
            effective_height = 576
        elif mediainfo_height >= 480:
            mediainfo_res = "480p"
            mediainfo_quality = "SD"
            effective_height = 480
        elif mediainfo_height > 0:
            mediainfo_res = f"{mediainfo_height}p"
            mediainfo_quality = "SD"
            effective_height = mediainfo_height
        else:
            mediainfo_res = None
            mediainfo_quality = None
            effective_height = 0

        # Use filename resolution if it suggests higher quality than MediaInfo
        if filename_height and filename_height > effective_height:
            res = filename_res
            quality = filename_quality
            logger.debug(f"Using filename resolution ({res}) over MediaInfo ({mediainfo_res})")
        elif mediainfo_res:
            res = mediainfo_res
            quality = mediainfo_quality
        elif filename_height:
            # Fallback to filename if no MediaInfo
            res = filename_res
            quality = filename_quality
        else:
            return "Unknown"

        # Detect source from filename
        if "bluray" in filename_lower or "blu-ray" in filename_lower:
            source = "BluRay"
        elif "webrip" in filename_lower:
            source = "WEBRip"
        elif "webdl" in filename_lower or "web-dl" in filename_lower:
            source = "WEB-DL"
        elif "hdtv" in filename_lower:
            source = "HDTV"
        elif "dvdrip" in filename_lower:
            source = "DVDRip"
        elif "mhd" in filename_lower:
            source = "mHD"
        elif "amz" in filename_lower or "amazon" in filename_lower:
            source = "WEBRip AMZ"
        elif "nf" in filename_lower or "netflix" in filename_lower:
            source = "WEBRip NF"
        elif "dsnp" in filename_lower or "disney" in filename_lower:
            source = "WEBRip DSNP"
        else:
            source = "WEB"

        return f"{res} {source} ({quality})"

    def _get_format_string(self, media_data: MediaInfoData) -> str:
        """Generate format string (container + video codec)."""
        container = media_data.format or "MKV"

        if not media_data.video_tracks:
            return container

        video = media_data.video_tracks[0]
        codec_profile = video.format_profile or ""

        return f"{container} ({video.format} {codec_profile})".strip()

    def _get_hdr_string(self, media_data: MediaInfoData) -> str:
        """Generate HDR/rendering string."""
        if not media_data.video_tracks:
            return ""

        video = media_data.video_tracks[0]
        filename_lower = media_data.file_name.lower()

        hdr_types = []

        # Check HDR format from MediaInfo
        if video.hdr_format:
            hdr_types.append(video.hdr_format)

        # Check filename for HDR indicators
        if "dv" in filename_lower or "dolby.vision" in filename_lower or "dolbyvision" in filename_lower:
            if "Dolby Vision" not in " ".join(hdr_types):
                hdr_types.append("Dolby Vision")

        if "hdr10+" in filename_lower or "hdr10plus" in filename_lower:
            if "HDR10+" not in " ".join(hdr_types):
                hdr_types.append("HDR10+")
        elif "hdr10" in filename_lower or "hdr" in filename_lower:
            if "HDR10" not in " ".join(hdr_types) and "HDR10+" not in " ".join(hdr_types):
                hdr_types.append("HDR10")

        if "hlg" in filename_lower:
            hdr_types.append("HLG")

        if not hdr_types:
            # Check bit depth
            if video.bit_depth and "10" in video.bit_depth:
                return "HDR10"
            return "SDR"

        return " / ".join(hdr_types)

    def _get_video_codec_string(self, media_data: MediaInfoData) -> str:
        """Generate video codec string with bitrate."""
        if not media_data.video_tracks:
            return "Unknown"

        video = media_data.video_tracks[0]

        # Determine codec name
        codec = video.format
        if codec.upper() == "HEVC" or codec.upper() == "H265":
            codec = "H.265"
        elif codec.upper() == "AVC" or codec.upper() == "H264":
            codec = "H.264"

        bitrate = video.bitrate or "Unknown"

        return f"{codec} @ {bitrate}"

    def _get_audio_codec_list(self, media_data: MediaInfoData) -> List[str]:
        """Generate list of audio codec strings."""
        audio_lines = []

        for audio in media_data.audio_tracks:
            lang_name = self._get_language_name(audio.language)
            version = self._detect_audio_version(audio.title, audio.language)

            # Build language string
            if version:
                lang_str = f"{lang_name} ({version})"
            else:
                lang_str = lang_name

            # Build codec string
            codec = audio.format
            if codec.upper() == "E-AC-3" or codec.upper() == "EAC3":
                codec = "E-AC3"
            elif codec.upper() == "AC-3" or codec.upper() == "AC3":
                codec = "AC-3"
            elif codec.upper() == "DTS-HD MA":
                codec = "DTS-HD MA"
            elif codec.upper() == "TRUEHD":
                codec = "TrueHD"

            # Channel configuration
            channels = audio.channels
            if channels == 8:
                channel_str = "7.1"
            elif channels == 6:
                channel_str = "5.1"
            elif channels == 2:
                channel_str = "2.0"
            elif channels == 1:
                channel_str = "1.0"
            else:
                channel_str = f"{channels}ch"

            bitrate = audio.bitrate or ""

            audio_lines.append(f"- {lang_str} : {codec} {channel_str} @ {bitrate}")

        return audio_lines

    def _get_languages_string(self, media_data: MediaInfoData) -> str:
        """Generate languages summary string."""
        languages = []
        seen_langs = set()

        for audio in media_data.audio_tracks:
            lang_name = self._get_language_name(audio.language)
            version = self._detect_audio_version(audio.title, audio.language)

            if lang_name not in seen_langs:
                seen_langs.add(lang_name)
                if version and lang_name == "Français":
                    # Group French versions
                    french_versions = []
                    for a in media_data.audio_tracks:
                        if self._get_language_name(a.language) == "Français":
                            v = self._detect_audio_version(a.title, a.language)
                            if v and v not in french_versions:
                                french_versions.append(v)
                    if french_versions:
                        languages.append(f"Français ({' + '.join(french_versions)})")
                    else:
                        languages.append("Français")
                else:
                    languages.append(lang_name)

        # Remove duplicates while preserving order
        unique_languages = []
        for lang in languages:
            if lang not in unique_languages:
                unique_languages.append(lang)

        return ", ".join(unique_languages)

    def _get_subtitles_string(self, media_data: MediaInfoData) -> str:
        """Generate subtitles summary string."""
        if not media_data.subtitle_tracks:
            return "Aucun"

        subtitles = []
        seen_langs = set()

        for sub in media_data.subtitle_tracks:
            lang_name = self._get_language_name(sub.language)

            if lang_name not in seen_langs:
                seen_langs.add(lang_name)

                # Check for forced/SDH variants
                title_lower = (sub.title or "").lower()
                has_forced = any(
                    "forc" in (s.title or "").lower()
                    for s in media_data.subtitle_tracks
                    if self._get_language_name(s.language) == lang_name
                )
                has_sdh = any(
                    "sdh" in (s.title or "").lower() or "hearing" in (s.title or "").lower()
                    for s in media_data.subtitle_tracks
                    if self._get_language_name(s.language) == lang_name
                )

                variants = []
                variants.append("Complets")
                if has_forced:
                    variants.append("Forcés")
                if has_sdh:
                    variants.append("SDH")

                subtitles.append(f"{lang_name} ({' & '.join(variants)})")

        return ", ".join(subtitles)

    def _build_audio_table(self, media_data: MediaInfoData) -> str:
        """Generate BBCode table for audio tracks."""
        if not media_data.audio_tracks:
            return ""

        rows = []
        for audio in media_data.audio_tracks:
            lang_name = self._get_language_name(audio.language)
            version = self._detect_audio_version(audio.title, audio.language)
            lang_str = f"{lang_name} ({version})" if version else lang_name

            codec = audio.format
            channels = audio.channels
            if channels == 8:
                channel_str = "7.1"
            elif channels == 6:
                channel_str = "5.1"
            elif channels == 2:
                channel_str = "2.0"
            elif channels == 1:
                channel_str = "1.0"
            else:
                channel_str = f"{channels}ch"

            bitrate = audio.bitrate or "N/A"
            rows.append(f"[tr][td]{lang_str}[/td][td]{codec} {channel_str}[/td][td]{bitrate}[/td][/tr]")

        header = "[tr][td][b]Langue[/b][/td][td][b]Codec[/b][/td][td][b]Débit[/b][/td][/tr]"
        return f"[table]{header}{''.join(rows)}[/table]"

    def _build_subtitles_table(self, media_data: MediaInfoData) -> str:
        """Generate BBCode table for subtitle tracks."""
        if not media_data.subtitle_tracks:
            return "Aucun"

        rows = []
        for sub in media_data.subtitle_tracks:
            lang_name = self._get_language_name(sub.language)
            sub_format = sub.format or "SRT"
            title = sub.title or ""

            # Detect type
            title_lower = title.lower()
            if "forc" in title_lower:
                sub_type = "Forcés"
            elif "sdh" in title_lower or "hearing" in title_lower:
                sub_type = "SDH"
            else:
                sub_type = "Complets"

            rows.append(f"[tr][td]{lang_name}[/td][td]{sub_format}[/td][td]{sub_type}[/td][/tr]")

        header = "[tr][td][b]Langue[/b][/td][td][b]Format[/b][/td][td][b]Type[/b][/td][/tr]"
        return f"[table]{header}{''.join(rows)}[/table]"

    def generate_bbcode(
        self,
        media_data: MediaInfoData,
        tmdb_data: Optional[TMDBData] = None
    ) -> str:
        """
        Generate BBCode description from MediaInfo and TMDB data.

        Args:
            media_data: MediaInfoData object with technical information
            tmdb_data: Optional TMDBData object with movie/show metadata

        Returns:
            Formatted BBCode string
        """
        lines = []

        # Header section
        lines.append("[center]")

        # Poster
        if tmdb_data and tmdb_data.poster_url:
            # Check if it's already a complete URL or just a TMDB path
            if tmdb_data.poster_url.startswith("http"):
                poster_url = tmdb_data.poster_url
            else:
                poster_url = f"https://image.tmdb.org/t/p/w500{tmdb_data.poster_url}"
            lines.append(f"[img]{poster_url}[/img]")
        else:
            lines.append("[img]https://via.placeholder.com/500x750?text=No+Image[/img]")

        lines.append("")

        # Title and year
        if tmdb_data and tmdb_data.title:
            title = tmdb_data.title
            year = tmdb_data.year or ""
            lines.append(f"[size=6][color=#eab308][b]{title} ({year})[/b][/color][/size]")
        else:
            lines.append("[size=6][color=#eab308][b]Titre (Année)[/b][/color][/size]")

        lines.append("")

        # Rating
        if tmdb_data and tmdb_data.vote_average:
            rating = f"{tmdb_data.vote_average}/10"
        else:
            rating = "0/10"
        lines.append(f"[b]Note :[/b] {rating}")

        # Genres
        if tmdb_data and tmdb_data.genres:
            genres = ", ".join(tmdb_data.genres)
        else:
            genres = ""
        lines.append(f"[b]Genre :[/b] {genres}")

        lines.append("")

        # Synopsis
        if tmdb_data and tmdb_data.overview:
            lines.append(f"[quote]{tmdb_data.overview}[/quote]")
        else:
            lines.append("[quote][/quote]")

        lines.append("")

        # Technical details section
        lines.append("[color=#eab308][b]--- DÉTAILS ---[/b][/color]")
        lines.append("")

        # Quality
        quality = self._get_quality_string(media_data)
        lines.append(f"[b]Qualité :[/b] {quality}")

        # Format
        format_str = self._get_format_string(media_data)
        lines.append(f"[b]Format :[/b] {format_str}")

        # HDR/Rendering
        hdr = self._get_hdr_string(media_data)
        if hdr:
            lines.append(f"[b]Rendu :[/b] {hdr}")

        # Duration
        if media_data.duration:
            lines.append(f"[b]Durée :[/b] {media_data.duration}")

        # Video codec
        video_codec = self._get_video_codec_string(media_data)
        lines.append(f"[b]Codec Vidéo :[/b] {video_codec}")

        lines.append("")

        # Audio codecs
        lines.append("[b]Codec Audio :[/b]")
        for audio_line in self._get_audio_codec_list(media_data):
            lines.append(audio_line)

        lines.append("")

        # Languages
        languages = self._get_languages_string(media_data)
        lines.append(f"[b]Langues :[/b] {languages}")

        # Subtitles
        subtitles = self._get_subtitles_string(media_data)
        lines.append(f"[b]Sous-titres :[/b] {subtitles}")

        # File size
        lines.append(f"[b]Taille :[/b] {media_data.file_size}")

        lines.append("")
        lines.append("[/center]")

        return "\n".join(lines)

    def _convert_cast_from_dict(self, cast_list: Optional[List[Dict]]) -> List[CastMember]:
        """Convert cast data from dict format to CastMember objects."""
        if not cast_list:
            return []

        cast_members = []
        for i, actor in enumerate(cast_list[:6]):  # Take first 6 for templates
            cast_members.append(CastMember(
                name=actor.get("name", ""),
                character=actor.get("character", ""),
                profile_path=actor.get("profile_path", "") or actor.get("photo_url", ""),
            ))
        return cast_members

    async def generate_from_file(
        self,
        file_path: str,
        tmdb_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Generate BBCode from a media file.

        Args:
            file_path: Path to the media file
            tmdb_data: Optional dictionary with TMDB metadata

        Returns:
            Formatted BBCode string
        """
        # Extract MediaInfo
        media_data = await self.nfo_generator.extract_mediainfo(file_path)

        # Convert TMDB dict to dataclass if provided
        tmdb = None
        if tmdb_data:
            tmdb = TMDBData(
                title=tmdb_data.get("title", ""),
                original_title=tmdb_data.get("original_title", ""),
                year=tmdb_data.get("year", 0),
                poster_url=tmdb_data.get("poster_url", ""),
                vote_average=tmdb_data.get("vote_average", 0.0),
                genres=normalize_genres(tmdb_data.get("genres", [])),
                overview=tmdb_data.get("overview", ""),
                tmdb_id=str(tmdb_data.get("tmdb_id", "")),
                imdb_id=tmdb_data.get("imdb_id", ""),
                cast=self._convert_cast_from_dict(tmdb_data.get("cast", [])),
            )

        return self.generate_bbcode(media_data, tmdb)

    def _build_template_variables(
        self,
        media_data: MediaInfoData,
        tmdb_data: Optional[TMDBData] = None
    ) -> Dict[str, str]:
        """
        Build a dictionary of template variables from media and TMDB data.

        Args:
            media_data: MediaInfoData object with technical information
            tmdb_data: Optional TMDBData object with movie/show metadata

        Returns:
            Dictionary mapping variable names to their values
        """
        variables = {}

        # TMDB variables
        if tmdb_data:
            # Poster URL handling
            poster_url = tmdb_data.poster_url
            if poster_url and not poster_url.startswith("http"):
                poster_url = f"https://image.tmdb.org/t/p/w500{poster_url}"

            # Backdrop URL handling
            backdrop_url = tmdb_data.backdrop_url
            if backdrop_url and not backdrop_url.startswith("http"):
                backdrop_url = f"https://image.tmdb.org/t/p/w1280{backdrop_url}"

            variables["title"] = tmdb_data.title or "Titre"
            variables["original_title"] = tmdb_data.original_title or tmdb_data.title or ""
            variables["year"] = str(tmdb_data.year) if tmdb_data.year else ""
            variables["release_date"] = tmdb_data.release_date or ""
            variables["poster_url"] = poster_url or "https://via.placeholder.com/500x750?text=No+Image"
            variables["backdrop_url"] = backdrop_url or ""
            variables["rating"] = str(tmdb_data.vote_average) if tmdb_data.vote_average else "0"
            variables["rating_10"] = f"{tmdb_data.vote_average}/10" if tmdb_data.vote_average else "0/10"
            variables["genres"] = ", ".join(tmdb_data.genres) if tmdb_data.genres else ""
            variables["overview"] = tmdb_data.overview or ""
            variables["tagline"] = tmdb_data.tagline or ""
            variables["runtime"] = tmdb_data.runtime_formatted or ""
            variables["country"] = tmdb_data.country or ""
            variables["director"] = tmdb_data.director or ""
            variables["tmdb_id"] = tmdb_data.tmdb_id or ""
            variables["imdb_id"] = tmdb_data.imdb_id or ""
            variables["tmdb_url"] = tmdb_data.tmdb_url or f"https://www.themoviedb.org/movie/{tmdb_data.tmdb_id}" if tmdb_data.tmdb_id else ""
            variables["trailer_url"] = tmdb_data.trailer_url or ""

            # Cast names list
            cast_names = []
            for i, cast_member in enumerate(tmdb_data.cast[:6] if tmdb_data.cast else []):
                cast_names.append(cast_member.name)
            variables["cast_names"] = ", ".join(cast_names)

            # Cast variables (6 actors)
            for i in range(1, 7):
                if tmdb_data.cast and len(tmdb_data.cast) >= i:
                    cast_member = tmdb_data.cast[i - 1]
                    variables[f"cast_{i}_name"] = cast_member.name
                    variables[f"cast_{i}_character"] = cast_member.character
                    variables[f"cast_{i}_photo"] = cast_member.photo_url
                    # Card: inline format - just photo (name can be added separately or via cast_names)
                    # No line breaks to allow horizontal display when cards are placed together
                    variables[f"cast_{i}_card"] = f"[img]{cast_member.photo_url}[/img]"
                else:
                    variables[f"cast_{i}_name"] = ""
                    variables[f"cast_{i}_character"] = ""
                    variables[f"cast_{i}_photo"] = ""
                    variables[f"cast_{i}_card"] = ""
        else:
            variables["title"] = "Titre"
            variables["original_title"] = ""
            variables["year"] = ""
            variables["release_date"] = ""
            variables["poster_url"] = "https://via.placeholder.com/500x750?text=No+Image"
            variables["backdrop_url"] = ""
            variables["rating"] = "0"
            variables["rating_10"] = "0/10"
            variables["genres"] = ""
            variables["overview"] = ""
            variables["tagline"] = ""
            variables["runtime"] = ""
            variables["country"] = ""
            variables["director"] = ""
            variables["tmdb_id"] = ""
            variables["imdb_id"] = ""
            variables["tmdb_url"] = ""
            variables["trailer_url"] = ""
            variables["cast_names"] = ""
            # Empty cast variables (6 actors)
            for i in range(1, 7):
                variables[f"cast_{i}_name"] = ""
                variables[f"cast_{i}_character"] = ""
                variables[f"cast_{i}_photo"] = ""
                variables[f"cast_{i}_card"] = ""

        # MediaInfo variables
        variables["quality"] = self._get_quality_string(media_data)
        variables["format"] = media_data.format or "MKV"
        variables["video_codec"] = media_data.video_tracks[0].format if media_data.video_tracks else ""
        variables["video_bitrate"] = media_data.video_tracks[0].bitrate if media_data.video_tracks else ""

        # Resolution: prefer MediaInfo but fallback to filename detection
        if media_data.video_tracks and media_data.video_tracks[0].width and media_data.video_tracks[0].height:
            width = media_data.video_tracks[0].width
            height = media_data.video_tracks[0].height
            # Check if filename suggests higher resolution (4K mislabeled as 1080p)
            filename_height, _, _ = self._detect_resolution_from_filename(media_data.file_name)
            if filename_height and filename_height > height:
                # Use filename resolution, estimate width based on 16:9 aspect ratio
                height = filename_height
                width = int(height * 16 / 9)
                logger.debug(f"Resolution adjusted from MediaInfo to filename: {width}x{height}")
            variables["resolution"] = f"{width}x{height}"
        else:
            # Fallback to filename detection
            filename_height, _, _ = self._detect_resolution_from_filename(media_data.file_name)
            if filename_height:
                width = int(filename_height * 16 / 9)
                variables["resolution"] = f"{width}x{filename_height}"
            else:
                variables["resolution"] = ""

        variables["hdr"] = self._get_hdr_string(media_data)
        variables["duration"] = media_data.duration or ""
        variables["audio_list"] = "\n".join(self._get_audio_codec_list(media_data))
        variables["audio_table"] = self._build_audio_table(media_data)
        variables["languages"] = self._get_languages_string(media_data)
        variables["subtitles"] = self._get_subtitles_string(media_data)
        variables["subtitles_table"] = self._build_subtitles_table(media_data)
        variables["file_size"] = media_data.file_size or ""
        variables["file_count"] = "1"  # Default, will be updated by pipeline for multi-file releases
        variables["source"] = self._detect_source_from_filename(media_data.file_name)

        # Release info (extracted from filename)
        file_name = media_data.file_name or ""
        base_name = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
        variables["release_name"] = base_name
        # Extract team from release name (after last hyphen)
        if "-" in base_name:
            variables["release_team"] = base_name.rsplit("-", 1)[-1]
        else:
            variables["release_team"] = ""

        return variables

    def _detect_source_from_filename(self, filename: str) -> str:
        """Detect media source from filename."""
        filename_lower = filename.lower()

        if "remux" in filename_lower:
            return "REMUX"
        elif "bluray" in filename_lower or "blu-ray" in filename_lower:
            if "uhd" in filename_lower or "2160p" in filename_lower or "4k" in filename_lower:
                return "BluRay UHD"
            return "BluRay"
        elif "webdl" in filename_lower or "web-dl" in filename_lower:
            return "WEB-DL"
        elif "webrip" in filename_lower:
            return "WEBRip"
        elif "hdtv" in filename_lower:
            return "HDTV"
        elif "dvdrip" in filename_lower:
            return "DVDRip"
        elif "bdrip" in filename_lower:
            return "BDRip"
        elif "hdrip" in filename_lower:
            return "HDRip"
        elif "mhd" in filename_lower:
            return "mHD"
        elif "amzn" in filename_lower or "amazon" in filename_lower:
            return "AMZN WEB-DL"
        elif "nf" in filename_lower or "netflix" in filename_lower:
            return "NF WEB-DL"
        elif "dsnp" in filename_lower or "disney" in filename_lower:
            return "DSNP WEB-DL"
        elif "atvp" in filename_lower or "apple" in filename_lower:
            return "ATVP WEB-DL"
        elif "hmax" in filename_lower:
            return "HMAX WEB-DL"
        elif "web" in filename_lower:
            return "WEB"
        else:
            return ""

    def render_template(
        self,
        template_content: str,
        media_data: MediaInfoData,
        tmdb_data: Optional[TMDBData] = None,
        extra_variables: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Render a BBCode template by replacing placeholders with actual data.

        Args:
            template_content: BBCode template with {{placeholder}} syntax
            media_data: MediaInfoData object with technical information
            tmdb_data: Optional TMDBData object with movie/show metadata
            extra_variables: Optional dict of additional variables to override/add

        Returns:
            Rendered BBCode string with placeholders replaced
        """
        variables = self._build_template_variables(media_data, tmdb_data)

        # Override with extra variables
        if extra_variables:
            variables.update(extra_variables)

        result = template_content

        # Process conditional blocks {{#var}}...{{/var}}
        # If variable is non-empty, keep the content; otherwise remove the block
        def replace_conditional(match):
            var_name = match.group(1)
            content = match.group(2)
            value = variables.get(var_name, "")
            if value:
                return content
            return ""

        result = re.sub(
            r'\{\{#(\w+)\}\}([\s\S]*?)\{\{/\1\}\}',
            replace_conditional,
            result
        )

        # Replace all {{variable}} placeholders
        for var_name, var_value in variables.items():
            pattern = r'\{\{' + re.escape(var_name) + r'\}\}'
            result = re.sub(pattern, str(var_value), result)

        return result

    async def generate_from_template(
        self,
        template_content: str,
        file_path: str,
        tmdb_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Generate BBCode from a template and media file.

        Args:
            template_content: BBCode template with {{placeholder}} syntax
            file_path: Path to the media file
            tmdb_data: Optional dictionary with TMDB metadata

        Returns:
            Rendered BBCode string
        """
        # Extract MediaInfo
        media_data = await self.nfo_generator.extract_mediainfo(file_path)

        # Convert TMDB dict to dataclass if provided
        tmdb = None
        if tmdb_data:
            tmdb = TMDBData(
                title=tmdb_data.get("title", ""),
                original_title=tmdb_data.get("original_title", ""),
                year=tmdb_data.get("year", 0),
                poster_url=tmdb_data.get("poster_url", ""),
                vote_average=tmdb_data.get("vote_average", 0.0),
                genres=normalize_genres(tmdb_data.get("genres", [])),
                overview=tmdb_data.get("overview", ""),
                tmdb_id=str(tmdb_data.get("tmdb_id", "")),
                imdb_id=tmdb_data.get("imdb_id", ""),
                cast=self._convert_cast_from_dict(tmdb_data.get("cast", [])),
            )

        return self.render_template(template_content, media_data, tmdb)

    def preview_template(
        self,
        template_content: str
    ) -> str:
        """
        Preview a template with sample data.

        Args:
            template_content: BBCode template with {{placeholder}} syntax

        Returns:
            Rendered BBCode string with sample data
        """
        # Create sample cast data (Harry Potter and the Order of the Phoenix)
        sample_cast = [
            CastMember(
                name="Daniel Radcliffe",
                character="Harry Potter",
                profile_path="/iPg0J9UzAlPj1fLEJNllpW9IhGe.jpg"
            ),
            CastMember(
                name="Emma Watson",
                character="Hermione Granger",
                profile_path="/A14lLCZYDhfYdBa0fFRpwMDiwRN.jpg"
            ),
            CastMember(
                name="Rupert Grint",
                character="Ron Weasley",
                profile_path="/q2KZZ0ltTEl7Sf8volNFV1JDEP4.jpg"
            ),
            CastMember(
                name="Gary Oldman",
                character="Sirius Black",
                profile_path="/2v9FVVBUrrkW2m3QOcYkuhq9A6o.jpg"
            ),
            CastMember(
                name="Ralph Fiennes",
                character="Lord Voldemort",
                profile_path="/tJr9GcmGNHhLVVEH3i7QYbj6hBi.jpg"
            ),
            CastMember(
                name="Helena Bonham Carter",
                character="Bellatrix Lestrange",
                profile_path="/DDeITcCpnBd0CkAIRPhggy9bt5.jpg"
            ),
        ]

        # Create sample TMDB data (Harry Potter et l'Ordre du Phenix)
        sample_tmdb = TMDBData(
            title="Harry Potter et l'Ordre du Phenix",
            original_title="Harry Potter and the Order of the Phoenix",
            year=2007,
            release_date="mercredi 11 juillet 2007",
            poster_url="https://image.tmdb.org/t/p/w500/s836PRwHkLjrOJrfW0eo7B4NJOf.jpg",
            backdrop_url="https://image.tmdb.org/t/p/w1280/nnqWhdWjGL1zag8wgXCf3V9UGE9.jpg",
            vote_average=7.7,
            genres=["Aventure", "Fantastique", "Famille"],
            overview="Alors qu'il entame sa cinquieme annee d'etudes a Poudlard, Harry decouvre que la communaute des sorciers refuse de croire au retour de Voldemort, preferant remettre en cause sa sante mentale et sa credibilite. Isole, incompris, Harry doit faire face seul a ses tourments. Le Ministere de la Magie envoie Dolores Ombrage pour enseigner la Defense contre les forces du Mal et prendre peu a peu le controle de l'ecole. Harry et ses amis decident alors de former en secret l'Armee de Dumbledore.",
            tagline="La rebellion commence.",
            runtime=138,  # 2h18
            country="Royaume-Uni",
            director="David Yates",
            tmdb_id="675",
            imdb_id="tt0373889",
            tmdb_url="https://www.themoviedb.org/movie/675",
            trailer_url="https://www.youtube.com/watch?v=47PHhQgHvME",
            cast=sample_cast,
        )

        # Create sample MediaInfo data
        from .nfo_generator import MediaInfoData, VideoTrack, AudioTrack, SubtitleTrack

        sample_video = VideoTrack(
            format="HEVC",
            format_profile="Main 10",
            width=3840,
            height=2160,
            bitrate="18.5 Mb/s",
            frame_rate="23.976",
            bit_depth="10",
            hdr_format="Dolby Vision / HDR10",
        )

        sample_audio_fr = AudioTrack(
            format="TrueHD",
            channels=8,
            bitrate="4 800 kb/s",
            language="fra",
            title="VFF",
        )

        sample_audio_en = AudioTrack(
            format="TrueHD",
            channels=8,
            bitrate="4 800 kb/s",
            language="eng",
            title="VO",
        )

        sample_sub_fr = SubtitleTrack(
            format="SRT",
            language="fra",
            title="Francais",
        )

        sample_sub_en = SubtitleTrack(
            format="SRT",
            language="eng",
            title="English",
        )

        sample_media = MediaInfoData(
            file_name="Harry.Potter.and.the.Order.of.the.Phoenix.2007.2160p.UHD.BluRay.DV.HDR10.TrueHD.Atmos.7.1.HEVC-GROUP.mkv",
            file_size="52.4 GiB",
            duration="2h 18min",
            format="MKV",
            video_tracks=[sample_video],
            audio_tracks=[sample_audio_fr, sample_audio_en],
            subtitle_tracks=[sample_sub_fr, sample_sub_en],
        )

        return self.render_template(template_content, sample_media, sample_tmdb)


# Singleton instance
_bbcode_generator: Optional[BBCodeGenerator] = None


def get_bbcode_generator() -> BBCodeGenerator:
    """Get the singleton BBCodeGenerator instance."""
    global _bbcode_generator
    if _bbcode_generator is None:
        _bbcode_generator = BBCodeGenerator()
    return _bbcode_generator
