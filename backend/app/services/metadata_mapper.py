"""
Metadata Mapper Service for Seedarr v2.0

This service maps extracted file metadata (resolution, codec, source, audio, etc.)
to tracker tags and categories. It dynamically resolves tag IDs from the database
using label matching, eliminating hardcoded values.

Key Features:
    - Dynamic tag resolution from database (no hardcoded IDs)
    - Regex-based filename parsing for metadata extraction
    - MediaInfo integration for accurate technical details
    - Flexible mapping rules with fuzzy matching
    - Support for both Films and TV Shows categories

Usage Example:
    >>> from app.services.metadata_mapper import MetadataMapper
    >>> from app.database import SessionLocal
    >>>
    >>> db = SessionLocal()
    >>> mapper = MetadataMapper(db)
    >>>
    >>> # Map from filename
    >>> result = mapper.map_from_filename("Movie.2024.1080p.BluRay.x264-GROUP.mkv")
    >>> print(result['category_id'], result['tag_ids'])
    >>>
    >>> # Map from MediaInfo data
    >>> result = mapper.map_from_mediainfo(mediainfo_dict, is_tv_show=False)
"""

import re
import logging
from typing import Dict, List, Optional, Any, Tuple
from sqlalchemy.orm import Session

from app.models.tags import Tags
from app.models.categories import Categories

logger = logging.getLogger(__name__)


class MetadataMapper:
    """
    Service for mapping file metadata to tracker tags and categories.

    This service analyzes filenames and MediaInfo data to determine the appropriate
    tracker category and tags for upload. It uses dynamic tag resolution from the
    database to avoid hardcoded tag IDs.

    Architecture:
        1. Parse filename or MediaInfo to extract metadata (resolution, codec, etc.)
        2. Look up corresponding tag IDs from database by label
        3. Determine category (Films vs Séries TV) based on content type
        4. Return structured metadata ready for upload

    Attributes:
        db: SQLAlchemy database session for tag lookups
        _tag_cache: In-memory cache of tag label -> tag_id mappings
    """

    # ============================================================================
    # Regex patterns for filename parsing
    # ============================================================================

    # Resolution patterns
    RESOLUTION_PATTERNS = {
        '2160p': [r'2160p', r'4k', r'uhd'],
        '1080p': [r'1080p', r'1080i'],
        '720p': [r'720p'],
        '576p': [r'576p', r'576i'],
        '480p': [r'480p', r'480i'],
    }

    # Source patterns (order matters - check more specific first)
    SOURCE_PATTERNS = {
        'BluRay': [r'blu[\-\.]?ray', r'bdrip', r'brrip'],
        'WEB-DL': [r'web[\-\.]?dl', r'webdl'],
        'WEBRip': [r'webrip', r'web[\-\.]?rip'],
        'HDTV': [r'hdtv'],
        'DVDRip': [r'dvdrip', r'dvd[\-\.]?rip'],
        'HDRip': [r'hdrip', r'hd[\-\.]?rip'],
        'CAM': [r'cam', r'camrip', r'hdcam'],
        'TS': [r'telesync', r'hdts', r'\bts\b'],
        'VOD': [r'\bvod\b'],
        'mHD': [r'\bmhd\b'],
    }

    # REMUX detection (separated from source)
    REMUX_PATTERNS = [r'remux', r'bdremux']

    # REPACK detection
    REPACK_PATTERNS = [r'repack', r'rerip']

    # IMAX detection
    IMAX_PATTERNS = [r'imax']

    # Edition/type detection
    EDITION_PATTERNS = {
        'DOC': [r'\bdoc\b', r'\bdocu\b', r'documentary', r'documentaire'],
        'INTEGRALE': [r'integrale', r'complete[\.\-\s]?series'],
        'COLLECTION': [r'collection', r'saga'],
    }

    # Video codec patterns
    CODEC_PATTERNS = {
        'x265': [r'x265', r'hevc', r'h\.?265'],
        'x264': [r'x264', r'avc', r'h\.?264'],
        'AV1': [r'\bav1\b'],
        'VP9': [r'vp9'],
        'MPEG-2': [r'mpeg[\-\.]?2'],
    }

    # Audio codec patterns
    AUDIO_PATTERNS = {
        'Atmos': [r'atmos'],
        'TrueHD': [r'truehd', r'true[\-\.]?hd'],
        'DTS-HD MA': [r'dts[\-\.]?hd[\-\.]?ma', r'dts[\-\.]?hdma'],
        'DTS-HD': [r'dts[\-\.]?hd'],
        'DTS': [r'\bdts\b'],
        'DD+': [r'dd[\+p]', r'ddp', r'e[\-\.]?ac[\-\.]?3', r'eac3'],
        'DD5.1': [r'dd5[\.\s]?1', r'ac[\-\.]?3[\-\.]?5[\.\s]?1', r'ac3'],
        'AAC': [r'\baac\b'],
        'FLAC': [r'\bflac\b'],
        'MP3': [r'\bmp3\b'],
    }

    # HDR patterns
    HDR_PATTERNS = {
        'Dolby Vision': [r'dolby[\-\.\s]?vision', r'\bdv\b', r'dovi'],
        'HDR10+': [r'hdr10[\+p]', r'hdr10plus'],
        'HDR10': [r'hdr10', r'hdr[\-\.]?10'],
        'HDR': [r'\bhdr\b'],
        'SDR': [r'\bsdr\b'],
    }

    # Language patterns (French-focused for La Cale)
    LANGUAGE_PATTERNS = {
        'VFF': [r'\bvff\b'],
        'VOF': [r'\bvof\b'],
        'VFQ': [r'\bvfq\b'],
        'VFI': [r'\bvfi\b'],
        'VF2': [r'\bvf2\b'],
        'TRUEFRENCH': [r'truefrench'],
        'FRENCH': [r'\bfrench\b', r'\bvf\b'],
        'MULTI': [r'\bmulti\b', r'\bmulti\-?lang'],
        'VOSTFR': [r'vostfr', r'subfrench'],
        'VO': [r'\bvo\b', r'\beng\b', r'\benglish\b'],
    }

    # TV Show detection patterns
    TV_PATTERNS = [
        r's\d{1,2}e\d{1,2}',  # S01E01
        r's\d{1,2}[\.\-\s]?e\d{1,2}',  # S01.E01, S01-E01
        r'\d{1,2}x\d{1,2}',  # 1x01
        r'season[\.\-\s]?\d+',  # Season 1
        r'saison[\.\-\s]?\d+',  # Saison 1 (French)
        r'episode[\.\-\s]?\d+',  # Episode 1
        r'complete[\.\-\s]?series',
        r'integrale',
    ]

    def __init__(self, db: Session):
        """
        Initialize MetadataMapper.

        Args:
            db: SQLAlchemy database session for tag lookups
        """
        self.db = db
        self._tag_cache: Dict[str, str] = {}
        self._load_tag_cache()
        logger.info(f"MetadataMapper initialized with {len(self._tag_cache)} cached tags")

    def _load_tag_cache(self) -> None:
        """Load all tags into memory cache for fast lookups."""
        try:
            all_tags = Tags.get_all(self.db)
            self._tag_cache = {}
            for tag in all_tags:
                # Store multiple variations for flexible matching
                label_lower = tag.label.lower()
                self._tag_cache[label_lower] = tag.tag_id
                # Also store without special chars
                label_clean = re.sub(r'[^a-z0-9]', '', label_lower)
                self._tag_cache[label_clean] = tag.tag_id
            logger.debug(f"Loaded {len(all_tags)} tags into cache")
        except Exception as e:
            logger.error(f"Failed to load tag cache: {e}")
            self._tag_cache = {}

    def refresh_cache(self) -> None:
        """Refresh the tag cache from database."""
        self._load_tag_cache()
        logger.info("Tag cache refreshed")

    def _get_tag_id(self, label: str) -> Optional[str]:
        """
        Get tag ID by label with fuzzy matching.

        Args:
            label: Tag label to look up

        Returns:
            Tag ID if found, None otherwise
        """
        # Try exact match first
        label_lower = label.lower()
        if label_lower in self._tag_cache:
            return self._tag_cache[label_lower]

        # Try cleaned version
        label_clean = re.sub(r'[^a-z0-9]', '', label_lower)
        if label_clean in self._tag_cache:
            return self._tag_cache[label_clean]

        # Try database lookup with case-insensitive match
        tag = Tags.get_by_label(self.db, label)
        if tag:
            # Update cache
            self._tag_cache[label_lower] = tag.tag_id
            return tag.tag_id

        logger.debug(f"No tag found for label: {label}")
        return None

    def _match_pattern(self, text: str, patterns: Dict[str, List[str]]) -> Optional[str]:
        """
        Match text against pattern dictionary.

        Args:
            text: Text to search in (usually filename)
            patterns: Dict of label -> list of regex patterns

        Returns:
            Matched label if found, None otherwise
        """
        text_lower = text.lower()
        for label, pattern_list in patterns.items():
            for pattern in pattern_list:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    return label
        return None

    def is_tv_show(self, filename: str) -> bool:
        """
        Determine if filename represents a TV show.

        Args:
            filename: Filename to analyze

        Returns:
            True if TV show patterns detected, False otherwise
        """
        filename_lower = filename.lower()
        for pattern in self.TV_PATTERNS:
            if re.search(pattern, filename_lower, re.IGNORECASE):
                logger.debug(f"TV show detected in: {filename}")
                return True
        return False

    def parse_filename(self, filename: str) -> Dict[str, Optional[str]]:
        """
        Parse filename to extract metadata.

        Args:
            filename: Filename to parse

        Returns:
            Dictionary with extracted metadata:
                - title: Clean title extracted from filename
                - year: Release year if found
                - resolution: "1080p", "2160p", etc.
                - source: "BluRay", "WEB-DL", etc.
                - codec: "x264", "x265", etc.
                - audio: "DTS", "DD5.1", "Atmos", etc.
                - hdr: "HDR10", "Dolby Vision", etc.
                - language: "FRENCH", "MULTI", etc.
                - is_tv_show: bool
        """
        # Detect REMUX, REPACK, IMAX flags
        filename_lower = filename.lower()
        remux = any(re.search(p, filename_lower) for p in self.REMUX_PATTERNS)
        repack = any(re.search(p, filename_lower) for p in self.REPACK_PATTERNS)
        imax = any(re.search(p, filename_lower) for p in self.IMAX_PATTERNS)

        # Detect edition (DOC, INTEGRALE, COLLECTION)
        edition = self._match_pattern(filename, self.EDITION_PATTERNS)

        # Detect language and language variant (MULTI.VFF compound)
        language = self._match_pattern(filename, self.LANGUAGE_PATTERNS)
        language_variant = None
        if language == 'MULTI':
            # Check for compound MULTI + VFF/VOF/VFQ/VFI/VF2
            variant_patterns = {
                'VFF': [r'\bvff\b'],
                'VOF': [r'\bvof\b'],
                'VFQ': [r'\bvfq\b'],
                'VFI': [r'\bvfi\b'],
                'VF2': [r'\bvf2\b'],
            }
            language_variant = self._match_pattern(filename, variant_patterns)

        # Extract title and year from filename
        title, year = self._extract_title_and_year(filename)

        result = {
            'title': title,
            'year': year,
            'resolution': self._match_pattern(filename, self.RESOLUTION_PATTERNS),
            'source': self._match_pattern(filename, self.SOURCE_PATTERNS),
            'codec': self._match_pattern(filename, self.CODEC_PATTERNS),
            'audio': self._match_pattern(filename, self.AUDIO_PATTERNS),
            'hdr': self._match_pattern(filename, self.HDR_PATTERNS),
            'language': language,
            'language_variant': language_variant,
            'is_tv_show': self.is_tv_show(filename),
            'remux': remux,
            'repack': repack,
            'imax': imax,
            'edition': edition,
        }

        logger.debug(f"Parsed filename '{filename}': {result}")
        return result

    def _extract_title_and_year(self, filename: str) -> Tuple[Optional[str], Optional[int]]:
        """
        Extract clean title and year from filename.

        The title is everything before the year or technical indicators.
        Handles formats like:
        - "The.Hangover.Part.III.2013.VFF.1080p.BluRay.AC3.x265-HD2.mkv"
        - "Movie Title 2024 1080p WEB-DL"
        - "Show.Name.S01E05.720p.HDTV"

        Args:
            filename: Filename to parse

        Returns:
            Tuple of (title, year) where either can be None
        """
        # Remove extension
        name = re.sub(r'\.[a-zA-Z0-9]{2,4}$', '', filename)

        # Replace dots and underscores with spaces for easier parsing
        name_spaced = re.sub(r'[._]', ' ', name)

        # Try to find year (4 digits between 1900-2099)
        year_match = re.search(r'\b(19\d{2}|20\d{2})\b', name_spaced)
        year = None
        title = None

        if year_match:
            year = int(year_match.group(1))
            # Title is everything before the year
            title = name_spaced[:year_match.start()].strip()
        else:
            # No year found, try to find first technical indicator
            # Common indicators: resolution, language, source, codec
            indicators = [
                r'\b(2160p|1080p|720p|480p|576p)\b',
                r'\b(FRENCH|MULTI|MULTi|TRUEFRENCH|VFF|VFQ|VOSTFR|ENGLISH)\b',
                r'\b(BluRay|WEB|HDTV|DVDRip|BDRip|WEBRip|HDRip)\b',
                r'\b(x264|x265|H264|H265|HEVC|AVC|XviD)\b',
                r'\b(REMUX|REPACK|PROPER|IMAX)\b',
                r'\bS\d{1,2}(?:E\d{1,2})?\b',  # Season/Episode
            ]

            earliest_pos = len(name_spaced)
            for pattern in indicators:
                match = re.search(pattern, name_spaced, re.IGNORECASE)
                if match and match.start() < earliest_pos:
                    earliest_pos = match.start()

            if earliest_pos < len(name_spaced):
                title = name_spaced[:earliest_pos].strip()
            else:
                # Fallback: remove group tag (-GROUP at the end) and use as title
                title = re.sub(r'\s*-\s*[A-Za-z0-9]+$', '', name_spaced).strip()

        # Clean up title
        if title:
            # Remove trailing dashes, dots, spaces
            title = re.sub(r'[\s.\-]+$', '', title)
            # Remove double spaces
            title = re.sub(r'\s+', ' ', title)

        return title if title else None, year

    def detect_source_from_mediainfo(self, mediainfo_dict: Dict[str, Any]) -> Optional[str]:
        """
        Detect source (BluRay, WEB-DL, DVDRip) from MediaInfo technical data.

        Used as fallback when filename parsing doesn't find a source.
        Priority: subtitle format (most reliable) > lossless audio > high bitrate.

        Args:
            mediainfo_dict: MediaInfo extraction dictionary with video_tracks,
                           audio_tracks, and subtitle_tracks.

        Returns:
            Detected source string or None if no clear indicator found.
        """
        subtitle_tracks = mediainfo_dict.get('subtitle_tracks', [])
        audio_tracks = mediainfo_dict.get('audio_tracks', [])
        video_tracks = mediainfo_dict.get('video_tracks', [])

        # Check subtitle formats (most reliable indicator)
        has_pgs = False
        has_vobsub = False
        for sub in subtitle_tracks:
            sub_format = (sub.get('format') or '').upper()
            if 'PGS' in sub_format or 'HDMV' in sub_format:
                has_pgs = True
            if 'VOBSUB' in sub_format or 'DVD' in sub_format:
                has_vobsub = True

        if has_pgs:
            logger.info("Source detected from MediaInfo: BluRay (PGS subtitles found)")
            return 'BluRay'
        if has_vobsub:
            logger.info("Source detected from MediaInfo: DVDRip (VobSub subtitles found)")
            return 'DVDRip'

        # Check audio codecs (lossless = BluRay)
        for audio in audio_tracks:
            audio_codec = (audio.get('codec') or '').lower()
            if 'truehd' in audio_codec or 'dts-hd ma' in audio_codec or 'dts-hd' in audio_codec:
                # Check for MA specifically
                if 'truehd' in audio_codec or 'ma' in audio_codec:
                    logger.info(f"Source detected from MediaInfo: BluRay (lossless audio: {audio_codec})")
                    return 'BluRay'

        # Check for EAC3/DD+ without PGS → WEB-DL
        for audio in audio_tracks:
            audio_codec = (audio.get('codec') or '').lower()
            if 'e-ac-3' in audio_codec or 'eac3' in audio_codec or 'dd+' in audio_codec:
                logger.info(f"Source detected from MediaInfo: WEB-DL (EAC3/DD+ audio: {audio_codec})")
                return 'WEB-DL'

        # Check video bitrate (high bitrate 1080p = likely BluRay)
        if video_tracks:
            video = video_tracks[0]
            bitrate = video.get('bitrate')
            height = video.get('height')
            if bitrate and height:
                try:
                    bitrate_val = int(str(bitrate).replace(' ', ''))
                    # >20 Mbps for 1080p suggests BluRay/Remux
                    if height >= 1080 and bitrate_val > 20_000_000:
                        logger.info(f"Source detected from MediaInfo: BluRay (high bitrate: {bitrate_val/1_000_000:.1f} Mbps)")
                        return 'BluRay'
                except (ValueError, TypeError):
                    pass

        logger.debug("No source could be detected from MediaInfo")
        return None

    def map_from_filename(
        self,
        filename: str,
        force_category: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Map filename to tracker category and tags.

        Args:
            filename: Filename to analyze
            force_category: Optional category ID to force (override detection)

        Returns:
            Dictionary with:
                - category_id: Tracker category ID
                - tag_ids: List of tracker tag IDs
                - parsed_metadata: Raw parsed metadata
                - warnings: List of warnings (e.g., missing tags)
        """
        # Parse filename
        parsed = self.parse_filename(filename)
        tag_ids = []
        warnings = []

        # Determine category
        if force_category:
            category_id = force_category
        else:
            # Resolve category from database based on content type
            content_type = 'tv' if parsed['is_tv_show'] else 'movie'
            category_id = self.get_category_for_type(content_type)

        # Map each metadata field to tag
        metadata_to_tag_map = [
            ('resolution', parsed.get('resolution')),
            ('source', parsed.get('source')),
            ('codec', parsed.get('codec')),
            ('audio', parsed.get('audio')),
            ('hdr', parsed.get('hdr')),
            ('language', parsed.get('language')),
        ]

        for field_name, label in metadata_to_tag_map:
            if label:
                tag_id = self._get_tag_id(label)
                if tag_id:
                    tag_ids.append(tag_id)
                    logger.debug(f"Mapped {field_name}='{label}' -> tag_id={tag_id}")
                else:
                    warnings.append(f"No tag found for {field_name}='{label}'")
                    logger.warning(f"No tag found for {field_name}='{label}'")

        result = {
            'category_id': category_id,
            'tag_ids': tag_ids,
            'parsed_metadata': parsed,
            'warnings': warnings,
        }

        logger.info(
            f"Mapped filename '{filename}' -> category={category_id}, "
            f"tags={len(tag_ids)}, warnings={len(warnings)}"
        )

        return result

    def map_from_mediainfo(
        self,
        mediainfo: Dict[str, Any],
        is_tv_show: bool = False,
        filename: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Map MediaInfo data to tracker tags.

        Combines MediaInfo analysis with filename parsing for comprehensive metadata.

        Args:
            mediainfo: MediaInfo extraction dictionary
            is_tv_show: Whether content is a TV show
            filename: Optional filename for additional parsing

        Returns:
            Dictionary with category_id, tag_ids, and metadata
        """
        tag_ids = []
        warnings = []
        parsed = {}

        # Parse filename if provided
        if filename:
            parsed = self.parse_filename(filename)
            is_tv_show = is_tv_show or parsed.get('is_tv_show', False)

        # Extract from MediaInfo
        video_info = mediainfo.get('video', {})
        audio_info = mediainfo.get('audio', {})
        general_info = mediainfo.get('general', {})

        # Resolution from MediaInfo
        height = video_info.get('height')
        if height:
            if height >= 2160:
                resolution = '2160p'
            elif height >= 1080:
                resolution = '1080p'
            elif height >= 720:
                resolution = '720p'
            elif height >= 576:
                resolution = '576p'
            else:
                resolution = '480p'
            parsed['resolution'] = resolution

        # Codec from MediaInfo
        codec_id = video_info.get('codec_id', '').lower()
        format_name = video_info.get('format', '').lower()
        if 'hevc' in codec_id or 'h265' in codec_id or 'hevc' in format_name:
            parsed['codec'] = 'x265'
        elif 'avc' in codec_id or 'h264' in codec_id or 'avc' in format_name:
            parsed['codec'] = 'x264'
        elif 'av1' in codec_id or 'av1' in format_name:
            parsed['codec'] = 'AV1'

        # HDR from MediaInfo
        hdr_format = video_info.get('hdr_format', '')
        if 'dolby vision' in hdr_format.lower():
            parsed['hdr'] = 'Dolby Vision'
        elif 'hdr10+' in hdr_format.lower():
            parsed['hdr'] = 'HDR10+'
        elif 'hdr10' in hdr_format.lower() or video_info.get('transfer_characteristics') == 'PQ':
            parsed['hdr'] = 'HDR10'

        # Audio from MediaInfo
        audio_format = audio_info.get('format', '').lower()
        audio_codec = audio_info.get('codec_id', '').lower()
        commercial_name = audio_info.get('commercial_name', '').lower()

        if 'atmos' in commercial_name or 'atmos' in audio_format:
            parsed['audio'] = 'Atmos'
        elif 'truehd' in audio_format:
            parsed['audio'] = 'TrueHD'
        elif 'dts' in audio_format:
            if 'ma' in audio_format or 'hd ma' in commercial_name:
                parsed['audio'] = 'DTS-HD MA'
            elif 'hd' in commercial_name:
                parsed['audio'] = 'DTS-HD'
            else:
                parsed['audio'] = 'DTS'
        elif 'e-ac-3' in audio_format or 'eac3' in audio_codec:
            parsed['audio'] = 'DD+'
        elif 'ac-3' in audio_format or 'ac3' in audio_codec:
            parsed['audio'] = 'DD5.1'
        elif 'aac' in audio_format:
            parsed['audio'] = 'AAC'
        elif 'flac' in audio_format:
            parsed['audio'] = 'FLAC'

        # Determine category from database
        content_type = 'tv' if is_tv_show else 'movie'
        category_id = self.get_category_for_type(content_type)

        # Map parsed metadata to tags
        for field in ['resolution', 'source', 'codec', 'audio', 'hdr', 'language']:
            label = parsed.get(field)
            if label:
                tag_id = self._get_tag_id(label)
                if tag_id:
                    if tag_id not in tag_ids:
                        tag_ids.append(tag_id)
                else:
                    warnings.append(f"No tag found for {field}='{label}'")

        return {
            'category_id': category_id,
            'tag_ids': tag_ids,
            'parsed_metadata': parsed,
            'warnings': warnings,
        }

    def get_category_for_type(self, content_type: str) -> Optional[str]:
        """
        Get category ID for content type from database.

        First tries database lookup, then falls back to defaults.

        Args:
            content_type: "movie" or "tv"

        Returns:
            Category ID string
        """
        # Try database lookup first
        category_id = Categories.get_category_id_for_type(self.db, content_type)
        if category_id:
            logger.debug(f"Found category ID {category_id} for type {content_type} from database")
            return category_id

        # Fallback: Try to find Vidéo category by slug
        video_cat = Categories.get_by_slug(self.db, 'video')
        if video_cat:
            logger.warning(f"No specific category for '{content_type}', using Vidéo category: {video_cat.category_id}")
            return video_cat.category_id

        # Last resort - should not happen if categories are synced
        logger.error(f"No category found for type '{content_type}' and no Vidéo category in DB!")
        return None

    def validate_tags(self, tag_ids: List[str]) -> Tuple[List[str], List[str]]:
        """
        Validate that tag IDs exist in database.

        Args:
            tag_ids: List of tag IDs to validate

        Returns:
            Tuple of (valid_tag_ids, invalid_tag_ids)
        """
        valid = []
        invalid = []

        for tag_id in tag_ids:
            tag = Tags.get_by_tag_id(self.db, tag_id)
            if tag:
                valid.append(tag_id)
            else:
                invalid.append(tag_id)
                logger.warning(f"Invalid tag ID: {tag_id}")

        return valid, invalid

    def __repr__(self) -> str:
        """String representation of MetadataMapper."""
        return f"<MetadataMapper(cached_tags={len(self._tag_cache)})>"
