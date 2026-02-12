"""
UniversalRenamer Service for Seedarr v2.0

This module provides unified release naming that satisfies requirements from
multiple trackers. The naming convention follows scene standards while
ensuring compatibility with trackers like La Cale and C411.

Format:
    Titre.Annee.Langue.Resolution.Source.CodecAudio.CodecVideo-Team.ext

Rules:
    - Separators: Dots (.) only
    - No accents, apostrophes, or cedillas
    - Audio codec included (required by C411)
    - Year included when available
    - Team/group suffix with hyphen

Examples:
    - Gladiator.II.2024.FRENCH.1080p.WEB.EAC3.x264-TP.mkv
    - The.Matrix.1999.MULTi.2160p.BluRay.DTS-HD.MA.x265-GROUP.mkv
    - Breaking.Bad.S01E01.FRENCH.720p.WEB.AAC.x264-TEAM.mkv

Usage:
    renamer = UniversalRenamer()

    # Format release name from metadata
    release_name = renamer.format_release_name(
        title="Gladiator II",
        year=2024,
        language="FRENCH",
        resolution="1080p",
        source="WEB",
        audio_codec="EAC3",
        video_codec="x264",
        team="TP"
    )
    # Returns: "Gladiator.II.2024.FRENCH.1080p.WEB.EAC3.x264-TP"
"""

import re
import unicodedata
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class UniversalRenamer:
    """
    Service for creating unified release names compatible with multiple trackers.

    This renamer creates release names that follow scene conventions and
    satisfy requirements from various trackers (La Cale, C411, etc.).

    Naming Convention:
        {Title}.{Year}.{Language}.{Resolution}.{Source}.{AudioCodec}.{VideoCodec}-{Team}

    Components:
        - Title: Movie/show title with dots instead of spaces
        - Year: Release year (4 digits)
        - Language: Audio language (FRENCH, MULTi, VOSTFR, etc.)
        - Resolution: Video resolution (720p, 1080p, 2160p)
        - Source: Media source (BluRay, WEB, HDTV, DVDRip)
        - AudioCodec: Audio codec (AAC, EAC3, DTS-HD.MA, TrueHD)
        - VideoCodec: Video codec (x264, x265, AV1)
        - Team: Release group/team name

    Example:
        >>> renamer = UniversalRenamer()
        >>> name = renamer.format_release_name(
        ...     title="The Matrix",
        ...     year=1999,
        ...     language="MULTi",
        ...     resolution="2160p",
        ...     source="BluRay",
        ...     audio_codec="DTS-HD.MA",
        ...     video_codec="x265",
        ...     team="RELEASE"
        ... )
        >>> print(name)
        The.Matrix.1999.MULTi.2160p.BluRay.DTS-HD.MA.x265-RELEASE
    """

    # Default team name if none specified
    DEFAULT_TEAM = "TP"

    # Language mapping for normalization
    LANGUAGE_MAP = {
        "french": "FRENCH",
        "francais": "FRENCH",
        "fr": "FRENCH",
        "english": "ENGLISH",
        "en": "ENGLISH",
        "multi": "MULTi",
        "multilingual": "MULTi",
        "vf": "FRENCH",
        "vostfr": "VOSTFR",
        "vo": "VO",
        "vff": "VFF",
        "vof": "VOF",
        "vfq": "VFQ",
        "vfi": "VFI",
        "vf2": "VF2",
        "truefrench": "TRUEFRENCH",
    }

    # Resolution normalization
    RESOLUTION_MAP = {
        "4k": "2160p",
        "uhd": "2160p",
        "2160": "2160p",
        "1080": "1080p",
        "720": "720p",
        "576": "576p",
        "480": "480p",
    }

    # Source normalization
    # Note: WEB-DL and WEBDL are normalized to just "WEB" as per tracker requirements
    SOURCE_MAP = {
        "bluray": "BluRay",
        "blu-ray": "BluRay",
        "bdrip": "BDRip",
        "brrip": "BRRip",
        "web": "WEB",
        "web-dl": "WEB",
        "webdl": "WEB",
        "web.dl": "WEB",
        "webrip": "WEBRip",
        "hdtv": "HDTV",
        "dvdrip": "DVDRip",
        "dvd": "DVDRip",
        "hdcam": "HDCAM",
        "cam": "CAM",
        "ts": "TS",
        "telesync": "TS",
        "mhd": "mHD",
        "hdrip": "HDRip",
        "hd-rip": "HDRip",
        "vod": "VOD",
    }

    # Audio codec normalization
    AUDIO_CODEC_MAP = {
        "aac": "AAC",
        "ac3": "AC3",
        "ac-3": "AC3",
        "dd": "AC3",
        "dd5.1": "AC3",
        "eac3": "EAC3",
        "e-ac-3": "EAC3",
        "dd+": "EAC3",
        "ddp": "EAC3",
        "dts": "DTS",
        "dts-hd": "DTS-HD",
        "dts-hd.ma": "DTS-HD.MA",
        "dts-hd ma": "DTS-HD.MA",
        "truehd": "TrueHD",
        "atmos": "Atmos",
        "flac": "FLAC",
        "opus": "Opus",
        "mp3": "MP3",
    }

    # Video codec normalization
    VIDEO_CODEC_MAP = {
        "h264": "x264",
        "h.264": "x264",
        "avc": "x264",
        "h265": "x265",
        "h.265": "x265",
        "hevc": "x265",
        "av1": "AV1",
        "vp9": "VP9",
        "vc1": "VC1",
        "xvid": "XviD",
        "divx": "DivX",
    }

    def __init__(self, default_team: Optional[str] = None):
        """
        Initialize UniversalRenamer.

        Args:
            default_team: Default team name to use if none specified.
                         Defaults to "TP".
        """
        self.default_team = default_team or self.DEFAULT_TEAM

    def remove_accents(self, text: str) -> str:
        """
        Remove accents and diacritical marks from text.

        Converts accented characters to their ASCII equivalents:
        - e, e, e, e -> e
        - c -> c
        - a, a -> a
        - etc.

        Args:
            text: Text with potential accents

        Returns:
            Text with accents removed
        """
        # Normalize to NFD (decomposed form)
        normalized = unicodedata.normalize('NFD', text)

        # Remove combining diacritical marks
        without_accents = ''.join(
            char for char in normalized
            if unicodedata.category(char) != 'Mn'
        )

        return without_accents

    def sanitize_title(self, title: str) -> str:
        """
        Sanitize title for use in release name.

        Operations:
        1. Remove accents
        2. Replace spaces with dots
        3. Remove invalid characters (keep alphanumeric, dots, hyphens)
        4. Remove consecutive dots
        5. Strip leading/trailing dots

        Args:
            title: Raw title string

        Returns:
            Sanitized title suitable for release name
        """
        if not title:
            return ""

        # Remove accents
        title = self.remove_accents(title)

        # Replace common separators with dots
        title = re.sub(r'[\s_]+', '.', title)

        # Remove invalid characters (keep alphanumeric, dots, hyphens, apostrophes temporarily)
        title = re.sub(r'[^\w.\-\']', '', title)

        # Handle apostrophes (remove or convert)
        # "It's" -> "Its", "L'homme" -> "Lhomme"
        title = re.sub(r"'", '', title)

        # Remove consecutive dots
        title = re.sub(r'\.+', '.', title)

        # Strip leading/trailing dots
        title = title.strip('.')

        return title

    def normalize_language(self, language: Optional[str]) -> str:
        """
        Normalize language code to standard format.

        Args:
            language: Language code or name

        Returns:
            Normalized language string (e.g., "FRENCH", "MULTi")
        """
        if not language:
            return "FRENCH"  # Default for French trackers

        language_lower = language.lower().strip()
        return self.LANGUAGE_MAP.get(language_lower, language.upper())

    def normalize_resolution(self, resolution: Optional[str]) -> str:
        """
        Normalize resolution to standard format.

        Args:
            resolution: Resolution string

        Returns:
            Normalized resolution (e.g., "1080p", "2160p")
        """
        if not resolution:
            return ""

        resolution_lower = resolution.lower().strip()

        # Remove 'p' suffix if present for mapping
        resolution_clean = resolution_lower.rstrip('p')

        if resolution_clean in self.RESOLUTION_MAP:
            return self.RESOLUTION_MAP[resolution_clean]

        # Return with 'p' suffix if not in map
        if not resolution_lower.endswith('p'):
            return f"{resolution_lower}p"

        return resolution_lower.upper()

    def normalize_source(self, source: Optional[str]) -> str:
        """
        Normalize source to standard format.

        Args:
            source: Source string

        Returns:
            Normalized source (e.g., "BluRay", "WEB-DL")
        """
        if not source:
            return ""

        source_lower = source.lower().strip()
        return self.SOURCE_MAP.get(source_lower, source)

    def normalize_audio_codec(self, codec: Optional[str]) -> str:
        """
        Normalize audio codec to standard format.

        Args:
            codec: Audio codec string

        Returns:
            Normalized audio codec (e.g., "AAC", "DTS-HD.MA")
        """
        if not codec:
            return ""

        codec_lower = codec.lower().strip()
        return self.AUDIO_CODEC_MAP.get(codec_lower, codec.upper())

    def normalize_video_codec(self, codec: Optional[str]) -> str:
        """
        Normalize video codec to standard format.

        Args:
            codec: Video codec string

        Returns:
            Normalized video codec (e.g., "x264", "x265")
        """
        if not codec:
            return ""

        codec_lower = codec.lower().strip()
        return self.VIDEO_CODEC_MAP.get(codec_lower, codec)

    def format_release_name(
        self,
        title: str,
        year: Optional[int] = None,
        language: Optional[str] = None,
        resolution: Optional[str] = None,
        source: Optional[str] = None,
        audio_codec: Optional[str] = None,
        video_codec: Optional[str] = None,
        team: Optional[str] = None,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        hdr: Optional[str] = None,
        remux: bool = False,
        repack: bool = False,
        imax: bool = False,
        edition: Optional[str] = None,
        language_variant: Optional[str] = None,
        audio_channels: Optional[str] = None,
    ) -> str:
        """
        Format a complete release name from metadata components.

        C411 Naming Convention:
            Film:     Title.[IMAX].Year.[REPACK].Language[.Variant].Resolution.Source[.REMUX].[HDR].Audio.Video-Team
            Doc:      Title.Year.DOC.Language.Resolution.Source.Audio.Video-Team
            Series:   Title.SXXEXX.Language.Resolution.Source.[HDR].Audio.Video-Team
            Pack:     Title.COLLECTION/INTEGRALE.Language.Resolution.Source.Audio.Video-Team

        Args:
            title: Movie or show title
            year: Release year (optional)
            language: Audio language (defaults to "FRENCH")
            resolution: Video resolution (e.g., "1080p")
            source: Media source (e.g., "BluRay", "WEB")
            audio_codec: Audio codec (e.g., "AAC", "DTS-HD.MA")
            video_codec: Video codec (e.g., "x264", "x265")
            team: Release team/group name (defaults to "TP")
            season: Season number for TV shows (optional)
            episode: Episode number for TV shows (optional)
            hdr: HDR type (e.g., "Dolby Vision", "HDR10")
            remux: Whether this is a REMUX release
            repack: Whether this is a REPACK release
            imax: Whether this is an IMAX release
            edition: Edition type ("DOC", "INTEGRALE", "COLLECTION")
            language_variant: Language variant for MULTI releases ("VFF", "VOF", etc.)

        Returns:
            Formatted release name without extension
        """
        components = []

        # 1. Title (required)
        sanitized_title = self.sanitize_title(title)
        if not sanitized_title:
            raise ValueError("Title is required for release name")
        components.append(sanitized_title)

        is_series = season is not None
        is_pack = edition in ('INTEGRALE', 'COLLECTION')
        is_doc = edition == 'DOC'

        # 2. IMAX (before year, for films only)
        if imax and not is_series:
            components.append("IMAX")

        # 3. Edition for packs (INTEGRALE/COLLECTION after title)
        if is_pack:
            components.append(edition)

        # 4. Year (for films/docs, not for series unless it's meaningful)
        if not is_series and year:
            components.append(str(year))

        # 5. Season/Episode for TV shows
        if is_series:
            if episode is not None:
                components.append(f"S{season:02d}E{episode:02d}")
            else:
                components.append(f"S{season:02d}")

        # 6. REPACK (after year for films)
        if repack and not is_series:
            components.append("REPACK")

        # 7. DOC marker (after year)
        if is_doc:
            components.append("DOC")

        # 8. Language (defaults to FRENCH)
        lang = self.normalize_language(language)
        if lang:
            components.append(lang)
            # Language variant (e.g., MULTI.VFF)
            if language_variant:
                components.append(language_variant.upper())

        # 9. Resolution
        res = self.normalize_resolution(resolution)
        if res:
            components.append(res)

        # 10. Source
        src = self.normalize_source(source)
        if src:
            # For REMUX BluRay, use BLURAY.REMUX format
            if remux:
                if src.lower() in ('bluray', 'bdrip', 'brrip'):
                    components.append("BLURAY")
                else:
                    components.append(src)
                components.append("REMUX")
            else:
                components.append(src)
        elif remux:
            components.append("BLURAY")
            components.append("REMUX")

        # 11. HDR details (DV, HDR10, etc.)
        if hdr and hdr not in ('SDR',):
            hdr_components = self._format_hdr_components(hdr)
            components.extend(hdr_components)

        # 12. Audio codec (with channels if provided, e.g. AC3.5.1)
        audio = self.normalize_audio_codec(audio_codec)
        if audio:
            if audio_channels:
                components.append(f"{audio}.{audio_channels}")
            else:
                components.append(audio)

        # 13. Video codec
        video = self.normalize_video_codec(video_codec)
        if video:
            components.append(video)

        # Build name without team
        name = '.'.join(components)

        # 14. Add team with hyphen
        team_name = team or self.default_team
        name = f"{name}-{team_name}"

        logger.debug(f"Formatted release name: {name}")

        return name

    def _format_hdr_components(self, hdr: str) -> list:
        """
        Convert HDR string to C411 release name components.

        Args:
            hdr: HDR type string from metadata parser

        Returns:
            List of HDR components (e.g., ["DV", "HDR10"])
        """
        if not hdr:
            return []

        hdr_lower = hdr.lower()
        parts = []

        # Dolby Vision
        if 'dolby vision' in hdr_lower or 'dv' in hdr_lower or 'dovi' in hdr_lower:
            parts.append("DV")

        # HDR10+ or HDR10
        if 'hdr10+' in hdr_lower or 'hdr10plus' in hdr_lower:
            parts.append("HDR10+")
        elif 'hdr10' in hdr_lower:
            parts.append("HDR10")
        elif 'hdr' in hdr_lower and 'hdr10' not in hdr_lower:
            parts.append("HDR")

        # HLG
        if 'hlg' in hdr_lower:
            parts.append("HLG")

        return parts if parts else [hdr.upper()]

    def format_with_extension(
        self,
        title: str,
        extension: str,
        **kwargs
    ) -> str:
        """
        Format release name with file extension.

        Args:
            title: Movie or show title
            extension: File extension (with or without dot)
            **kwargs: Additional arguments passed to format_release_name

        Returns:
            Formatted release name with extension

        Example:
            >>> renamer.format_with_extension(
            ...     title="The Matrix",
            ...     extension="mkv",
            ...     year=1999,
            ...     resolution="1080p"
            ... )
            'The.Matrix.1999.FRENCH.1080p-TP.mkv'
        """
        name = self.format_release_name(title, **kwargs)

        # Ensure extension has dot prefix
        if not extension.startswith('.'):
            extension = f'.{extension}'

        return f"{name}{extension}"

    def extract_team_from_filename(self, filename: str) -> Optional[str]:
        """
        Extract team/group name from existing filename.

        Looks for patterns like:
        - -TEAM at the end (scene format: Movie.Name-TEAM.mkv)
        - - TEAM at the end (space format: Movie Name - TEAM.mkv)
        - .TEAM at the end (dot format: Movie.Name.TEAM.mkv - less common)

        Args:
            filename: Filename to extract team from

        Returns:
            Team name if found, None otherwise
        """
        # Remove extension
        name = re.sub(r'\.[^.]+$', '', filename)

        # Strip trailing parenthetical content (e.g., "(Beauty and the Beast)")
        # These are often alternate titles appended after the team tag
        name = re.sub(r'\s*\([^)]*\)\s*$', '', name)

        # Known codec patterns that end with "-something" and look like team tags
        # E-AC-3 -> "-3", DTS-HD -> "-HD", AC-3 -> "-3"
        codec_tail_patterns = [
            r'E[\-\.]?AC[\-\.]?3$',           # E-AC-3
            r'DTS[\-\.]?HD(?:[\-\.]?MA)?$',   # DTS-HD, DTS-HD.MA
            r'AC[\-\.]?3$',                    # AC-3
        ]

        # Pattern 1: Scene format "-TEAM" (no spaces)
        match = re.search(r'-([A-Za-z0-9]+)$', name)
        if match:
            # Check if the matched suffix is actually part of a codec
            # by looking at the full tail of the name including the match
            tail = name[max(0, match.start() - 10):]  # Get enough context
            is_false_positive = any(
                re.search(pattern, tail, re.IGNORECASE)
                for pattern in codec_tail_patterns
            )
            if not is_false_positive:
                return match.group(1)

        # Pattern 2: Space format " - TEAM" or "- TEAM" or " -TEAM"
        match = re.search(r'\s+-\s*([A-Za-z0-9]+)$', name)
        if match:
            return match.group(1)

        # Pattern 3: Common release groups at end (even without separator)
        # Known team patterns that might appear without clear separator
        common_teams = r'(QTZ|YGG|FGT|AMIABLE|SPARKS|GECKOS|TP|FraMeSToR|BHD|DON|EPSiLON|FLUX|TEPES|ROVERS)$'
        match = re.search(common_teams, name, re.I)
        if match:
            return match.group(1).upper()

        return None

    def has_scene_format(self, filename: str) -> bool:
        """
        Check if filename already follows scene naming conventions.

        Checks for patterns like:
        - Dots as separators (or spaces - common in non-scene releases)
        - Year present (4 digits)
        - Resolution present (720p, 1080p, 2160p, 4K)
        - Team suffix (-GROUP or - GROUP)

        Args:
            filename: Filename to check

        Returns:
            True if filename appears to follow scene/release format
        """
        # Remove extension
        name = re.sub(r'\.[^.]+$', '', filename)

        # Check for key indicators
        has_dots = '.' in name
        has_year = bool(re.search(r'[\s.\(](19|20)\d{2}[\s.\)]?', name))
        has_resolution = bool(re.search(r'\b(720|1080|2160|4K)p?\b', name, re.I))
        # Team with or without spaces around hyphen
        has_team = bool(re.search(r'\s*-\s*[A-Za-z0-9]+$', name))

        # Consider scene format if has most indicators
        indicators = [has_dots, has_year, has_resolution, has_team]
        return sum(indicators) >= 3

    def has_team_tag(self, filename: str) -> bool:
        """
        Check if filename has a team/release group tag.

        This is a simpler check than has_scene_format - just looks for
        any team tag at the end of the filename.

        Args:
            filename: Filename to check

        Returns:
            True if filename has a team tag
        """
        return self.extract_team_from_filename(filename) is not None

    def format_with_template(
        self,
        template: str,
        metadata: dict
    ) -> str:
        """
        Format release name using a custom template.

        This method allows trackers to define their own naming conventions
        through a template string with {variable} placeholders.

        Args:
            template: Template string with {variables}
                     Example: "{titre}.{annee}.{langue}.{resolution}.{source}.{codec_audio}.{codec_video}-{group}"
            metadata: Dict with all available variables:
                     - titre: Normalized title (dots instead of spaces)
                     - titre_fr: French title if available
                     - titre_en: English/original title
                     - annee: Release year
                     - langue: Language (FRENCH, MULTi, etc.)
                     - resolution: Video resolution (1080p, 2160p, etc.)
                     - source: Source (WEB, BluRay, etc.)
                     - codec_audio: Audio codec (AAC, DTS-HD.MA, etc.)
                     - codec_video: Video codec (x264, x265, etc.)
                     - group: Release group name
                     - hdr: HDR format if present (HDR10, DV, etc.)
                     - saison: Season number for series (S01)
                     - episode: Episode number for series (E05)

        Returns:
            Formatted release name with variables replaced.
            Missing variables are omitted (empty string).

        Example:
            >>> renamer.format_with_template(
            ...     "{titre}.{annee}.{langue}.{resolution}-{group}",
            ...     {"titre": "Cloud.9", "annee": "2014", "langue": "FRENCH",
            ...      "resolution": "1080p", "group": "FW"}
            ... )
            'Cloud.9.2014.FRENCH.1080p-FW'
        """
        import re

        # Normalize values in metadata
        normalized = {}
        for key, value in metadata.items():
            if value is None:
                normalized[key] = ''
            elif isinstance(value, str):
                # Sanitize string values (remove spaces, accents)
                if key in ('titre', 'titre_fr', 'titre_en'):
                    normalized[key] = self.sanitize_title(value)
                else:
                    normalized[key] = value
            else:
                normalized[key] = str(value)

        # Find all {variable} patterns in template
        pattern = r'\{([a-z_]+)\}'

        def replace_variable(match):
            var_name = match.group(1)
            return normalized.get(var_name, '')

        # Replace all variables
        result = re.sub(pattern, replace_variable, template)

        # Clean up result:
        # 1. Remove multiple consecutive dots (from empty variables)
        result = re.sub(r'\.{2,}', '.', result)
        # 2. Remove dots before hyphen (e.g., ".-GROUP" -> "-GROUP")
        result = re.sub(r'\.-', '-', result)
        # 3. Remove leading/trailing dots
        result = result.strip('.')

        logger.debug(f"Template formatted: {template} -> {result}")

        return result

    def build_template_metadata(
        self,
        title: str,
        year: Optional[int] = None,
        language: Optional[str] = None,
        resolution: Optional[str] = None,
        source: Optional[str] = None,
        audio_codec: Optional[str] = None,
        video_codec: Optional[str] = None,
        team: Optional[str] = None,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        hdr: Optional[str] = None,
        title_fr: Optional[str] = None,
        title_en: Optional[str] = None,
        audio_channels: Optional[str] = None,
        quality: Optional[str] = None,
    ) -> dict:
        """
        Build a metadata dictionary for use with format_with_template().

        This is a helper method that normalizes all values and creates
        the properly formatted metadata dict expected by format_with_template().

        Args:
            title: Movie or show title (used as titre)
            year: Release year
            language: Audio language (e.g., "FRENCH", "MULTi")
            resolution: Video resolution (e.g., "1080p")
            source: Media source (e.g., "WEB", "BluRay")
            audio_codec: Audio codec (e.g., "AAC", "DTS-HD.MA")
            video_codec: Video codec (e.g., "x264", "x265")
            team: Release group name
            season: Season number (for TV shows)
            episode: Episode number (for TV shows)
            hdr: HDR format (e.g., "HDR10", "Dolby Vision")
            title_fr: French title (optional)
            title_en: English/original title (optional)
            audio_channels: Audio channels (e.g., "2.0", "5.1", "7.1")
            quality: Quality indicator (e.g., "HDLight", "Remux")

        Returns:
            Dict with normalized values ready for format_with_template()
        """
        # Sanitize titles
        titre = self.sanitize_title(title) if title else ''
        titre_fr = self.sanitize_title(title_fr) if title_fr else ''
        titre_en = self.sanitize_title(title_en) if title_en else ''

        # Normalize language
        normalized_lang = self.normalize_language(language) if language else ''

        # Determine VFF/VFQ based on language
        # MULTi on French trackers typically includes French audio, so we set VFF
        vff = ''
        if normalized_lang in ('FRENCH', 'VFF', 'TRUEFRENCH', 'MULTi'):
            vff = 'VFF'
        elif normalized_lang == 'VFQ':
            vff = 'VFQ'
        elif normalized_lang == 'VFI':
            vff = 'VFI'

        # Format audio codec with channels if provided
        codec_audio = self.normalize_audio_codec(audio_codec) if audio_codec else ''
        codec_audio_full = codec_audio
        if codec_audio and audio_channels:
            codec_audio_full = f"{codec_audio}.{audio_channels}"

        metadata = {
            'titre': titre,
            'titre_fr': titre_fr,
            'titre_en': titre_en,
            'titre_lower': titre.lower() if titre else '',
            'titre_fr_lower': titre_fr.lower() if titre_fr else '',
            'titre_en_lower': titre_en.lower() if titre_en else '',
            'annee': str(year) if year else '',
            'langue': normalized_lang,
            'vff': vff,
            'resolution': self.normalize_resolution(resolution) if resolution else '',
            'source': self.normalize_source(source) if source else '',
            'quality': quality or '',
            'codec_audio': codec_audio,
            'codec_audio_full': codec_audio_full,
            'audio_channels': audio_channels or '',
            'codec_video': self.normalize_video_codec(video_codec) if video_codec else '',
            'group': team or self.default_team,
            'hdr': '',
            'saison': '',
            'episode': '',
        }

        # Format HDR components
        if hdr and hdr not in ('SDR',):
            hdr_components = self._format_hdr_components(hdr)
            metadata['hdr'] = '.'.join(hdr_components) if hdr_components else ''

        # Format season/episode
        if season is not None:
            metadata['saison'] = f"S{season:02d}"
            if episode is not None:
                metadata['episode'] = f"E{episode:02d}"

        return metadata

    def should_rename(self, filename: str, preserve_team: bool = True) -> bool:
        """
        Determine if a file should be renamed.

        Decision logic:
        1. If file has a team tag -> Don't rename (preserve original work)
        2. If no team tag -> Should rename to add our team tag

        The key insight is that if someone already tagged a release,
        we should respect their naming unless user explicitly forces rename.

        Args:
            filename: Current filename
            preserve_team: If True, don't rename files with existing team tag

        Returns:
            True if file should be renamed, False to preserve current name
        """
        if preserve_team:
            team = self.extract_team_from_filename(filename)
            if team:
                logger.info(f"Preserving existing release with team tag: {team}")
                return False

        return True


# Singleton instance
_renamer_instance: Optional[UniversalRenamer] = None


def get_universal_renamer() -> UniversalRenamer:
    """
    Get the singleton UniversalRenamer instance.

    Returns:
        UniversalRenamer instance
    """
    global _renamer_instance
    if _renamer_instance is None:
        _renamer_instance = UniversalRenamer()
    return _renamer_instance
