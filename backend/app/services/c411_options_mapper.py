"""
C411 Options Mapper Service for Seedarr v2.0

This service maps release metadata (resolution, language, source, etc.)
to C411 API options format.

C411 Options Format:
    options={"1": [2, 4], "2": 25, "7": 121, "6": 96}

Option Types:
    - Type 1: Language (multi-select)
        1=Anglais, 2=VFF (French), 4=Multi (FR inclus), 6=VFQ (Quebec), 8=VOSTFR
    - Type 2: Quality
        10=BluRay 4K, 12=BluRay Remux, 25=WEB-DL 1080, 26=WEB-DL 4K, 16=HDRip 1080
    - Type 7: Season (TV shows)
        118=Serie integrale, 121=S01, 122=S02, ..., 150=S30
    - Type 6: Episode (TV shows)
        96=Saison complete, 97=E01, 98=E02, ..., 116=E20

Usage:
    mapper = C411OptionsMapper()
    options = mapper.build_options(
        resolution="1080p",
        source="WEB-DL",
        languages=["French", "English"],
        season=1,
        episode=None  # Full season
    )
    # Returns: {"1": [4], "2": 25, "7": 121, "6": 96}
"""

import re
import logging
from typing import Dict, Any, Optional, List, Union

logger = logging.getLogger(__name__)


class C411OptionsMapper:
    """
    Maps release metadata to C411 options format.

    This class converts common release attributes (resolution, source,
    language, etc.) to the specific option IDs used by C411 API.
    """

    # Type 1: Language options (multi-select)
    LANGUAGE_OPTIONS = {
        "english": 1,
        "anglais": 1,
        "en": 1,
        "french": 2,       # VFF
        "vff": 2,
        "francais": 2,
        "fr": 2,
        "multi": 4,        # Multi (FR inclus)
        "multi-french": 4,
        "multilingual": 4,
        "quebec": 6,       # VFQ
        "vfq": 6,
        "quebecois": 6,
        "vostfr": 8,
        "subfrench": 8,
    }

    # Type 5: Genre options (multi-select)
    # Maps TMDB genre names (French) to C411 genre option IDs
    GENRE_OPTIONS = {
        # TMDB genre name (lowercase) -> C411 option ID
        "action": 39,
        "animation": 41,
        "aventure": 44,
        "comédie": 49,
        "comedie": 49,
        "crime": 81,  # Maps to Policier
        "documentaire": 56,
        "drame": 57,
        "familial": 61,
        "famille": 61,
        "fantastique": 62,
        "fantasy": 62,
        "guerre": 66,
        "histoire": 67,
        "historique": 67,
        "horreur": 59,
        "épouvante": 59,
        "musique": 73,
        "musical": 73,
        "mystère": 58,  # Maps to Enquête
        "romance": 84,
        "science-fiction": 86,
        "science fiction": 86,
        "thriller": 92,
        "téléfilm tv": 93,
        "western": 95,
        # English TMDB genre names mapping
        "adventure": 44,
        "comedy": 49,
        "documentary": 56,
        "drama": 57,
        "family": 61,
        "history": 67,
        "horror": 59,
        "music": 73,
        "mystery": 58,
        "war": 66,
        "tv movie": 93,
    }

    # TMDB genre ID to C411 genre option ID mapping
    TMDB_GENRE_TO_C411 = {
        28: 39,     # Action -> Action
        12: 44,     # Adventure -> Aventure
        16: 41,     # Animation -> Animation
        35: 49,     # Comedy -> Comédie
        80: 81,     # Crime -> Policier
        99: 56,     # Documentary -> Documentaire
        18: 57,     # Drama -> Drame
        10751: 61,  # Family -> Famille
        14: 62,     # Fantasy -> Fantastique
        36: 67,     # History -> Historique
        27: 59,     # Horror -> Épouvante & Horreur
        10402: 73,  # Music -> Musical
        9648: 58,   # Mystery -> Enquête
        10749: 84,  # Romance -> Romance
        878: 86,    # Science Fiction -> Science Fiction
        10770: 93,  # TV Movie -> Variétés TV
        53: 92,     # Thriller -> Thriller
        10752: 66,  # War -> Guerre
        37: 95,     # Western -> Western
    }

    # Type 2: Quality options
    # Format: (resolution_pattern, source_pattern) -> option_id
    QUALITY_OPTIONS = {
        # Light encodings (reduced bitrate) - check FIRST before full quality
        ("4klight", None): 415,
        ("2160p", "light"): 415,
        ("4k", "light"): 415,
        ("hdlight", "1080"): 413,
        ("1080p", "light"): 413,
        ("hdlight", "720"): 414,
        ("720p", "light"): 414,
        # BluRay 4K
        ("2160p", "bluray"): 10,
        ("2160p", "blu-ray"): 10,
        ("4k", "bluray"): 10,
        ("uhd", "bluray"): 10,
        # BluRay Remux
        ("remux", None): 12,
        # WEB-DL 4K
        ("2160p", "web"): 26,
        ("2160p", "web-dl"): 26,
        ("4k", "web"): 26,
        # WEB-DL 1080p
        ("1080p", "web"): 25,
        ("1080p", "web-dl"): 25,
        ("1080p", "webrip"): 25,
        # WEB-DL 720p
        ("720p", "web"): 24,
        ("720p", "web-dl"): 24,
        # HDRip 1080p
        ("1080p", "hdrip"): 16,
        ("1080p", "bdrip"): 16,
        # HDRip 720p
        ("720p", "hdrip"): 15,
        ("720p", "bdrip"): 15,
        # BluRay 1080p
        ("1080p", "bluray"): 11,
        ("1080p", "blu-ray"): 11,
        # BluRay 720p
        ("720p", "bluray"): 13,
        ("720p", "blu-ray"): 13,
        # HDTV
        ("1080p", "hdtv"): 17,
        ("1080i", "hdtv"): 17,
        ("720p", "hdtv"): 14,
        # DVD
        ("dvdrip", None): 18,
        ("dvd", None): 18,
        # CAM/TS (low quality)
        ("cam", None): 19,
        ("ts", None): 20,
        ("hdts", None): 20,
    }

    # Fallback quality mappings by resolution only
    QUALITY_BY_RESOLUTION = {
        "2160p": 26,  # Default to WEB-DL 4K
        "4k": 26,
        "1080p": 25,  # Default to WEB-DL 1080p
        "720p": 24,   # Default to WEB-DL 720p
        "480p": 18,   # Default to DVD quality
    }

    # Type 7: Season options (121 = S01, 122 = S02, etc.)
    # Season 0 or complete series = 118
    SEASON_COMPLETE = 118
    SEASON_BASE = 120  # S01 = 121, S02 = 122, etc.

    # Type 6: Episode options (97 = E01, 98 = E02, etc.)
    # Full season = 96
    EPISODE_COMPLETE = 96
    EPISODE_BASE = 96  # E01 = 97, E02 = 98, etc.

    def __init__(self):
        """Initialize C411OptionsMapper."""
        pass

    def map_language(self, languages: List[str]) -> List[int]:
        """
        Map language strings to C411 language option IDs.

        Args:
            languages: List of language strings (e.g., ["French", "English"])

        Returns:
            List of C411 language option IDs
        """
        option_ids = []

        for lang in languages:
            lang_lower = lang.lower().strip()

            # Direct mapping
            if lang_lower in self.LANGUAGE_OPTIONS:
                option_id = self.LANGUAGE_OPTIONS[lang_lower]
                if option_id not in option_ids:
                    option_ids.append(option_id)
                continue

            # Check for partial matches
            for key, option_id in self.LANGUAGE_OPTIONS.items():
                if key in lang_lower or lang_lower in key:
                    if option_id not in option_ids:
                        option_ids.append(option_id)
                    break

        # If we have both French and English, add Multi indicator but keep individual languages
        # C411 allows multi-select, so we can include all: French (2) + English (1) + Multi (4)
        if 1 in option_ids and 2 in option_ids:
            if 4 not in option_ids:
                option_ids.append(4)  # Add Multi (FR inclus) indicator

        # Default to Multi if nothing matched
        if not option_ids:
            option_ids = [4]  # Default to Multi

        return option_ids

    def map_quality(
        self,
        resolution: Optional[str] = None,
        source: Optional[str] = None,
        release_name: Optional[str] = None
    ) -> Optional[int]:
        """
        Map resolution and source to C411 quality option ID.

        Args:
            resolution: Resolution string (e.g., "1080p", "2160p")
            source: Source string (e.g., "WEB-DL", "BluRay")
            release_name: Full release name for fallback detection

        Returns:
            C411 quality option ID or None if not determined
        """
        resolution_lower = (resolution or "").lower()
        source_lower = (source or "").lower()
        release_lower = (release_name or "").lower()

        # Normalize resolution
        if "2160" in resolution_lower or "4k" in resolution_lower or "uhd" in resolution_lower:
            resolution_lower = "2160p"
        elif "1080" in resolution_lower:
            resolution_lower = "1080p"
        elif "720" in resolution_lower:
            resolution_lower = "720p"

        # Check for Light encodings FIRST (before other quality checks)
        if "4klight" in release_lower or ("4k" in release_lower and "light" in release_lower):
            return 415  # 4KLight [Encodage allégé 4K]
        if "hdlight" in release_lower:
            if "1080" in release_lower:
                return 413  # HDLight 1080
            elif "720" in release_lower:
                return 414  # HDLight 720
        if "light" in release_lower:
            if "2160" in release_lower or "4k" in release_lower:
                return 415  # 4KLight
            elif "1080" in release_lower:
                return 413  # HDLight 1080
            elif "720" in release_lower:
                return 414  # HDLight 720

        # Check for Remux (special case)
        if "remux" in source_lower or "remux" in release_lower:
            return 12  # BluRay Remux

        # Normalize source
        if "web" in source_lower:
            source_lower = "web"
        elif "blu" in source_lower:
            source_lower = "bluray"
        elif "hdtv" in source_lower:
            source_lower = "hdtv"
        elif "hdrip" in source_lower or "bdrip" in source_lower:
            source_lower = "hdrip"

        # Try exact match
        for (res_pattern, src_pattern), option_id in self.QUALITY_OPTIONS.items():
            if res_pattern in resolution_lower:
                if src_pattern is None or src_pattern in source_lower:
                    return option_id

        # Try to detect from release name
        if release_name:
            # Check for source in release name
            if "remux" in release_lower:
                return 12
            elif "web-dl" in release_lower or "webdl" in release_lower:
                if "2160" in release_lower or "4k" in release_lower:
                    return 26
                elif "1080" in release_lower:
                    return 25
                elif "720" in release_lower:
                    return 24
            elif "webrip" in release_lower:
                if "1080" in release_lower:
                    return 25
                elif "720" in release_lower:
                    return 24
            elif "bluray" in release_lower or "blu-ray" in release_lower:
                if "2160" in release_lower or "4k" in release_lower:
                    return 10
                elif "1080" in release_lower:
                    return 11
                elif "720" in release_lower:
                    return 13

        # Fallback to resolution-only mapping
        for res_key, option_id in self.QUALITY_BY_RESOLUTION.items():
            if res_key in resolution_lower:
                return option_id

        # Final fallback: WEB-DL 1080p
        return 25

    def map_genres(self, genres: List[dict]) -> List[int]:
        """
        Map TMDB genres to C411 genre option IDs.

        Args:
            genres: List of TMDB genre dicts with 'id' and 'name' keys
                   Example: [{"id": 28, "name": "Action"}, {"id": 12, "name": "Aventure"}]

        Returns:
            List of C411 genre option IDs
        """
        option_ids = []

        for genre in genres:
            c411_id = None

            # First try to map by TMDB genre ID (most reliable)
            tmdb_id = genre.get('id')
            if tmdb_id and tmdb_id in self.TMDB_GENRE_TO_C411:
                c411_id = self.TMDB_GENRE_TO_C411[tmdb_id]

            # Fallback: try to map by genre name
            if not c411_id:
                genre_name = genre.get('name', '').lower().strip()
                if genre_name in self.GENRE_OPTIONS:
                    c411_id = self.GENRE_OPTIONS[genre_name]

            if c411_id and c411_id not in option_ids:
                option_ids.append(c411_id)

        logger.debug(f"Mapped genres {genres} to C411 IDs: {option_ids}")
        return option_ids

    def map_season(self, season: Optional[int]) -> Optional[int]:
        """
        Map season number to C411 season option ID.

        Args:
            season: Season number (1-30) or None for complete series

        Returns:
            C411 season option ID
        """
        if season is None or season == 0:
            return self.SEASON_COMPLETE  # 118 = Serie integrale

        if 1 <= season <= 30:
            return self.SEASON_BASE + season  # 121 = S01, 122 = S02, etc.

        # Season > 30: return S30
        return 150

    def map_episode(self, episode: Optional[int]) -> int:
        """
        Map episode number to C411 episode option ID.

        Args:
            episode: Episode number (1-20) or None for complete season

        Returns:
            C411 episode option ID
        """
        if episode is None or episode == 0:
            return self.EPISODE_COMPLETE  # 96 = Saison complete

        if 1 <= episode <= 20:
            return self.EPISODE_BASE + episode  # 97 = E01, 98 = E02, etc.

        # Episode > 20: return E20
        return 116

    def detect_language_from_release_name(self, release_name: str) -> List[str]:
        """
        Detect languages from release name.

        Args:
            release_name: Full release name

        Returns:
            List of detected language strings
        """
        languages = []
        release_lower = release_name.lower()

        # Check for French language markers (check all, not mutually exclusive)
        if "vff" in release_lower or "vof" in release_lower or "vfi" in release_lower or "vf2" in release_lower:
            languages.append("french")
        elif "vfq" in release_lower or "quebec" in release_lower:
            languages.append("quebec")
        elif "vostfr" in release_lower:
            languages.append("vostfr")
        elif "french" in release_lower or ".fr." in release_lower:
            languages.append("french")

        # Check for English
        if "english" in release_lower or ".en." in release_lower or "vo" in release_lower:
            if "english" not in languages:
                languages.append("english")

        # MULTI usually means French + English included
        if "multi" in release_lower:
            # Add both French and English if not already detected
            if "french" not in languages and "quebec" not in languages:
                languages.append("french")
            if "english" not in languages:
                languages.append("english")

        # Default to Multi if nothing detected
        if not languages:
            languages = ["multi"]

        return languages

    def detect_season_episode(self, release_name: str) -> tuple:
        """
        Detect season and episode numbers from release name.

        Args:
            release_name: Full release name

        Returns:
            Tuple of (season, episode) or (None, None) for movies
        """
        # Pattern: S01E01, S01.E01, S01 E01
        se_pattern = re.search(r'[Ss](\d{1,2})[.\s]?[Ee](\d{1,2})', release_name)
        if se_pattern:
            return int(se_pattern.group(1)), int(se_pattern.group(2))

        # Pattern: S01 (full season)
        s_pattern = re.search(r'[Ss](\d{1,2})(?![Ee])', release_name)
        if s_pattern:
            return int(s_pattern.group(1)), None

        # Pattern: Season 1, Saison 1
        season_pattern = re.search(r'(?:Season|Saison)\s*(\d{1,2})', release_name, re.IGNORECASE)
        if season_pattern:
            return int(season_pattern.group(1)), None

        # Not a TV show
        return None, None

    def build_options(
        self,
        resolution: Optional[str] = None,
        source: Optional[str] = None,
        languages: Optional[List[str]] = None,
        genres: Optional[List[dict]] = None,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        release_name: Optional[str] = None,
        is_tv_show: bool = False
    ) -> Dict[str, Union[int, List[int]]]:
        """
        Build complete C411 options dict from release metadata.

        Args:
            resolution: Resolution string (e.g., "1080p")
            source: Source string (e.g., "WEB-DL")
            languages: List of language strings
            genres: List of TMDB genre dicts [{"id": 28, "name": "Action"}, ...]
            season: Season number for TV shows
            episode: Episode number for TV shows
            release_name: Full release name for fallback detection
            is_tv_show: Whether this is a TV show

        Returns:
            C411 options dict ready for API

        Example:
            >>> mapper.build_options(
            ...     resolution="1080p",
            ...     source="WEB-DL",
            ...     languages=["Multi"],
            ...     genres=[{"id": 28, "name": "Action"}],
            ...     season=1,
            ...     episode=None
            ... )
            {"1": [4], "2": 25, "5": [39], "7": 121, "6": 96}
        """
        options = {}

        # Detect from release name if not provided
        if release_name:
            if not languages:
                languages = self.detect_language_from_release_name(release_name)

            if is_tv_show and season is None:
                season, episode = self.detect_season_episode(release_name)

        # Type 1: Language (always include)
        if languages:
            lang_ids = self.map_language(languages)
            if lang_ids:
                options["1"] = lang_ids
        else:
            options["1"] = [4]  # Default to Multi

        # Type 2: Quality (always include)
        quality_id = self.map_quality(resolution, source, release_name)
        if quality_id:
            options["2"] = quality_id

        # Type 5: Genre (if provided)
        if genres:
            genre_ids = self.map_genres(genres)
            if genre_ids:
                options["5"] = genre_ids

        # Type 7 & 6: Season/Episode (TV shows only)
        if is_tv_show or season is not None:
            # Type 7: Season
            if season is not None:
                options["7"] = self.map_season(season)

            # Type 6: Episode
            options["6"] = self.map_episode(episode)

        logger.info(f"Built C411 options: {options}")
        return options

    def build_options_from_file_entry(
        self,
        file_entry: Any,
        release_name: Optional[str] = None,
        genres: Optional[List[dict]] = None
    ) -> Dict[str, Union[int, List[int]]]:
        """
        Build C411 options from a FileEntry object.

        Args:
            file_entry: FileEntry object with metadata
            release_name: Override release name
            genres: List of TMDB genre dicts [{"id": 28, "name": "Action"}, ...]

        Returns:
            C411 options dict
        """
        # Get release name
        name = release_name or getattr(file_entry, 'release_name', None) or getattr(file_entry, 'file_path', '')

        # Detect if TV show from tmdb_type
        is_tv_show = getattr(file_entry, 'tmdb_type', None) == 'tv'

        # Get resolution from mediainfo_data if available
        resolution = None
        source = None
        mediainfo = getattr(file_entry, 'mediainfo_data', None)
        if mediainfo and isinstance(mediainfo, dict):
            resolution = mediainfo.get('resolution') or mediainfo.get('video', {}).get('resolution')

        # Try to extract from release name
        if not resolution and name:
            res_match = re.search(r'(2160p|1080p|720p|480p|4K|UHD)', name, re.IGNORECASE)
            if res_match:
                resolution = res_match.group(1)

        # Detect source from release name
        if name:
            if 'WEB-DL' in name.upper() or 'WEBDL' in name.upper():
                source = 'WEB-DL'
            elif 'WEBRIP' in name.upper():
                source = 'WEBRip'
            elif 'BLURAY' in name.upper() or 'BLU-RAY' in name.upper():
                source = 'BluRay'
            elif 'REMUX' in name.upper():
                source = 'Remux'
            elif 'HDTV' in name.upper():
                source = 'HDTV'

        return self.build_options(
            resolution=resolution,
            source=source,
            languages=None,  # Will be detected from release name
            genres=genres,   # Passed from tmdb_data
            season=None,     # Will be detected from release name
            episode=None,    # Will be detected from release name
            release_name=name,
            is_tv_show=is_tv_show
        )


def get_c411_options_mapper() -> C411OptionsMapper:
    """
    Factory function to create C411OptionsMapper instance.

    Returns:
        C411OptionsMapper instance
    """
    return C411OptionsMapper()
