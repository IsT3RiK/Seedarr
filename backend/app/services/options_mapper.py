"""
Generic Options Mapper for Seedarr v2.0

This service maps release metadata (resolution, language, source, etc.)
to tracker-specific API options format using configuration data.

Instead of hardcoded mappings, this mapper reads all option definitions
from a tracker configuration (YAML/JSON), making it easy to add new
trackers without writing code.

Usage:
    config = load_tracker_config("c411.yaml")
    mapper = OptionsMapper(config.get("options", {}))
    options = mapper.build_options(
        resolution="1080p",
        source="WEB-DL",
        languages=["French", "English"],
        season=1,
        episode=None
    )
"""

import re
import logging
from typing import Dict, Any, Optional, List, Union

logger = logging.getLogger(__name__)


class OptionsMapper:
    """
    Generic options mapper that uses configuration for all mappings.

    The configuration should define option types with their mappings:

    options:
      language:
        type: "1"                           # API option type key
        multi_select: true                  # Whether multiple values can be selected
        default: [4]                        # Default value if no match
        auto_multi: true                    # Auto-add multi if both fr+en detected
        auto_multi_value: 4                 # Value to add for auto-multi
        mappings:
          english: 1
          french: 2
          multi: 4
          vostfr: 8
      quality:
        type: "2"
        multi_select: false
        default: 25
        mappings:
          "2160p_web": 26
          "1080p_web": 25
          "720p_web": 24
        resolution_fallback:               # Fallback by resolution only
          "2160p": 26
          "1080p": 25
      genre:
        type: "5"
        multi_select: true
        tmdb_mappings:                     # Map TMDB genre IDs to tracker IDs
          28: 39   # Action
          35: 49   # Comedy
        name_mappings:                     # Map genre names (fallback)
          action: 39
          comedy: 49
      season:
        type: "7"
        complete_value: 118               # Value for complete series
        base_value: 120                   # Base for S01=121, S02=122, etc.
        max_value: 150                    # Maximum season value (S30)
      episode:
        type: "6"
        complete_value: 96                # Value for complete season
        base_value: 96                    # Base for E01=97, E02=98, etc.
        max_value: 116                    # Maximum episode value (E20)
    """

    def __init__(self, options_config: Dict[str, Any]):
        """
        Initialize OptionsMapper with configuration.

        Args:
            options_config: Dictionary with option type definitions
        """
        self.config = options_config or {}

    def _get_option_config(self, option_name: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific option type."""
        return self.config.get(option_name)

    def _normalize_key(self, value: str) -> str:
        """Normalize a string key for matching."""
        return value.lower().strip().replace("-", "_").replace(" ", "_")

    def map_language(self, languages: List[str]) -> List[int]:
        """
        Map language strings to tracker language option IDs.

        Args:
            languages: List of language strings (e.g., ["French", "English"])

        Returns:
            List of tracker language option IDs
        """
        lang_config = self._get_option_config("language")
        if not lang_config:
            return []

        mappings = lang_config.get("mappings", {})
        option_ids = []

        for lang in languages:
            lang_key = self._normalize_key(lang)

            # Direct mapping
            if lang_key in mappings:
                option_id = mappings[lang_key]
                if option_id not in option_ids:
                    option_ids.append(option_id)
                continue

            # Partial match
            for key, option_id in mappings.items():
                if key in lang_key or lang_key in key:
                    if option_id not in option_ids:
                        option_ids.append(option_id)
                    break

        # Auto-add multi if both French and English detected
        if lang_config.get("auto_multi"):
            french_ids = [v for k, v in mappings.items() if "french" in k or "vff" in k or k == "fr"]
            english_ids = [v for k, v in mappings.items() if "english" in k or k == "en"]

            has_french = any(fid in option_ids for fid in french_ids)
            has_english = any(eid in option_ids for eid in english_ids)

            if has_french and has_english:
                multi_value = lang_config.get("auto_multi_value")
                if multi_value and multi_value not in option_ids:
                    option_ids.append(multi_value)

        # Default value if no match
        if not option_ids:
            default = lang_config.get("default", [])
            if isinstance(default, list):
                option_ids = default
            else:
                option_ids = [default]

        return option_ids

    def map_quality(
        self,
        resolution: Optional[str] = None,
        source: Optional[str] = None,
        release_name: Optional[str] = None
    ) -> Optional[int]:
        """
        Map resolution and source to tracker quality option ID.

        Args:
            resolution: Resolution string (e.g., "1080p", "2160p")
            source: Source string (e.g., "WEB-DL", "BluRay")
            release_name: Full release name for fallback detection

        Returns:
            Quality option ID or None
        """
        quality_config = self._get_option_config("quality")
        if not quality_config:
            return None

        mappings = quality_config.get("mappings", {})
        resolution_fallback = quality_config.get("resolution_fallback", {})

        # Normalize inputs
        resolution_lower = self._normalize_key(resolution or "")
        source_lower = self._normalize_key(source or "")
        release_lower = (release_name or "").lower()

        # Normalize resolution
        if "2160" in resolution_lower or "4k" in resolution_lower or "uhd" in resolution_lower:
            resolution_norm = "2160p"
        elif "1080" in resolution_lower:
            resolution_norm = "1080p"
        elif "720" in resolution_lower:
            resolution_norm = "720p"
        elif "480" in resolution_lower:
            resolution_norm = "480p"
        else:
            resolution_norm = resolution_lower

        # Check for Light encodings FIRST (highest priority)
        if "4klight" in release_lower or ("4k" in release_lower and "light" in release_lower):
            if "4klight" in mappings:
                return mappings["4klight"]
            if "2160p_light" in mappings:
                return mappings["2160p_light"]
        if "hdlight" in release_lower or "light" in release_lower:
            if "1080" in release_lower:
                if "hdlight_1080" in mappings:
                    return mappings["hdlight_1080"]
                if "1080p_light" in mappings:
                    return mappings["1080p_light"]
            elif "720" in release_lower:
                if "hdlight_720" in mappings:
                    return mappings["hdlight_720"]
                if "720p_light" in mappings:
                    return mappings["720p_light"]

        # Normalize source
        if "remux" in source_lower or "remux" in release_lower:
            source_norm = "remux"
        elif "web" in source_lower:
            source_norm = "web"
        elif "blu" in source_lower:
            source_norm = "bluray"
        elif "hdtv" in source_lower:
            source_norm = "hdtv"
        elif "hdrip" in source_lower or "bdrip" in source_lower:
            source_norm = "hdrip"
        else:
            source_norm = source_lower

        # Try combined key (resolution_source)
        combined_key = f"{resolution_norm}_{source_norm}"
        if combined_key in mappings:
            return mappings[combined_key]

        # Try with underscored source variants
        for map_key, option_id in mappings.items():
            if resolution_norm in map_key and source_norm in map_key:
                return option_id

        # Try source-only match (like "remux")
        if source_norm in mappings:
            return mappings[source_norm]

        # Detect from release name
        if release_name:
            for pattern, option_id in mappings.items():
                pattern_parts = pattern.replace("_", " ").split()
                if all(p in release_lower for p in pattern_parts):
                    return option_id

        # Resolution fallback
        if resolution_norm in resolution_fallback:
            return resolution_fallback[resolution_norm]

        # Default value
        return quality_config.get("default")

    def map_genres(self, genres: List[dict]) -> List[int]:
        """
        Map TMDB genres to tracker genre option IDs.

        Args:
            genres: List of TMDB genre dicts with 'id' and 'name' keys

        Returns:
            List of tracker genre option IDs
        """
        genre_config = self._get_option_config("genre")
        if not genre_config:
            return []

        tmdb_mappings = genre_config.get("tmdb_mappings", {})
        name_mappings = genre_config.get("name_mappings", {})
        option_ids = []

        for genre in genres:
            c_id = None

            # First try TMDB genre ID
            tmdb_id = genre.get('id')
            if tmdb_id:
                # Handle both string and int keys in config
                c_id = tmdb_mappings.get(tmdb_id) or tmdb_mappings.get(str(tmdb_id))

            # Fallback to genre name
            if not c_id:
                genre_name = self._normalize_key(genre.get('name', ''))
                c_id = name_mappings.get(genre_name)

                # Partial match on name
                if not c_id:
                    for key, val in name_mappings.items():
                        if key in genre_name or genre_name in key:
                            c_id = val
                            break

            if c_id and c_id not in option_ids:
                option_ids.append(c_id)

        return option_ids

    def map_season(self, season: Optional[int]) -> Optional[int]:
        """
        Map season number to tracker season option ID.

        Args:
            season: Season number (1-30) or None for complete series

        Returns:
            Season option ID
        """
        season_config = self._get_option_config("season")
        if not season_config:
            return None

        complete_value = season_config.get("complete_value")
        base_value = season_config.get("base_value", 0)
        max_value = season_config.get("max_value")

        if season is None or season == 0:
            return complete_value

        calculated = base_value + season

        if max_value and calculated > max_value:
            return max_value

        return calculated

    def map_episode(self, episode: Optional[int]) -> Optional[int]:
        """
        Map episode number to tracker episode option ID.

        Args:
            episode: Episode number or None for complete season

        Returns:
            Episode option ID
        """
        episode_config = self._get_option_config("episode")
        if not episode_config:
            return None

        complete_value = episode_config.get("complete_value")
        base_value = episode_config.get("base_value", 0)
        max_value = episode_config.get("max_value")

        if episode is None or episode == 0:
            return complete_value

        calculated = base_value + episode

        if max_value and calculated > max_value:
            return max_value

        return calculated

    def detect_language_from_release_name(self, release_name: str) -> List[str]:
        """
        Detect languages from release name.

        Args:
            release_name: Full release name

        Returns:
            List of detected language strings
        """
        lang_config = self._get_option_config("language")
        if not lang_config:
            return ["multi"]

        detection_patterns = lang_config.get("detection_patterns", {})
        languages = []
        release_lower = release_name.lower()

        # Default patterns if not configured
        if not detection_patterns:
            detection_patterns = {
                "vff": "french",
                "vfq": "quebec",
                "vostfr": "vostfr",
                "french": "french",
                ".fr.": "french",
                "english": "english",
                ".en.": "english",
                "multi": "multi"
            }

        for pattern, lang in detection_patterns.items():
            if pattern in release_lower:
                if lang not in languages:
                    languages.append(lang)

        # Handle MULTI expansion
        if "multi" in languages and lang_config.get("multi_expands_to"):
            expand_to = lang_config.get("multi_expands_to", [])
            for exp_lang in expand_to:
                if exp_lang not in languages:
                    languages.append(exp_lang)

        if not languages:
            return [lang_config.get("default_detection", "multi")]

        return languages

    def detect_season_episode(self, release_name: str) -> tuple:
        """
        Detect season and episode numbers from release name.

        Args:
            release_name: Full release name

        Returns:
            Tuple of (season, episode) or (None, None) for movies
        """
        # S01E01 pattern
        se_pattern = re.search(r'[Ss](\d{1,2})[.\s]?[Ee](\d{1,2})', release_name)
        if se_pattern:
            return int(se_pattern.group(1)), int(se_pattern.group(2))

        # S01 pattern (full season)
        s_pattern = re.search(r'[Ss](\d{1,2})(?![Ee])', release_name)
        if s_pattern:
            return int(s_pattern.group(1)), None

        # Season X / Saison X pattern
        season_pattern = re.search(r'(?:Season|Saison)\s*(\d{1,2})', release_name, re.IGNORECASE)
        if season_pattern:
            return int(season_pattern.group(1)), None

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
        Build complete options dict from release metadata.

        Args:
            resolution: Resolution string (e.g., "1080p")
            source: Source string (e.g., "WEB-DL")
            languages: List of language strings
            genres: List of TMDB genre dicts
            season: Season number for TV shows
            episode: Episode number for TV shows
            release_name: Full release name for fallback detection
            is_tv_show: Whether this is a TV show

        Returns:
            Options dict ready for API
        """
        options = {}

        # Detect from release name if not provided
        if release_name:
            if not languages:
                languages = self.detect_language_from_release_name(release_name)

            if is_tv_show and season is None:
                season, episode = self.detect_season_episode(release_name)

        # Map each option type
        for option_name, option_config in self.config.items():
            option_type = option_config.get("type")
            if not option_type:
                continue

            if option_name == "language":
                lang_ids = self.map_language(languages or [])
                if lang_ids:
                    options[str(option_type)] = lang_ids

            elif option_name == "quality":
                quality_id = self.map_quality(resolution, source, release_name)
                if quality_id:
                    options[str(option_type)] = quality_id

            elif option_name == "genre" and genres:
                genre_ids = self.map_genres(genres)
                if genre_ids:
                    options[str(option_type)] = genre_ids

            elif option_name == "season" and (is_tv_show or season is not None):
                if season is not None:
                    season_id = self.map_season(season)
                    if season_id:
                        options[str(option_type)] = season_id

            elif option_name == "episode" and (is_tv_show or season is not None):
                episode_id = self.map_episode(episode)
                if episode_id:
                    options[str(option_type)] = episode_id

        logger.info(f"Built options: {options}")
        return options

    def build_options_from_file_entry(
        self,
        file_entry: Any,
        release_name: Optional[str] = None,
        genres: Optional[List[dict]] = None
    ) -> Dict[str, Union[int, List[int]]]:
        """
        Build options from a FileEntry object.

        Args:
            file_entry: FileEntry object with metadata
            release_name: Override release name
            genres: List of TMDB genre dicts

        Returns:
            Options dict
        """
        name = release_name or getattr(file_entry, 'release_name', None) or getattr(file_entry, 'file_path', '')
        is_tv_show = getattr(file_entry, 'tmdb_type', None) == 'tv'

        resolution = None
        source = None
        mediainfo = getattr(file_entry, 'mediainfo_data', None)
        if mediainfo and isinstance(mediainfo, dict):
            resolution = mediainfo.get('resolution') or mediainfo.get('video', {}).get('resolution')

        # Extract from release name if not in mediainfo
        if not resolution and name:
            res_match = re.search(r'(2160p|1080p|720p|480p|4K|UHD)', name, re.IGNORECASE)
            if res_match:
                resolution = res_match.group(1)

        # Detect source from release name
        if name:
            name_upper = name.upper()
            if 'WEB-DL' in name_upper or 'WEBDL' in name_upper:
                source = 'WEB-DL'
            elif 'WEBRIP' in name_upper:
                source = 'WEBRip'
            elif 'BLURAY' in name_upper or 'BLU-RAY' in name_upper:
                source = 'BluRay'
            elif 'REMUX' in name_upper:
                source = 'Remux'
            elif 'HDTV' in name_upper:
                source = 'HDTV'

        return self.build_options(
            resolution=resolution,
            source=source,
            languages=None,
            genres=genres,
            season=None,
            episode=None,
            release_name=name,
            is_tv_show=is_tv_show
        )


def get_options_mapper(options_config: Dict[str, Any]) -> OptionsMapper:
    """
    Factory function to create OptionsMapper instance.

    Args:
        options_config: Options configuration dictionary

    Returns:
        OptionsMapper instance
    """
    return OptionsMapper(options_config)
