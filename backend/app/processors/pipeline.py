"""
ProcessingPipeline for Seedarr v2.0

This module implements the main processing pipeline with checkpoint/resume capability
for idempotent file processing. The pipeline processes media files through multiple
stages with database checkpoints, allowing resumption from any failed stage.

Pipeline Stages:
    1. PENDING → SCANNED: Scan file and extract basic information
    2. SCANNED → ANALYZED: Perform MediaInfo analysis and TMDB validation
    3. ANALYZED → RENAMED: Rename file according to release format
    4. RENAMED → METADATA_GENERATED: Generate .torrent and NFO files
    5. METADATA_GENERATED → UPLOADED: Upload to tracker

Idempotence Strategy:
    - Each stage checks checkpoint timestamps before executing
    - If a stage is already completed (timestamp set), it's skipped
    - Allows retry from any failed stage without duplicating work
    - Example: If .torrent generation succeeds but upload fails,
      retry will skip scan/analyze/rename/metadata and go directly to upload

Features:
    - Checkpoint-based resumption from failed stages
    - Comprehensive error handling with typed exceptions
    - Detailed logging at INFO level for pipeline stages
    - Database transaction management for atomic checkpoint updates
    - Supports async/await for non-blocking operations
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from sqlalchemy.orm import Session

from ..models.file_entry import FileEntry, Status, TrackerStatus
from ..services.exceptions import TrackerAPIError, CloudflareBypassError, NetworkRetryableError, retry_on_network_error
from ..services.nfo_validator import NFOValidator
from ..services.nfo_generator import get_nfo_generator
from ..services.metadata_mapper import MetadataMapper
from ..services.options_mapper import OptionsMapper, get_options_mapper
# C411OptionsMapper removed - all options mapping now via ConfigAdapter + OptionsMapper
from ..adapters.tracker_adapter import TrackerAdapter
from ..adapters.tracker_config_loader import get_config_loader
from ..services.statistics_service import get_statistics_service

logger = logging.getLogger(__name__)


class ProcessingPipeline:
    """
    Main processing pipeline with checkpoint/resume capability.

    This class orchestrates the file processing workflow through multiple stages,
    using database checkpoints to enable idempotent operations. If any stage fails,
    the pipeline can be restarted and will resume from the last completed checkpoint.

    Architecture:
        - Sequential stage processing with checkpoint validation
        - Database-backed state persistence
        - Idempotent design allows safe retries
        - Comprehensive error handling and logging

    Example:
        >>> from app.database import SessionLocal
        >>> from app.models.file_entry import FileEntry
        >>>
        >>> db = SessionLocal()
        >>> pipeline = ProcessingPipeline(db)
        >>>
        >>> # Create or get file entry
        >>> entry = FileEntry.create_or_get(db, "/media/Movie.2024.1080p.mkv")
        >>>
        >>> # Process file (will resume from last checkpoint if retrying)
        >>> await pipeline.process_file(entry)
        >>>
        >>> # If upload fails, retry will skip completed stages:
        >>> # - Skip scan (scanned_at is set)
        >>> # - Skip analysis (analyzed_at is set)
        >>> # - Skip rename (renamed_at is set)
        >>> # - Skip metadata generation (metadata_generated_at is set)
        >>> # - Retry only upload stage
        >>> await pipeline.process_file(entry)
    """

    def __init__(self, db: Session, tracker_adapter: Optional[TrackerAdapter] = None):
        """
        Initialize ProcessingPipeline.

        Args:
            db: SQLAlchemy database session for checkpoint persistence
            tracker_adapter: TrackerAdapter instance for tracker uploads (optional for testing)
                            In production, this should be injected via FastAPI dependency injection
        """
        self.db = db
        self.tracker_adapter = tracker_adapter
        self.nfo_validator = NFOValidator(db)
        self.metadata_mapper = MetadataMapper(db)

    async def process_file(self, file_entry: FileEntry, skip_approval: bool = False) -> None:
        """
        Process a file through all pipeline stages with checkpoint/resume logic.

        This method orchestrates the complete file processing workflow, checking
        checkpoint timestamps before each stage to skip already-completed work.
        If any stage fails, the error is logged and the file_entry status is
        updated to FAILED with error details.

        Pipeline Stages (v2.1):
            1. PENDING → SCANNED: Scan and validate file
            2. SCANNED → ANALYZED: Extract metadata, map tags
            3. ANALYZED → PENDING_APPROVAL: Wait for user approval (PAUSE)
            4. APPROVED → PREPARING: Create hardlinks, screenshots
            5. PREPARING → RENAMED: Format release name
            6. RENAMED → METADATA_GENERATED: Generate .torrent and NFO
            7. METADATA_GENERATED → UPLOADED: Upload to trackers

        Args:
            file_entry: FileEntry to process
            skip_approval: If True, skip approval step (for automated workflows)

        Raises:
            TrackerAPIError: If any stage encounters an unrecoverable error

        Example:
            >>> entry = FileEntry.create_or_get(db, "/media/Movie.mkv")
            >>> await pipeline.process_file(entry)
            >>> # Pipeline pauses at PENDING_APPROVAL
            >>> # After user approves:
            >>> await pipeline.process_file(entry)  # Resumes from APPROVED
        """
        try:
            logger.info(f"Starting pipeline processing for: {file_entry.file_path}")
            logger.info(f"Current status: {file_entry.status.value}")

            # Stage 1: Scan (if not already scanned)
            if not file_entry.is_scanned():
                logger.info(f"Stage 1/7: Scanning file: {file_entry.file_path}")
                await self._scan_stage(file_entry)
                logger.info("✓ Scan stage completed")
            else:
                logger.info("⊘ Scan stage already completed, skipping")

            # Stage 2: Analysis (if not already analyzed)
            if not file_entry.is_analyzed():
                logger.info(f"Stage 2/7: Analyzing file: {file_entry.file_path}")
                await self._analyze_stage(file_entry)
                logger.info("✓ Analysis stage completed")
            else:
                logger.info("⊘ Analysis stage already completed, skipping")

            # Stage 3: Approval checkpoint (v2.1)
            # After analysis, pause for user approval unless skip_approval=True
            if not skip_approval and not file_entry.is_approved():
                if not file_entry.is_pending_approval():
                    # Set to pending approval and STOP pipeline
                    file_entry.mark_pending_approval()
                    self.db.commit()
                    logger.info("⏸ Pipeline paused - waiting for user approval")
                    logger.info(f"  Release: {file_entry.release_name}")
                    logger.info(f"  TMDB ID: {file_entry.tmdb_id}")
                    logger.info("  Use /api/releases/{id}/approve to continue")
                    return  # STOP - wait for approval
                else:
                    logger.info("⏸ Pipeline waiting for approval - not continuing")
                    return  # Still waiting for approval

            # Stage 4: Prepare files (v2.1 - hardlinks, screenshots)
            if not file_entry.is_preparing():
                logger.info(f"Stage 4/7: Preparing files (hardlinks, screenshots): {file_entry.file_path}")
                await self._prepare_files_stage(file_entry)
                logger.info("✓ File preparation stage completed")
            else:
                logger.info("⊘ File preparation already completed, skipping")

            # Stage 5: Rename (if not already renamed)
            if not file_entry.is_renamed():
                logger.info(f"Stage 5/7: Renaming file: {file_entry.file_path}")
                await self._rename_stage(file_entry)
                logger.info("✓ Rename stage completed")
            else:
                logger.info("⊘ Rename stage already completed, skipping")

            # Stage 6: Metadata Generation (if not already generated)
            if not file_entry.is_metadata_generated():
                logger.info(f"Stage 6/7: Generating metadata (.torrent, NFO): {file_entry.file_path}")
                await self._metadata_generation_stage(file_entry)
                logger.info("✓ Metadata generation stage completed")
            else:
                logger.info("⊘ Metadata generation stage already completed, skipping (reusing existing files)")

            # Stage 7: Upload (if not already uploaded)
            if not file_entry.is_uploaded():
                logger.info(f"Stage 7/7: Uploading to tracker: {file_entry.file_path}")
                await self._upload_stage(file_entry)
                logger.info("✓ Upload stage completed")
            else:
                logger.info("⊘ Upload stage already completed, skipping")

            logger.info(f"Pipeline processing completed successfully for: {file_entry.file_path}")

        except (NetworkRetryableError, CloudflareBypassError) as e:
            # Retryable errors - preserve the exception type for upstream retry logic
            error_msg = f"Pipeline failed at stage {file_entry.status.value} (retryable): {e}"
            logger.error(error_msg)
            file_entry.mark_failed(error_msg)
            self.db.commit()
            # Re-raise as-is to allow upstream retry logic to handle it
            raise

        except TrackerAPIError as e:
            # Non-retryable tracker API errors - fail fast
            error_msg = f"Pipeline failed at stage {file_entry.status.value}: {e}"
            logger.error(error_msg)
            file_entry.mark_failed(error_msg)
            self.db.commit()
            raise

        except Exception as e:
            # Unexpected errors - wrap in TrackerAPIError (non-retryable)
            error_msg = f"Unexpected error in pipeline at stage {file_entry.status.value}: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            file_entry.mark_failed(error_msg)
            self.db.commit()
            raise TrackerAPIError(error_msg) from e

    async def _scan_stage(self, file_entry: FileEntry) -> None:
        """
        Stage 1: Scan file and extract basic information.

        This stage performs initial file validation and basic information extraction:
        - Verify file exists
        - Check file size
        - Extract filename components
        - Validate file format

        Args:
            file_entry: FileEntry to scan

        Raises:
            TrackerAPIError: If file validation fails
        """
        logger.debug(f"Executing scan stage for: {file_entry.file_path}")

        file_path = Path(file_entry.file_path)

        # Verify file exists
        if not file_path.exists():
            raise TrackerAPIError(f"File does not exist: {file_entry.file_path}")

        # Check file size
        file_size = file_path.stat().st_size
        if file_size == 0:
            raise TrackerAPIError(f"File is empty: {file_entry.file_path}")

        logger.info(f"File validated: {file_path.name} ({file_size / (1024*1024):.2f} MB)")

        # Validate file format (video extensions)
        valid_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.ts', '.mov', '.wmv'}
        if file_path.suffix.lower() not in valid_extensions:
            raise TrackerAPIError(
                f"Invalid file format: {file_path.suffix}. "
                f"Supported formats: {', '.join(valid_extensions)}"
            )

        # Mark checkpoint and update status
        file_entry.mark_scanned()
        self.db.commit()
        logger.debug(f"Scan checkpoint set at: {file_entry.scanned_at}")

    async def _analyze_stage(self, file_entry: FileEntry) -> None:
        """
        Stage 2: Perform MediaInfo analysis, TMDB lookup, and metadata mapping.

        This stage performs deep media analysis:
        - Extract technical info using MediaInfo (codec, bitrate, resolution, etc.)
        - Parse filename to extract metadata (resolution, source, codec, etc.)
        - Map metadata to tracker category and tags
        - Fetch TMDB/IMDB metadata (title, year, plot, poster)
        - Store all metadata in file_entry for upload stage

        Args:
            file_entry: FileEntry to analyze

        Raises:
            TrackerAPIError: If analysis fails
        """
        logger.debug(f"Executing analysis stage for: {file_entry.file_path}")

        file_path = Path(file_entry.file_path)
        filename = file_path.name

        # =====================================================================
        # Step 1: Extract full MediaInfo data
        # =====================================================================
        logger.info(f"Extracting MediaInfo from: {filename}")
        try:
            nfo_generator = get_nfo_generator()
            media_data = await nfo_generator.extract_mediainfo(str(file_path))

            # Convert MediaInfo data to dict for storage
            mediainfo_dict = {
                'file_name': media_data.file_name,
                'format': media_data.format,
                'file_size': media_data.file_size,
                'duration': media_data.duration,
                'overall_bitrate': media_data.overall_bitrate,
                'video_tracks': [
                    {
                        'codec': v.format,
                        'width': v.width,
                        'height': v.height,
                        'resolution': v.resolution,
                        'frame_rate': v.frame_rate,
                        'bit_depth': v.bit_depth,
                        'hdr_format': v.hdr_format,
                        'bitrate': v.bitrate
                    } for v in media_data.video_tracks
                ],
                'audio_tracks': [
                    {
                        'codec': a.format,
                        'channels': a.channels,
                        'language': a.language or self._infer_audio_language(file_path.name, i, len(media_data.audio_tracks)),
                        'bitrate': a.bitrate,
                        'title': a.title,
                        'channel_layout': getattr(a, 'channel_layout', None)
                    } for i, a in enumerate(media_data.audio_tracks)
                ],
                'subtitle_tracks': [
                    {
                        'language': s.language,
                        'format': s.format,
                        'title': s.title
                    } for s in media_data.subtitle_tracks
                ]
            }

            # Extract key info for display
            if media_data.video_tracks:
                v = media_data.video_tracks[0]
                logger.info(f"✓ Video: {v.format} {v.width}x{v.height} @ {v.frame_rate}, {v.bit_depth} {v.hdr_format or 'SDR'}")
            if media_data.audio_tracks:
                for a in media_data.audio_tracks:
                    logger.info(f"✓ Audio: {a.format} {a.channels} ({a.language})")
            if media_data.subtitle_tracks:
                logger.info(f"✓ Subtitles: {len(media_data.subtitle_tracks)} track(s)")

        except Exception as e:
            logger.warning(f"⚠ MediaInfo extraction failed: {e}")
            mediainfo_dict = {}

        # =====================================================================
        # Step 2: Map filename to tracker category and tags
        # =====================================================================
        logger.info(f"Mapping metadata from filename: {filename}")
        mapping_result = self.metadata_mapper.map_from_filename(filename)

        # Store category and tags in file_entry
        file_entry.category_id = mapping_result['category_id']
        file_entry.set_tag_ids(mapping_result['tag_ids'])

        # Fallback: detect source from MediaInfo if filename parsing didn't find one
        if not mapping_result['parsed_metadata'].get('source') and mediainfo_dict:
            detected_source = self.metadata_mapper.detect_source_from_mediainfo(mediainfo_dict)
            if detected_source:
                mapping_result['parsed_metadata']['source'] = detected_source
                # Also map the detected source to a tag
                source_tag_id = self.metadata_mapper._get_tag_id(detected_source)
                if source_tag_id:
                    mapping_result['tag_ids'].append(source_tag_id)
                    file_entry.set_tag_ids(mapping_result['tag_ids'])
                    logger.info(f"✓ Source '{detected_source}' detected from MediaInfo and mapped to tag")
                else:
                    logger.warning(f"⚠ Source '{detected_source}' detected from MediaInfo but no matching tag found")

        # Merge parsed filename metadata with MediaInfo data
        mediainfo_dict['parsed_from_filename'] = mapping_result['parsed_metadata']
        file_entry.mediainfo_data = mediainfo_dict

        # Log warnings if any tags couldn't be mapped
        warnings = mapping_result.get('warnings', [])
        if warnings:
            logger.warning(f"⚠ {len(warnings)} metadata field(s) could not be mapped to tracker tags:")
            for warning in warnings:
                logger.warning(f"  - {warning}")
            logger.warning("This may cause upload issues if the tracker requires tags.")

        # Log successfully mapped tags
        if mapping_result['tag_ids']:
            logger.info(f"✓ Successfully mapped {len(mapping_result['tag_ids'])} tag(s): {mapping_result['tag_ids']}")
        else:
            logger.warning("⚠ No tags were mapped from filename - upload will proceed without tags")

        # Determine release name from filename (strip extension)
        file_entry.release_name = file_path.stem

        # =====================================================================
        # Step 3: Fetch TMDB metadata
        # =====================================================================
        logger.info("Searching TMDB for metadata...")
        try:
            from app.services.tmdb_cache_service import TMDBCacheService
            tmdb_service = TMDBCacheService(self.db)
            is_tv = mapping_result['parsed_metadata'].get('is_tv_show', False)
            tmdb_data = await tmdb_service.search_and_get_metadata(str(file_path), is_tv_show=is_tv)

            if tmdb_data:
                # Store TMDB data in file_entry
                file_entry.tmdb_id = tmdb_data.get('tmdb_id')
                file_entry.tmdb_type = 'tv' if is_tv else 'movie'
                file_entry.description = tmdb_data.get('plot', '')
                file_entry.cover_url = tmdb_data.get('poster_url')

                # Also store in mediainfo_data for complete reference
                file_entry.mediainfo_data['tmdb'] = {
                    'tmdb_id': tmdb_data.get('tmdb_id'),
                    'title': tmdb_data.get('title'),
                    'original_title': tmdb_data.get('extra_data', {}).get('original_title'),
                    'year': tmdb_data.get('year'),
                    'plot': tmdb_data.get('plot'),
                    'poster_url': tmdb_data.get('poster_url'),
                    'backdrop_url': tmdb_data.get('backdrop_url'),
                    'genres': tmdb_data.get('genres', []),
                    'cast': tmdb_data.get('cast', []),
                    'ratings': tmdb_data.get('ratings', {})
                }

                logger.info(
                    f"✓ TMDB metadata found: {tmdb_data.get('title')} ({tmdb_data.get('year')}) "
                    f"- ID: {file_entry.tmdb_id}"
                )
            else:
                logger.warning("⚠ No TMDB metadata found - upload will proceed without TMDB data")
        except Exception as e:
            logger.warning(f"⚠ TMDB lookup failed: {e} - upload will proceed without TMDB data")

        logger.info(
            f"Analysis complete: category_id={file_entry.category_id}, "
            f"tags={len(file_entry.get_tag_ids())}, "
            f"release_name={file_entry.release_name}, "
            f"tmdb_id={file_entry.tmdb_id}"
        )

        # Mark checkpoint and update status
        file_entry.mark_analyzed()
        self.db.commit()
        logger.debug(f"Analysis checkpoint set at: {file_entry.analyzed_at}")

    def _infer_audio_language(self, filename: str, track_index: int, total_tracks: int) -> str:
        """
        Infer audio language from filename when MediaInfo doesn't provide it.

        Args:
            filename: The filename to parse
            track_index: Index of the current audio track (0-based)
            total_tracks: Total number of audio tracks

        Returns:
            Inferred language code or empty string
        """
        import re
        filename_upper = filename.upper()

        # Single audio track - use filename language
        if total_tracks == 1:
            if 'FRENCH' in filename_upper or 'VFF' in filename_upper or 'TRUEFRENCH' in filename_upper:
                return 'fra'
            elif 'ENGLISH' in filename_upper or '.VO.' in filename_upper:
                return 'eng'
            # Default to French for French trackers if no language indicator
            return 'fra'

        # Multiple audio tracks - try to infer based on common patterns
        # MULTI usually means French + English (or original)
        if 'MULTI' in filename_upper or 'MULTi' in filename:
            if track_index == 0:
                # First track is usually French on French trackers
                if 'VFF' in filename_upper:
                    return 'fra'
                elif 'VFQ' in filename_upper:
                    return 'fra'  # Quebec French is still French
                return 'fra'
            elif track_index == 1:
                # Second track is usually English/original
                return 'eng'

        return ''

    async def _prepare_files_stage(self, file_entry: FileEntry) -> None:
        """
        Stage 4 (v2.1): Prepare release files - hardlinks and screenshots.

        This stage creates the release folder structure and captures screenshots:
        1. Create release folder with hardlinked media file
        2. Generate screenshots at key timestamps
        3. Upload screenshots to ImgBB (if configured)
        4. Store paths and URLs in file_entry

        The hardlink saves disk space by pointing to the same data as the original.
        If cross-filesystem, falls back to copy.

        Args:
            file_entry: FileEntry to prepare

        Raises:
            TrackerAPIError: If critical preparation fails
        """
        from app.models.settings import Settings
        from ..services.hardlink_manager import get_hardlink_manager, HardlinkError
        from ..services.screenshot_generator import get_screenshot_generator, ScreenshotError

        logger.debug(f"Executing prepare files stage for: {file_entry.file_path}")

        file_path = Path(file_entry.file_path)
        settings = Settings.get_settings(self.db)

        # Use effective release name (user-corrected or original)
        release_name = file_entry.get_effective_release_name() or file_path.stem

        # Step 1: Create release structure with hardlinks
        logger.info("Creating release structure with hardlinks...")
        try:
            hardlink_manager = get_hardlink_manager()
            output_dir = settings.output_dir if settings else None

            structure = hardlink_manager.create_release_structure(
                source_file=str(file_path),
                release_name=release_name,
                output_dir=output_dir
            )

            # Store paths in file_entry
            file_entry.release_dir = structure['release_dir']
            file_entry.prepared_media_path = structure['media_file']

            logger.info(
                f"✓ Release structure created: {structure['release_dir']} "
                f"(hardlink={'yes' if structure['hardlink_used'] else 'no'})"
            )

        except HardlinkError as e:
            logger.error(f"Hardlink creation failed: {e}")
            raise TrackerAPIError(f"Failed to create release structure: {e}") from e

        except FileNotFoundError as e:
            logger.error(f"Source file not found: {e}")
            raise TrackerAPIError(f"Source file not found: {e}") from e

        # Step 2: Generate screenshots (optional - degrades gracefully)
        logger.info("Generating screenshots...")
        try:
            screenshot_generator = get_screenshot_generator()

            if screenshot_generator.is_available():
                screenshot_paths = await screenshot_generator.generate_screenshots(
                    video_path=str(file_path),
                    output_dir=structure['screens_dir'],
                    release_name=release_name,
                    count=4
                )

                file_entry.set_screenshot_paths(screenshot_paths)
                logger.info(f"✓ Generated {len(screenshot_paths)} screenshots")

                # Step 3: Upload screenshots to ImgBB (if configured)
                if settings and settings.imgbb_api_key:
                    logger.info("Uploading screenshots to ImgBB...")
                    try:
                        from ..adapters.imgbb_adapter import get_imgbb_adapter

                        imgbb = get_imgbb_adapter(api_key=settings.imgbb_api_key)
                        upload_results = await imgbb.upload_images(screenshot_paths)

                        # Filter successful uploads
                        successful_uploads = [r for r in upload_results if r.get('success')]
                        file_entry.set_screenshot_urls(successful_uploads)

                        logger.info(f"✓ Uploaded {len(successful_uploads)} screenshots to ImgBB")

                    except Exception as e:
                        logger.warning(f"⚠ Screenshot upload failed: {e} - continuing without hosted screenshots")

                else:
                    logger.info("⊘ ImgBB not configured - screenshots saved locally only")

            else:
                logger.warning("⚠ FFmpeg not available - skipping screenshot generation")

        except ScreenshotError as e:
            logger.warning(f"⚠ Screenshot generation failed: {e} - continuing without screenshots")

        # Mark checkpoint and update status
        file_entry.mark_preparing()
        self.db.commit()
        logger.debug(f"Prepare files checkpoint set at: {file_entry.preparing_at}")

    async def _rename_stage(self, file_entry: FileEntry) -> None:
        """
        Stage 3: Rename file according to universal release format.

        This stage renames the file to match the universal naming convention
        that satisfies all trackers (La Cale, C411, etc.):
        - Format: Title.Year.Language.Resolution.Source.AudioCodec.VideoCodec-Team.ext
        - Example: Gladiator.II.2024.FRENCH.1080p.WEB.EAC3.x264-TP.mkv
        - Update file_entry.file_path and release_name

        Conditional Logic:
        - If file already has scene format with team -> preserve original name
        - Otherwise -> apply universal renaming convention

        Args:
            file_entry: FileEntry to rename

        Raises:
            TrackerAPIError: If rename operation fails
        """
        from ..services.universal_renamer import get_universal_renamer

        logger.debug(f"Executing rename stage for: {file_entry.file_path}")

        file_path = Path(file_entry.file_path)
        renamer = get_universal_renamer()

        # Extract existing team tag (preserve original team if present)
        existing_team = renamer.extract_team_from_filename(file_path.name)
        if existing_team:
            logger.info(f"Preserving existing team tag: {existing_team}")

        # Extract metadata from parsed filename and MediaInfo for renaming
        metadata = file_entry.mediainfo_data or {}
        parsed = metadata.get('parsed_from_filename', {})
        tmdb = metadata.get('tmdb', {})

        # Get components for release name from the correct sources
        # Title/Year: prefer TMDB data, then parsed title, then fallback to filename stem
        # Important: Never use file_path.stem directly as it includes metadata (resolution, codec, etc.)
        # Apply .title() to parsed fallback to ensure proper capitalization (filename titles are often lowercase)
        parsed_title = parsed.get('title')
        if parsed_title:
            parsed_title = parsed_title.title()
        title = tmdb.get('title') or parsed_title or file_path.stem
        year = tmdb.get('year') or parsed.get('year')

        # Technical metadata: from parsed filename, with MediaInfo fallback
        language = parsed.get('language')
        resolution = parsed.get('resolution')
        source = parsed.get('source')
        audio_codec = parsed.get('audio')
        video_codec = parsed.get('codec')
        team = existing_team or 'NOTAG'

        # Fallback to MediaInfo data when filename parsing found nothing
        video_tracks = metadata.get('video_tracks', [])
        audio_tracks = metadata.get('audio_tracks', [])

        # Detect language from MediaInfo audio tracks
        # If multiple languages detected (e.g., French + English), use MULTI
        logger.debug(f"Audio tracks from MediaInfo: {audio_tracks}")
        logger.debug(f"Language from filename parsing: {language}")

        if audio_tracks:
            detected_languages = set()
            for track in audio_tracks:
                track_lang = (track.get('language') or '').lower()
                logger.debug(f"Audio track language: '{track_lang}'")
                if track_lang in ('fr', 'fra', 'fre', 'french'):
                    detected_languages.add('french')
                elif track_lang in ('en', 'eng', 'english'):
                    detected_languages.add('english')
                elif track_lang:
                    detected_languages.add(track_lang)

            logger.info(f"Detected audio languages: {detected_languages} (count: {len(detected_languages)})")

            if len(detected_languages) > 1:
                # Multiple languages = MULTI, override any filename-parsed language
                language = 'MULTI'
                logger.info(f"Multiple audio languages detected: {detected_languages} -> MULTI")
            elif not language and detected_languages:
                # Single language from MediaInfo
                if 'french' in detected_languages:
                    language = 'FRENCH'
                elif 'english' in detected_languages:
                    language = 'ENGLISH'
                logger.info(f"Language from MediaInfo: {language}")
        else:
            logger.warning("No audio tracks found in MediaInfo data for language detection")

        # Default language if still not set
        if not language:
            language = 'FRENCH'

        logger.info(f"Final language for release name: {language}")

        # Detect French audio version (VFF, VOF, VFI, TRUEFRENCH) from audio track titles
        # This is used when language is MULTI to specify which French version is included
        detected_french_version = None
        if audio_tracks:
            for track in audio_tracks:
                track_lang = (track.get('language') or '').lower()
                track_title = (track.get('title') or '').upper()

                # Only check French audio tracks
                if track_lang in ('fr', 'fra', 'fre', 'french'):
                    if 'TRUEFRENCH' in track_title or 'TRUE FRENCH' in track_title:
                        detected_french_version = 'TRUEFRENCH'
                        break  # TRUEFRENCH is highest priority
                    elif 'VFF' in track_title:
                        detected_french_version = 'VFF'
                    elif 'VOF' in track_title:
                        detected_french_version = 'VOF'
                    elif 'VFI' in track_title:
                        detected_french_version = 'VFI'
                    elif 'VFQ' in track_title:
                        detected_french_version = 'VFQ'
                    elif 'VF2' in track_title:
                        detected_french_version = 'VF2'

            if detected_french_version:
                logger.info(f"Detected French audio version from track title: {detected_french_version}")
            elif language == 'MULTI':
                # Default to VFF if MULTI but no version specified in audio track titles
                detected_french_version = 'VFF'
                logger.info(f"No French version in audio titles, defaulting to: {detected_french_version}")

        if not resolution and video_tracks:
            height = video_tracks[0].get('height', 0)
            if height >= 2160:
                resolution = '2160p'
            elif height >= 1080:
                resolution = '1080p'
            elif height >= 720:
                resolution = '720p'
            elif height >= 576:
                resolution = '576p'
            elif height >= 480:
                resolution = '480p'
            if resolution:
                logger.info(f"Resolution from MediaInfo: {resolution}")

        if not video_codec and video_tracks:
            v_format = (video_tracks[0].get('codec') or '').upper()
            if 'HEVC' in v_format or 'H265' in v_format:
                video_codec = 'x265'
            elif 'AVC' in v_format or 'H264' in v_format or 'H.264' in v_format:
                video_codec = 'x264'
            elif 'AV1' in v_format:
                video_codec = 'AV1'
            if video_codec:
                logger.info(f"Video codec from MediaInfo: {video_codec}")

        if not audio_codec and audio_tracks:
            a_format = (audio_tracks[0].get('codec') or '').upper()
            if 'ATMOS' in a_format:
                audio_codec = 'Atmos'
            elif 'TRUEHD' in a_format:
                audio_codec = 'TrueHD'
            elif 'DTS-HD MA' in a_format or 'DTS-HD.MA' in a_format:
                audio_codec = 'DTS-HD.MA'
            elif 'DTS-HD' in a_format:
                audio_codec = 'DTS-HD'
            elif 'DTS' in a_format:
                audio_codec = 'DTS'
            elif 'E-AC-3' in a_format or 'EAC3' in a_format:
                audio_codec = 'EAC3'
            elif 'AC-3' in a_format or 'AC3' in a_format:
                audio_codec = 'AC3'
            elif 'AAC' in a_format:
                audio_codec = 'AAC'
            elif 'FLAC' in a_format:
                audio_codec = 'FLAC'
            if audio_codec:
                logger.info(f"Audio codec from MediaInfo: {audio_codec}")

        # C411-specific fields
        hdr = parsed.get('hdr')
        if not hdr and video_tracks:
            mi_hdr = video_tracks[0].get('hdr_format', '')
            if mi_hdr:
                hdr = mi_hdr
                logger.info(f"HDR from MediaInfo: {hdr}")
        remux = parsed.get('remux', False)
        repack = parsed.get('repack', False)
        imax = parsed.get('imax', False)
        edition = parsed.get('edition')
        # Language variant: use filename-parsed value, or detected from audio tracks
        language_variant = parsed.get('language_variant')
        if not language_variant and language == 'MULTI' and detected_french_version:
            language_variant = detected_french_version
            logger.info(f"Using detected French version as language_variant: {language_variant}")

        is_tv_show = parsed.get('is_tv_show', False)

        # Extract season/episode from filename for TV shows
        season = None
        episode = None
        if is_tv_show:
            import re
            se_match = re.search(r's(\d{1,2})e(\d{1,2})', file_path.name, re.IGNORECASE)
            if se_match:
                season = int(se_match.group(1))
                episode = int(se_match.group(2))
            else:
                s_match = re.search(r's(\d{1,2})(?!\d)', file_path.name, re.IGNORECASE)
                if s_match:
                    season = int(s_match.group(1))

        # Extract audio channels from first audio track
        audio_channels = None
        if audio_tracks and audio_tracks[0].get('channels'):
            channels = audio_tracks[0].get('channels', '')
            channel_map = {'2': '2.0', '6': '5.1', '8': '7.1', '1': '1.0'}
            audio_channels = channel_map.get(str(channels), str(channels))
            if audio_tracks[0].get('channel_layout'):
                layout = audio_tracks[0].get('channel_layout', '')
                if '5.1' in layout:
                    audio_channels = '5.1'
                elif '7.1' in layout:
                    audio_channels = '7.1'
                elif '2.0' in layout or 'stereo' in layout.lower():
                    audio_channels = '2.0'
            if audio_channels:
                logger.info(f"Audio channels from MediaInfo: {audio_channels}")

        # Generate universal release name
        try:
            release_name = renamer.format_release_name(
                title=title,
                year=year if not is_tv_show else None,
                language=language,
                resolution=resolution,
                source=source,
                audio_codec=audio_codec,
                video_codec=video_codec,
                team=team,
                season=season,
                episode=episode,
                hdr=hdr,
                remux=remux,
                repack=repack,
                imax=imax,
                edition=edition,
                language_variant=language_variant,
                audio_channels=audio_channels,
            )

            logger.info(f"Generated release name: {release_name}")
            file_entry.release_name = release_name

            # Note: Physical file renaming is optional - can be configured
            # For now, we just update the release_name but keep original file
            # This allows the original file structure to be preserved

        except Exception as e:
            logger.warning(f"Could not generate release name: {e}")
            # Fallback to original filename stem
            file_entry.release_name = file_path.stem
            logger.info(f"Using original filename as release name: {file_entry.release_name}")

        # Mark checkpoint and update status
        file_entry.mark_renamed()
        self.db.commit()
        logger.debug(f"Rename checkpoint set at: {file_entry.renamed_at}")

    async def _metadata_generation_stage(self, file_entry: FileEntry) -> None:
        """
        Stage 4: Generate .torrent files for all enabled trackers and NFO.

        This stage creates distribution metadata files:
        - Generate one .torrent file per enabled tracker (unique hash per tracker)
        - Generate technical NFO file using MediaInfo data
        - Store file paths for upload stage

        Multi-Tracker Support:
        - Each tracker gets its own .torrent file with unique source flag
        - Piece sizes are calculated per tracker's strategy
        - Torrent paths stored in file_entry.torrent_paths dict

        Args:
            file_entry: FileEntry to generate metadata for

        Raises:
            TrackerAPIError: If metadata generation fails
        """
        from app.models.settings import Settings
        from app.models.tracker import Tracker
        from app.services.torrent_generator import get_torrent_generator, TorrentGenerationError
        from app.services.universal_renamer import get_universal_renamer

        logger.debug(f"Executing metadata generation stage for: {file_entry.file_path}")

        file_path = Path(file_entry.file_path)
        output_dir = file_path.parent  # Store torrent/NFO next to the media file
        release_name = file_entry.release_name or file_path.stem

        # Step 1: Generate .torrent files for all enabled trackers
        logger.info("Generating .torrent files for enabled trackers...")

        # Get enabled trackers
        trackers = Tracker.get_enabled(self.db)

        if trackers:
            # Multi-tracker mode: generate torrent per tracker
            logger.info(f"Found {len(trackers)} enabled tracker(s): {[t.name for t in trackers]}")

            torrent_generator = get_torrent_generator()
            renamer = get_universal_renamer()

            # Build tracker-specific release names using naming_template
            tracker_release_names = {}
            trackers_with_templates = [t for t in trackers if t.naming_template]

            if trackers_with_templates:
                # Extract metadata for template formatting from file_entry
                metadata = file_entry.mediainfo_data or {}
                parsed = metadata.get('parsed_from_filename', {})
                tmdb = metadata.get('tmdb', {})
                video_tracks = metadata.get('video_tracks', [])
                audio_tracks = metadata.get('audio_tracks', [])

                # Get components for template formatting
                existing_team = renamer.extract_team_from_filename(file_path.name)

                # Extract audio channels from first audio track
                audio_channels = None
                if audio_tracks and audio_tracks[0].get('channels'):
                    channels = audio_tracks[0].get('channels', '')
                    # Normalize channels format (e.g., "6" -> "5.1", "2" -> "2.0", "8" -> "7.1")
                    channel_map = {'2': '2.0', '6': '5.1', '8': '7.1', '1': '1.0'}
                    audio_channels = channel_map.get(str(channels), str(channels))
                    # Also check for explicit channel layout like "5.1"
                    if audio_tracks[0].get('channel_layout'):
                        layout = audio_tracks[0].get('channel_layout', '')
                        if '5.1' in layout:
                            audio_channels = '5.1'
                        elif '7.1' in layout:
                            audio_channels = '7.1'
                        elif '2.0' in layout or 'stereo' in layout.lower():
                            audio_channels = '2.0'

                # Determine quality indicator
                quality = parsed.get('quality', '')
                source = parsed.get('source', '')
                if not quality:
                    # Try to infer quality from source/filename
                    filename_lower = file_path.name.lower()
                    if 'hdlight' in filename_lower or 'hd.light' in filename_lower:
                        quality = 'HDLight'
                    elif 'remux' in filename_lower:
                        quality = 'REMUX'
                    elif source and 'web' in source.lower():
                        # WEB encodes (x264 or x265) are typically HDLight style
                        quality = 'HDLight'

                # Build template metadata
                # TMDB 'title' is the French title (API called with language=fr-FR)
                # TMDB 'original_title' is the original (usually English) title
                # Apply .title() to parsed fallback for proper capitalization
                parsed_title = parsed.get('title')
                if parsed_title:
                    parsed_title = parsed_title.title()
                title_fr = tmdb.get('title') or parsed_title or file_path.stem
                title_en = tmdb.get('original_title') or parsed_title or file_path.stem
                # base_title uses French title if available
                base_title = title_fr

                template_metadata = renamer.build_template_metadata(
                    title=base_title,
                    year=tmdb.get('year') or parsed.get('year'),
                    language=parsed.get('language') or 'FRENCH',
                    resolution=parsed.get('resolution'),
                    source=source,
                    audio_codec=parsed.get('audio'),
                    video_codec=parsed.get('codec'),
                    team=existing_team or 'NOTAG',
                    season=parsed.get('season'),
                    episode=parsed.get('episode'),
                    hdr=parsed.get('hdr') or (video_tracks[0].get('hdr_format') if video_tracks else None),
                    title_fr=title_fr,
                    title_en=title_en,
                    audio_channels=audio_channels,
                    quality=quality,
                )

                # Generate tracker-specific names
                for tracker in trackers_with_templates:
                    try:
                        tracker_name = renamer.format_with_template(
                            tracker.naming_template,
                            template_metadata
                        )
                        tracker_release_names[tracker.slug] = tracker_name
                        logger.info(f"Tracker {tracker.name} release name: {tracker_name}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to format naming template for {tracker.name}: {e}. "
                            f"Using default release name."
                        )

            try:
                torrent_paths = await torrent_generator.generate_all(
                    db=self.db,
                    file_path=str(file_path),
                    release_name=release_name,
                    output_dir=str(output_dir),
                    tracker_release_names=tracker_release_names if tracker_release_names else None
                )

                if not torrent_paths:
                    raise TrackerAPIError(
                        "No torrent files generated. Check tracker configuration."
                    )

                # Store all torrent paths in file_entry
                for tracker_slug, path in torrent_paths.items():
                    file_entry.set_torrent_path_for_tracker(tracker_slug, path)
                    logger.info(f"✓ Generated torrent for {tracker_slug}: {Path(path).name}")

                # Store tracker-specific release names for use during upload
                for tracker_slug, tracker_name in tracker_release_names.items():
                    file_entry.set_tracker_release_name(tracker_slug, tracker_name)

                # Set legacy single torrent path to the first one (backward compatibility)
                first_tracker_slug = list(torrent_paths.keys())[0]
                file_entry.torrent_path = torrent_paths[first_tracker_slug]

                logger.info(f"✓ Generated {len(torrent_paths)} torrent file(s)")

            except TorrentGenerationError as e:
                error_msg = f"Multi-tracker torrent generation failed: {e}"
                logger.error(error_msg)
                raise TrackerAPIError(error_msg) from e
            except Exception as e:
                error_msg = f"Torrent generation failed: {e}"
                logger.error(error_msg)
                raise TrackerAPIError(error_msg) from e

        else:
            # Fallback: Legacy single-tracker mode from Settings
            logger.warning("No trackers configured, falling back to Settings announce URL")
            settings = Settings.get_settings(self.db)

            if not settings or not settings.announce_url:
                raise TrackerAPIError(
                    "Cannot generate torrent: No trackers configured and no "
                    "announce URL in Settings. Please add trackers or configure Settings."
                )

            # Generate single torrent using TorrentGenerator
            torrent_generator = get_torrent_generator()
            try:
                torrent_path = await torrent_generator.generate_single_tracker_torrent(
                    file_path=str(file_path),
                    announce_url=settings.announce_url,
                    release_name=release_name,
                    source_flag="seedarr",
                    piece_size_strategy="auto",
                    output_dir=str(output_dir)
                )

                file_entry.torrent_path = torrent_path
                file_entry.set_torrent_path_for_tracker("default", torrent_path)
                logger.info(f"✓ Torrent file generated: {torrent_path}")

            except Exception as e:
                error_msg = f"Torrent generation failed: {e}"
                logger.error(error_msg)
                raise TrackerAPIError(error_msg) from e

        # Step 2: Generate technical NFO file using MediaInfo
        logger.info("Generating technical NFO file with MediaInfo...")

        try:
            nfo_generator = get_nfo_generator()
            nfo_path = await nfo_generator.generate_nfo(
                file_path=file_entry.file_path,
                media_type="Movies",  # TODO: Detect media type from file analysis
                release_name=release_name  # Use release name for NFO filename and content
            )

            file_entry.nfo_path = str(nfo_path)
            logger.info(f"✓ Technical NFO generated: {nfo_path}")

        except Exception as e:
            # NFO generation failed - BLOCK pipeline
            error_msg = f"NFO generation failed - pipeline blocked: {e}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg) from e

        # Mark checkpoint and update status
        file_entry.mark_metadata_generated()
        self.db.commit()

        torrent_count = len(file_entry.get_torrent_paths())
        logger.info(
            f"✓ Metadata generation complete: "
            f"{torrent_count} torrent(s), nfo={Path(file_entry.nfo_path).name}"
        )
        logger.debug(f"Metadata generation checkpoint set at: {file_entry.metadata_generated_at}")

    async def _upload_stage(self, file_entry: FileEntry) -> None:
        """
        Stage 5: Upload to all enabled trackers.

        This stage uploads the torrent to all enabled trackers:
        - Loop through upload-enabled trackers
        - Authenticate with each tracker
        - Upload tracker-specific .torrent file
        - Track upload results per tracker
        - Inject torrents into qBittorrent for seeding

        Multi-Tracker Support:
        - Uses TrackerFactory to get adapters for enabled trackers
        - Each tracker gets its specific .torrent file
        - Upload results stored per tracker in file_entry.upload_results
        - Continues with other trackers if one fails

        Args:
            file_entry: FileEntry to upload

        Raises:
            TrackerAPIError: If all uploads fail
            CloudflareBypassError: If FlareSolverr authentication fails (retryable)
            NetworkRetryableError: If network issues occur (retryable)
        """
        from app.models.settings import Settings
        from app.models.tracker import Tracker
        from app.adapters.tracker_factory import TrackerFactory

        logger.debug(f"Executing upload stage for: {file_entry.file_path}")

        # Verify required metadata is available
        if not file_entry.release_name:
            raise TrackerAPIError("Release name not set - run analysis stage first")

        # Note: category_id can be None here - trackers may provide default categories

        # Get enabled trackers for upload
        trackers = Tracker.get_upload_enabled(self.db)

        # Fallback to legacy single tracker if no trackers configured
        if not trackers and self.tracker_adapter:
            logger.warning("No trackers configured, using legacy single-tracker mode")
            await self._upload_to_single_tracker(file_entry)
            return

        if not trackers:
            raise TrackerAPIError(
                "No trackers configured for upload. "
                "Please add and enable at least one tracker."
            )

        logger.info(f"Uploading to {len(trackers)} tracker(s): {[t.name for t in trackers]}")

        # Get FlareSolverr URL from settings for trackers that need it
        settings = Settings.get_settings(self.db)
        flaresolverr_url = settings.flaresolverr_url if settings else None

        # Create tracker factory
        factory = TrackerFactory(
            db=self.db,
            flaresolverr_url=flaresolverr_url
        )

        # Read NFO file (shared across all trackers)
        nfo_data = None
        if file_entry.nfo_path and os.path.exists(file_entry.nfo_path):
            with open(file_entry.nfo_path, 'rb') as f:
                nfo_data = f.read()
            logger.info(f"Loaded NFO file: {file_entry.nfo_path}")
        else:
            raise TrackerAPIError("NFO file not available - run metadata generation stage first")

        # Prepare common upload metadata
        release_name = file_entry.release_name
        category_id = file_entry.category_id
        tag_ids = file_entry.get_tag_ids()
        description = file_entry.description
        tmdb_id = file_entry.tmdb_id
        tmdb_type = file_entry.tmdb_type
        cover_url = file_entry.cover_url

        if not description:
            description = f"Release: {release_name}"

        logger.info(f"Upload metadata prepared:")
        logger.info(f"  - release_name: {release_name}")
        logger.info(f"  - category_id: {category_id}")
        logger.info(f"  - tag_ids: {tag_ids if tag_ids else '(none)'}")

        # Extract resolution from parsed metadata (shared across all trackers)
        resolution = None
        if file_entry.mediainfo_data:
            parsed_meta = file_entry.mediainfo_data.get('parsed_from_filename', {})
            resolution = parsed_meta.get('resolution')
        logger.info(f"  - resolution: {resolution}")

        # Extract genres from TMDB data (shared across all trackers)
        tmdb_genres = []
        if file_entry.mediainfo_data and 'tmdb' in file_entry.mediainfo_data:
            tmdb_genres = file_entry.mediainfo_data['tmdb'].get('genres', [])
        # Fallback: get from TMDB cache
        if not tmdb_genres and tmdb_id:
            try:
                from app.models.tmdb_cache import TMDBCache
                cache_entry = TMDBCache.get_cached(self.db, tmdb_id)
                if cache_entry and cache_entry.extra_data:
                    genres_raw = cache_entry.extra_data.get('genres', [])
                    for g in genres_raw:
                        if isinstance(g, dict):
                            tmdb_genres.append(g)
                        else:
                            tmdb_genres.append({"name": g})
            except Exception as e:
                logger.warning(f"Failed to get genres from cache: {e}")

        if tmdb_genres:
            logger.info(f"  - genres: {[g.get('name', g) for g in tmdb_genres[:5]]}")

        # Initialize tracker statuses as PENDING
        qbittorrent_injected = False

        for tracker in trackers:
            # Initialize status as PENDING for each tracker
            file_entry.set_tracker_status(
                tracker_slug=tracker.slug,
                status=TrackerStatus.PENDING.value
            )
        self.db.commit()

        for tracker in trackers:
            logger.info(f"\n{'='*50}")
            logger.info(f"Uploading to tracker: {tracker.name}")
            logger.info(f"{'='*50}")

            # Get tracker-specific release name (from naming_template) or default
            tracker_release_name = file_entry.get_effective_release_name_for_tracker(tracker.slug)
            logger.info(f"  - tracker release_name: {tracker_release_name}")

            try:
                # Get adapter for this tracker
                adapter = factory.get_adapter(tracker)

                # Get tracker-specific torrent file
                torrent_path = file_entry.get_torrent_path_for_tracker(tracker.slug)
                if not torrent_path:
                    # Fallback to legacy single torrent path
                    torrent_path = file_entry.torrent_path

                if not torrent_path or not os.path.exists(torrent_path):
                    raise TrackerAPIError(
                        f"Torrent file not found for {tracker.name}: {torrent_path}"
                    )

                # Read torrent data
                with open(torrent_path, 'rb') as f:
                    torrent_data = f.read()
                logger.info(f"Loaded torrent: {torrent_path} ({len(torrent_data)} bytes)")

                # Authenticate with tracker
                logger.info(f"Authenticating with {tracker.name}...")
                authenticated = await adapter.authenticate()

                if not authenticated:
                    raise TrackerAPIError(
                        f"Authentication failed for {tracker.name}"
                    )

                logger.info(f"✓ Authenticated with {tracker.name}")

                # Check for duplicates before upload
                logger.info(f"Checking for duplicates on {tracker.name}...")
                try:
                    duplicate_result = await adapter.check_duplicate(
                        tmdb_id=tmdb_id,
                        imdb_id=file_entry.imdb_id if hasattr(file_entry, 'imdb_id') else None,
                        release_name=tracker_release_name,
                        quality=resolution,
                        file_size=file_entry.file_size  # Pass file size for exact match detection
                    )

                    # Block upload only if EXACT match found (same file size)
                    if duplicate_result.get('exact_match'):
                        exact_matches = duplicate_result.get('exact_matches', [])
                        search_method = duplicate_result.get('search_method', 'unknown')
                        logger.warning(
                            f"EXACT DUPLICATE on {tracker.name} - same file size detected!"
                        )
                        for match in exact_matches[:3]:
                            logger.warning(f"  - {match.get('name')} ({match.get('size', 0) / 1073741824:.2f} GB)")
                        file_entry.set_tracker_status(
                            tracker_slug=tracker.slug,
                            status=TrackerStatus.SKIPPED_DUPLICATE.value,
                            error=f"EXACT duplicate: {len(exact_matches)} release(s) with same size"
                        )
                        self.db.commit()
                        continue  # Skip to next tracker - exact duplicate found

                    # Similar releases (same movie, different quality) - just warn but allow upload
                    if duplicate_result.get('is_duplicate'):
                        existing = duplicate_result.get('existing_torrents', [])
                        search_method = duplicate_result.get('search_method', 'unknown')
                        logger.info(
                            f"Similar releases found on {tracker.name} ({len(existing)}), "
                            f"but different quality/size - proceeding with upload"
                        )

                    logger.info(f"✓ No exact duplicate on {tracker.name} - proceeding")

                except NotImplementedError:
                    logger.debug(f"Duplicate check not implemented for {tracker.name}, proceeding")
                except Exception as e:
                    logger.warning(f"Duplicate check failed for {tracker.name}: {e}, proceeding anyway")

                # Inject first torrent into qBittorrent (do this once)
                if not qbittorrent_injected:
                    logger.info("Injecting torrent into qBittorrent for seeding...")
                    try:
                        await self._inject_to_qbittorrent(file_entry, torrent_path, tracker_slug=tracker.slug)
                        qbittorrent_injected = True
                        logger.info("✓ Torrent added to qBittorrent")
                    except Exception as e:
                        logger.warning(f"qBittorrent injection failed: {e}")

                # Upload to tracker
                logger.info(f"Uploading to {tracker.name}...")

                # Resolve category and subcategory using config-driven approach
                effective_category_id, subcategory_id = self._resolve_category_for_tracker(
                    tracker=tracker,
                    tmdb_type=tmdb_type,
                    resolution=resolution,
                    category_id=category_id,
                    genres=tmdb_genres
                )

                if not effective_category_id:
                    raise TrackerAPIError(
                        f"No category ID available for {tracker.name}. "
                        f"Set tracker.default_category_id or run analysis stage to map categories."
                    )

                upload_kwargs = {
                    'torrent_data': torrent_data,
                    'release_name': tracker_release_name,
                    'category_id': effective_category_id,
                    'tag_ids': tag_ids,
                    'nfo_data': nfo_data,
                    'description': description,
                    'tmdb_id': tmdb_id,
                    'tmdb_type': tmdb_type,
                    'cover_url': cover_url,
                }

                # Add subcategory if resolved
                if subcategory_id:
                    upload_kwargs['subcategory_id'] = subcategory_id
                    logger.info(f"categoryId={effective_category_id}, subcategoryId={subcategory_id} (tmdb_type={tmdb_type})")

                # Add tracker-specific data (options, tmdbData, BBCode description)
                # Build TMDB data via ConfigAdapter if adapter supports it
                tracker_tmdb_data = None
                if hasattr(adapter, 'build_tmdb_data') and tmdb_id:
                    tracker_tmdb_data = await adapter.build_tmdb_data(tmdb_id, tmdb_type or 'movie', self.db)
                if tracker_tmdb_data:
                    upload_kwargs['tmdb_data'] = tracker_tmdb_data
                    logger.info(f"TMDB data prepared for TMDB ID: {tmdb_id}")

                # Build options using config-driven mapper
                genres = tracker_tmdb_data.get('genres', []) if tracker_tmdb_data else []
                tracker_options = self._build_tracker_options(tracker, file_entry, tracker_release_name, tmdb_type, genres)
                if tracker_options:
                    upload_kwargs['options'] = tracker_options
                    logger.info(f"Tracker options: {tracker_options}")

                # Generate rich BBCode description using tracker-specific or global template
                bbcode_description = await self._generate_bbcode_description(
                    file_entry, tracker_tmdb_data, template_id=tracker.default_template_id
                )
                if bbcode_description:
                    upload_kwargs['description'] = bbcode_description
                    logger.info(f"BBCode description generated ({len(bbcode_description)} chars)")

                result = await adapter.upload_torrent(**upload_kwargs)

                if result.get('success'):
                    # Store upload result with SUCCESS status
                    file_entry.set_tracker_status(
                        tracker_slug=tracker.slug,
                        status=TrackerStatus.SUCCESS.value,
                        torrent_id=str(result['torrent_id']),
                        torrent_url=result['torrent_url']
                    )

                    # Also set legacy fields for first successful upload
                    if not file_entry.tracker_torrent_id:
                        file_entry.set_upload_result(
                            torrent_id=result['torrent_id'],
                            torrent_url=result['torrent_url']
                        )

                    logger.info(
                        f"✓ Successfully uploaded to {tracker.name}: "
                        f"{result['torrent_url']}"
                    )

                    # Record statistics for successful upload
                    stats_service = get_statistics_service(self.db)
                    stats_service.record_upload(
                        success=True,
                        tracker_name=tracker.slug,
                        bytes_processed=file_entry.file_size or 0
                    )
                else:
                    error_msg = result.get('message', 'Unknown error')
                    logger.error(f"✗ Upload to {tracker.name} failed: {error_msg}")
                    file_entry.set_tracker_status(
                        tracker_slug=tracker.slug,
                        status=TrackerStatus.FAILED.value,
                        error=error_msg
                    )

                    # Record statistics for failed upload
                    stats_service = get_statistics_service(self.db)
                    stats_service.record_upload(
                        success=False,
                        tracker_name=tracker.slug,
                        bytes_processed=file_entry.file_size or 0
                    )

                self.db.commit()

            except (TrackerAPIError, CloudflareBypassError, NetworkRetryableError) as e:
                error_msg = getattr(e, 'message', str(e))
                logger.error(f"✗ Upload to {tracker.name} failed: {e}")
                file_entry.set_tracker_status(
                    tracker_slug=tracker.slug,
                    status=TrackerStatus.FAILED.value,
                    error=error_msg
                )
                # Record statistics for failed upload
                stats_service = get_statistics_service(self.db)
                stats_service.record_upload(
                    success=False,
                    tracker_name=tracker.slug,
                    bytes_processed=file_entry.file_size or 0
                )
                self.db.commit()
                # Continue with other trackers
                continue

            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                logger.error(f"✗ Unexpected error uploading to {tracker.name}: {error_msg}")
                file_entry.set_tracker_status(
                    tracker_slug=tracker.slug,
                    status=TrackerStatus.FAILED.value,
                    error=error_msg
                )
                # Record statistics for failed upload
                stats_service = get_statistics_service(self.db)
                stats_service.record_upload(
                    success=False,
                    tracker_name=tracker.slug,
                    bytes_processed=file_entry.file_size or 0
                )
                self.db.commit()
                continue

        # Report results using granular statuses
        successful_trackers = file_entry.get_successful_trackers()
        failed_trackers = file_entry.get_failed_trackers()
        skipped_trackers = file_entry.get_skipped_trackers()
        tracker_statuses = file_entry.tracker_statuses or {}

        logger.info(f"\n{'='*50}")
        logger.info("Upload Summary")
        logger.info(f"{'='*50}")
        logger.info(f"Successful: {len(successful_trackers)} - {successful_trackers}")
        logger.info(f"Failed: {len(failed_trackers)} - {failed_trackers}")
        logger.info(f"Skipped (duplicates): {len(skipped_trackers)} - {skipped_trackers}")
        logger.debug(f"Full tracker_statuses: {tracker_statuses}")

        # Mark as uploaded if at least one succeeded
        if successful_trackers:
            file_entry.mark_uploaded()
            self.db.commit()
            logger.info(f"✓ Upload stage completed ({len(successful_trackers)} tracker(s))")
        elif skipped_trackers and not failed_trackers:
            # All trackers were skipped due to duplicates - this is not a failure
            logger.warning(
                f"All {len(skipped_trackers)} tracker(s) skipped due to existing duplicates. "
                f"No upload performed."
            )
            # Don't mark as uploaded, but don't raise error either
            # The file stays in METADATA_GENERATED state for manual review
        else:
            # All uploads failed - extract clean error messages
            failed_errors = [
                data.get('error', 'Erreur inconnue')
                for slug, data in tracker_statuses.items()
                if data.get('status') == TrackerStatus.FAILED.value
            ]
            if failed_errors:
                # Use first error directly (most cases = single tracker)
                error_msg = failed_errors[0] if len(failed_errors) == 1 else "; ".join(failed_errors)
            else:
                all_statuses = "; ".join([
                    f"{slug}: {data.get('status', 'none')}"
                    for slug, data in tracker_statuses.items()
                ])
                error_msg = f"Aucun tracker n'a pu compléter l'upload. Statuts: {all_statuses or 'vide'}"
            raise TrackerAPIError(error_msg)

    async def _upload_to_single_tracker(self, file_entry: FileEntry) -> None:
        """
        Legacy upload method for single tracker mode.

        Used when no trackers are configured but a tracker_adapter is available
        (backward compatibility with existing code).
        """
        logger.info("Using legacy single-tracker upload mode")

        # Read torrent file
        torrent_data = None
        if file_entry.torrent_path and os.path.exists(file_entry.torrent_path):
            with open(file_entry.torrent_path, 'rb') as f:
                torrent_data = f.read()

        # Read NFO file
        nfo_data = None
        if file_entry.nfo_path and os.path.exists(file_entry.nfo_path):
            with open(file_entry.nfo_path, 'rb') as f:
                nfo_data = f.read()

        if not torrent_data or not nfo_data:
            raise TrackerAPIError("Missing torrent or NFO file")

        # Authenticate
        await self.tracker_adapter.authenticate()

        # Inject to qBittorrent
        await self._inject_to_qbittorrent(file_entry)

        # Upload
        result = await self.tracker_adapter.upload_torrent(
            torrent_data=torrent_data,
            release_name=file_entry.release_name,
            category_id=file_entry.category_id,
            tag_ids=file_entry.get_tag_ids(),
            nfo_data=nfo_data,
            description=file_entry.description,
            tmdb_id=file_entry.tmdb_id,
            tmdb_type=file_entry.tmdb_type,
            cover_url=file_entry.cover_url
        )

        if result.get('success'):
            file_entry.set_upload_result(
                torrent_id=result['torrent_id'],
                torrent_url=result['torrent_url']
            )
            file_entry.mark_uploaded()
            self.db.commit()
        else:
            raise TrackerAPIError(f"Upload failed: {result.get('message')}")

    async def _inject_to_qbittorrent(
        self,
        file_entry: FileEntry,
        torrent_path: Optional[str] = None,
        tracker_slug: Optional[str] = None
    ) -> None:
        """
        Inject torrent into qBittorrent for seeding with category TP and tracker tag.

        This method adds the torrent file to qBittorrent with the original file path
        so it starts seeding immediately without re-downloading.

        Args:
            file_entry: FileEntry with torrent_path set
            torrent_path: Optional specific torrent path to use (for multi-tracker)
            tracker_slug: Optional tracker slug to add as tag (e.g., "c411", "lacale")

        Raises:
            TrackerAPIError: If qBittorrent injection fails
        """
        import httpx
        from app.models.settings import Settings

        try:
            # Get qBittorrent settings
            settings = Settings.get_settings(self.db)

            if not settings or not settings.qbittorrent_host:
                raise TrackerAPIError(
                    "Cannot inject torrent: qBittorrent not configured. "
                    "Please set qBittorrent settings."
                )

            qb_host = settings.qbittorrent_host
            qb_user = settings.qbittorrent_username
            qb_pass = settings.qbittorrent_password

            # Ensure qBittorrent host has protocol
            if not qb_host.startswith('http'):
                qb_host = f"http://{qb_host}"

            logger.info(f"Connecting to qBittorrent at: {qb_host}")

            # Use provided torrent path or fallback to file_entry
            torrent_path = torrent_path or file_entry.torrent_path
            if not torrent_path or not os.path.exists(torrent_path):
                raise TrackerAPIError(
                    f"Torrent file not found: {torrent_path}. "
                    f"Run metadata generation stage first."
                )

            # Get the original file directory for save path
            file_path = Path(file_entry.file_path)
            save_path = str(file_path.parent)

            # Path mapping: translate Seedarr's internal path to qBittorrent's path
            # Handles Docker-to-Docker (e.g., Seedarr /media -> qBit /data)
            # and Docker-to-host (e.g., Seedarr /media -> Windows C:\Media)
            seedarr_root = (settings.input_media_path or '/media').rstrip('/')
            qbit_root = (settings.qbittorrent_content_path or '').rstrip('/')

            if qbit_root and save_path.startswith(seedarr_root):
                relative_path = save_path[len(seedarr_root):].lstrip('/')
                # Use forward slashes (paths are for Docker/Linux containers)
                save_path = f"{qbit_root}/{relative_path}" if relative_path else qbit_root
                logger.info(f"Path mapping ({seedarr_root} -> {qbit_root}): {save_path}")

            async with httpx.AsyncClient(timeout=30.0) as client:
                # Login to qBittorrent
                logger.info("Authenticating with qBittorrent...")
                login_response = await client.post(
                    f"{qb_host}/api/v2/auth/login",
                    data={"username": qb_user, "password": qb_pass}
                )

                if login_response.text != "Ok.":
                    raise TrackerAPIError(
                        f"qBittorrent authentication failed: {login_response.text}"
                    )

                logger.info("✓ Authenticated with qBittorrent")
                cookies = login_response.cookies

                # Read torrent file
                with open(torrent_path, 'rb') as f:
                    torrent_file_data = f.read()

                logger.info(f"Adding torrent to qBittorrent with category TP and save path: {save_path}")

                # Build request data with optional tracker tag
                add_data = {
                    "savepath": save_path,  # Use original file location
                    "category": "TP",  # Category TP
                    "skip_checking": "true",  # Skip hash check (file already exists)
                    "paused": "false",  # Start seeding immediately
                    "autoTMM": "false"  # Disable automatic torrent management
                }

                # Add tracker tag if provided
                if tracker_slug:
                    add_data["tags"] = tracker_slug.upper()
                    logger.info(f"Adding torrent with tag: {tracker_slug.upper()}")

                # Add torrent to qBittorrent
                add_response = await client.post(
                    f"{qb_host}/api/v2/torrents/add",
                    cookies=cookies,
                    files={"torrents": ("torrent.torrent", torrent_file_data, "application/x-bittorrent")},
                    data=add_data
                )

                if add_response.text != "Ok.":
                    # Check if torrent already exists (qBittorrent returns "Fails." or contains "already")
                    response_lower = add_response.text.lower()
                    if "already" in response_lower or response_lower == "fails.":
                        logger.warning(f"Torrent likely already exists in qBittorrent: {add_response.text}")
                        # Try to add tag to existing torrent if tracker_slug is provided
                        if tracker_slug:
                            try:
                                # Get torrent hash from the .torrent file using torf
                                import torf
                                t = torf.Torrent.read(torrent_path)
                                torrent_hash = str(t.infohash).lower()

                                # Add tag to existing torrent
                                tag_response = await client.post(
                                    f"{qb_host}/api/v2/torrents/addTags",
                                    cookies=cookies,
                                    data={
                                        "hashes": torrent_hash,
                                        "tags": tracker_slug.upper()
                                    }
                                )
                                logger.info(f"✓ Added tag {tracker_slug.upper()} to existing torrent {torrent_hash[:8]}...")
                            except Exception as tag_error:
                                logger.warning(f"Could not add tag to existing torrent: {tag_error}")
                    else:
                        raise TrackerAPIError(
                            f"Failed to add torrent to qBittorrent: {add_response.text}"
                        )
                else:
                    tag_info = f" and tag {tracker_slug.upper()}" if tracker_slug else ""
                    logger.info(f"✓ Torrent successfully added to qBittorrent with category TP{tag_info}")

                # Verify torrent is seeding (optional)
                try:
                    torrents_response = await client.get(
                        f"{qb_host}/api/v2/torrents/info",
                        cookies=cookies,
                        params={"category": "TP"}
                    )
                    torrents = torrents_response.json()
                    logger.info(f"✓ Verified: {len(torrents)} torrent(s) in category TP")
                except Exception as e:
                    logger.warning(f"Could not verify torrent status: {e}")

        except httpx.HTTPError as e:
            error_msg = f"qBittorrent connection error: {e}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg) from e
        except Exception as e:
            error_msg = f"Failed to inject torrent into qBittorrent: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg) from e

    def reset_checkpoint(self, file_entry: FileEntry, from_stage: Status) -> None:
        """
        Reset file entry to retry from a specific checkpoint.

        This method allows manual reset of a file entry to retry from a specific
        stage, clearing all subsequent checkpoint timestamps.

        Args:
            file_entry: FileEntry to reset
            from_stage: Status to reset to (e.g., Status.ANALYZED to retry from rename)

        Example:
            >>> # Retry from rename stage (clears renamed_at, metadata_generated_at, uploaded_at)
            >>> pipeline.reset_checkpoint(entry, Status.ANALYZED)
            >>> await pipeline.process_file(entry)  # Will start from rename stage
        """
        logger.info(f"Resetting checkpoint for {file_entry.file_path} to stage: {from_stage.value}")
        file_entry.reset_from_checkpoint(from_stage)
        self.db.commit()
        logger.info(f"Checkpoint reset complete. File will resume from: {from_stage.value}")

    def get_pipeline_status(self, file_entry: FileEntry) -> dict:
        """
        Get current pipeline status and checkpoint information.

        Returns detailed status information including which stages are completed
        and checkpoint timestamps.

        Args:
            file_entry: FileEntry to get status for

        Returns:
            Dictionary with status information:
            {
                'file_path': str,
                'status': str,
                'error_message': str or None,
                'checkpoints': {
                    'scanned': bool,
                    'analyzed': bool,
                    'renamed': bool,
                    'metadata_generated': bool,
                    'uploaded': bool
                },
                'timestamps': {
                    'scanned_at': datetime or None,
                    'analyzed_at': datetime or None,
                    'renamed_at': datetime or None,
                    'metadata_generated_at': datetime or None,
                    'uploaded_at': datetime or None
                },
                'created_at': datetime,
                'updated_at': datetime
            }

        Example:
            >>> status = pipeline.get_pipeline_status(entry)
            >>> print(f"Current stage: {status['status']}")
            >>> if status['checkpoints']['metadata_generated']:
            >>>     print("Metadata files already generated, will reuse on retry")
        """
        return {
            'file_path': file_entry.file_path,
            'status': file_entry.status.value,
            'error_message': file_entry.error_message,
            'checkpoints': {
                'scanned': file_entry.is_scanned(),
                'analyzed': file_entry.is_analyzed(),
                'renamed': file_entry.is_renamed(),
                'metadata_generated': file_entry.is_metadata_generated(),
                'uploaded': file_entry.is_uploaded()
            },
            'timestamps': {
                'scanned_at': file_entry.scanned_at,
                'analyzed_at': file_entry.analyzed_at,
                'renamed_at': file_entry.renamed_at,
                'metadata_generated_at': file_entry.metadata_generated_at,
                'uploaded_at': file_entry.uploaded_at
            },
            'created_at': file_entry.created_at,
            'updated_at': file_entry.updated_at
        }

    def _resolve_category_for_tracker(
        self,
        tracker,
        tmdb_type: Optional[str],
        resolution: Optional[str],
        category_id: Optional[str],
        genres: Optional[list] = None
    ) -> tuple:
        """
        Resolve category and subcategory for a tracker.

        Uses config-driven approach when available, falls back to
        tracker model's category_mapping for legacy support.

        Args:
            tracker: Tracker model instance
            tmdb_type: "movie" or "tv"
            resolution: Resolution string (e.g., "1080p", "2160p")
            category_id: Default category from file_entry
            genres: List of TMDB genre dicts [{"id": 16, "name": "Animation"}, ...]

        Returns:
            Tuple of (category_id, subcategory_id)
        """
        effective_category_id = None
        subcategory_id = None

        # Detect content type from TMDB genres
        # Animation genre ID = 16, Documentary genre ID = 99
        is_animation = False
        is_documentary = False
        if genres:
            genre_ids = [g.get('id') for g in genres if isinstance(g, dict)]
            is_animation = 16 in genre_ids
            is_documentary = 99 in genre_ids

        # Try config-driven category mapping first
        try:
            config_loader = get_config_loader()
            tracker_config = config_loader.load_from_tracker(tracker)

            if tracker_config and tracker_config.get("categories"):
                categories = dict(tracker_config["categories"])  # Copy to avoid mutation

                # Merge synced category_mapping (from API sync) - these take precedence
                # This adds keys like anime_movie, anime_series from C411 sync
                if tracker.category_mapping:
                    categories.update(tracker.category_mapping)
                    logger.info(f"[DEBUG] Merged category_mapping keys: {list(tracker.category_mapping.keys())}")
                    if 'anime_movie' in tracker.category_mapping:
                        logger.info(f"[DEBUG] anime_movie = {tracker.category_mapping['anime_movie']}")
                    else:
                        logger.warning("[DEBUG] anime_movie NOT FOUND in category_mapping - need to re-sync C411 categories!")

                # Build lookup keys based on media type and resolution
                res_suffix = None
                if resolution:
                    res_lower = resolution.lower()
                    if '2160' in res_lower or '4k' in res_lower:
                        res_suffix = '4k'
                    elif '1080' in res_lower:
                        res_suffix = '1080p'
                    elif '720' in res_lower:
                        res_suffix = '720p'

                # Determine base media type
                base_media_type = tmdb_type or 'movie'

                # Log the category detection for debugging
                logger.info(
                    f"Category detection for {tracker.name}: "
                    f"is_animation={is_animation}, is_documentary={is_documentary}, "
                    f"tmdb_type={base_media_type}, resolution={resolution}, res_suffix={res_suffix}, "
                    f"available_keys={list(categories.keys())}"
                )

                # For Animation, use anime_movie/anime_series (from C411 sync) or anime_* (from YAML)
                if is_animation:
                    # Try C411-style keys first (from synced mapping)
                    if base_media_type == 'tv':
                        subcategory_id = categories.get('anime_series')
                    else:
                        subcategory_id = categories.get('anime_movie')

                    # Fallback to YAML-style keys (anime_1080p, anime, etc.)
                    if not subcategory_id and res_suffix:
                        subcategory_id = categories.get(f'anime_{res_suffix}')
                    if not subcategory_id:
                        subcategory_id = categories.get('anime')

                    if subcategory_id:
                        logger.info(f"Animation subcategory resolved: {subcategory_id}")

                    # Main category for animation
                    effective_category_id = categories.get('anime_category') or categories.get('movie_category')

                elif is_documentary:
                    if res_suffix:
                        subcategory_id = categories.get(f'documentary_{res_suffix}')
                    if not subcategory_id:
                        subcategory_id = categories.get('documentary')
                    effective_category_id = categories.get('documentary_category') or categories.get('movie_category')

                else:
                    # Regular movie/tv
                    if res_suffix:
                        key = f"{base_media_type}_{res_suffix}"
                        if key in categories:
                            subcategory_id = categories[key]
                            logger.info(f"Found category mapping: {key} -> {subcategory_id}")

                    if not subcategory_id:
                        subcategory_id = categories.get(base_media_type)
                        if subcategory_id:
                            logger.info(f"Using fallback category: {base_media_type} -> {subcategory_id}")

                    effective_category_id = categories.get(f"{base_media_type}_category")

        except Exception:
            pass  # Fall back to legacy approach

        # Legacy fallback: use tracker model's category_mapping
        if not effective_category_id and tracker.category_mapping:
            mapping = tracker.category_mapping

            # Determine media type - prioritize Animation/Documentary
            if is_animation:
                media_type = 'anime'
            elif is_documentary:
                media_type = 'documentary'
            else:
                media_type = tmdb_type or 'movie'

            # Resolve category from mapping (works for all trackers)
            if is_animation:
                effective_category_id = mapping.get('anime_category') or mapping.get('movie_category')
            elif tmdb_type == 'tv':
                effective_category_id = mapping.get('tv_category') or mapping.get('movie_category')
            else:
                effective_category_id = mapping.get('movie_category')

            # Resolve subcategory based on resolution
            res_suffix = None
            if resolution:
                res_lower = resolution.lower()
                if '2160' in res_lower or '4k' in res_lower:
                    res_suffix = '4k'
                elif '1080' in res_lower:
                    res_suffix = '1080p'
                elif '720' in res_lower:
                    res_suffix = '720p'

            if is_animation:
                if tmdb_type == 'tv':
                    subcategory_id = mapping.get('anime_series')
                else:
                    subcategory_id = mapping.get('anime_movie')
                logger.info(f"Animation detected for {tracker.name}, subcategory: {subcategory_id}")
            elif res_suffix:
                if tmdb_type == 'tv':
                    subcategory_id = mapping.get(f'series_{res_suffix}') or mapping.get(f'tv_{res_suffix}')
                else:
                    subcategory_id = mapping.get(f'movie_{res_suffix}')

            if not subcategory_id:
                if tmdb_type == 'tv':
                    subcategory_id = mapping.get('tv') or mapping.get('series')
                else:
                    subcategory_id = mapping.get('movie')

            # Fallback: use get_category_id if mapping didn't resolve
            if not effective_category_id:
                effective_category_id = tracker.get_category_id(
                    media_type=media_type,
                    resolution=resolution
                )

        # Final fallbacks
        if not effective_category_id:
            effective_category_id = tracker.default_category_id or category_id
            logger.warning(
                f"Category fallback for {tracker.name}: "
                f"default_category_id={tracker.default_category_id}, "
                f"file_entry_category_id={category_id} -> using {effective_category_id}"
            )

        if not subcategory_id:
            subcategory_id = getattr(tracker, 'default_subcategory_id', None)

        logger.info(f"Final category for {tracker.name}: category_id={effective_category_id}, subcategory_id={subcategory_id}")
        return (effective_category_id, subcategory_id)

    def _build_tracker_options(
        self,
        tracker,
        file_entry: FileEntry,
        release_name: str,
        tmdb_type: Optional[str],
        genres: Optional[list] = None
    ) -> dict:
        """
        Build tracker-specific options dict from file metadata.

        Uses config-driven OptionsMapper to build options from YAML config.

        Args:
            tracker: Tracker model instance
            file_entry: FileEntry with metadata
            release_name: Release name for detection
            tmdb_type: "movie" or "tv"
            genres: List of TMDB genre dicts [{"id": 28, "name": "Action"}, ...]

        Returns:
            Options dict for tracker API
        """
        try:
            # Load config-driven options mapper
            config_loader = get_config_loader()
            tracker_config = None

            try:
                tracker_config = config_loader.load_from_tracker(tracker)
            except Exception:
                pass

            if tracker_config and tracker_config.get("options"):
                # Use generic config-driven options mapper
                mapper = get_options_mapper(tracker_config.get("options", {}))

                return mapper.build_options_from_file_entry(
                    file_entry=file_entry,
                    release_name=release_name,
                    genres=genres
                )

            # No options mapping for this tracker
            return {}

        except Exception as e:
            logger.warning(f"Failed to build options for {tracker.name}: {e}")
            return {}

    async def _generate_bbcode_description(
        self,
        file_entry: FileEntry,
        tmdb_data: Optional[dict],
        template_id: Optional[int] = None
    ) -> Optional[str]:
        """
        Generate a rich BBCode description for uploads.

        Uses BBCodeGenerator with a tracker-specific template, global default template,
        or fallback to built-in generator to create a formatted description.

        Args:
            file_entry: FileEntry with metadata
            tmdb_data: TMDB data dict (from ConfigAdapter.build_tmdb_data)
            template_id: Optional specific template ID to use (from tracker settings)
                        If None, uses global default template

        Returns:
            BBCode formatted description string or None if generation fails
        """
        try:
            from ..services.bbcode_generator import get_bbcode_generator, TMDBData, CastMember
            from ..services.nfo_generator import get_nfo_generator
            from ..models.bbcode_template import BBCodeTemplate

            # Get BBCode generator
            bbcode_gen = get_bbcode_generator()
            nfo_gen = get_nfo_generator()

            # Extract MediaInfo from file
            file_path = file_entry.file_path
            if not file_path or not os.path.exists(file_path):
                logger.warning(f"File not found for BBCode generation: {file_path}")
                return None

            media_data = await nfo_gen.extract_mediainfo(file_path)

            # Convert tmdb_data dict to TMDBData dataclass
            tmdb_data_obj = None
            if tmdb_data:
                # Extract cast from tmdb_data
                cast_list = []
                credits = tmdb_data.get('credits', {})
                for actor in credits.get('cast', [])[:6]:
                    cast_list.append(CastMember(
                        name=actor.get('name', ''),
                        character=actor.get('character', ''),
                        profile_path=actor.get('profile_path', '') or actor.get('profilePath', '')
                    ))

                # Extract genres
                genres = [g.get('name', '') for g in tmdb_data.get('genres', [])]

                # Format release date in French
                formatted_release_date = ""
                raw_date = tmdb_data.get('releaseDate', '') or tmdb_data.get('firstAirDate', '')
                if raw_date and len(raw_date) >= 10:
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(raw_date[:10], "%Y-%m-%d")
                        fr_days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
                        fr_months = ["", "janvier", "février", "mars", "avril", "mai", "juin",
                                     "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
                        formatted_release_date = f"{fr_days[dt.weekday()]} {dt.day} {fr_months[dt.month]} {dt.year}"
                    except Exception:
                        formatted_release_date = raw_date

                # Extract country from production countries
                country = ""
                prod_countries = tmdb_data.get('productionCountries', [])
                if prod_countries:
                    country = ", ".join(c.get('name', '') for c in prod_countries if c.get('name'))

                tmdb_data_obj = TMDBData(
                    title=tmdb_data.get('title') or tmdb_data.get('name', ''),
                    original_title=tmdb_data.get('originalTitle') or tmdb_data.get('originalName', ''),
                    year=int(raw_date[:4]) if raw_date and len(raw_date) >= 4 else 0,
                    release_date=formatted_release_date,
                    country=country,
                    poster_url=tmdb_data.get('posterPath', ''),
                    backdrop_url=tmdb_data.get('backdropPath', ''),
                    vote_average=tmdb_data.get('voteAverage', 0),
                    genres=genres,
                    overview=tmdb_data.get('overview', ''),
                    runtime=tmdb_data.get('runtime', 0),
                    tmdb_id=str(tmdb_data.get('id', '')),
                    imdb_id=tmdb_data.get('imdbId', ''),
                    cast=cast_list
                )

            # Try to get template: tracker-specific first, then global default
            template = None

            if template_id:
                # Use tracker-specific template
                template = BBCodeTemplate.get_by_id(self.db, template_id)
                if template:
                    logger.debug(f"Using tracker-specific BBCode template: {template.name} (id={template_id})")
                else:
                    logger.warning(f"Tracker template not found (id={template_id}), falling back to global default")

            if not template:
                # Fall back to global default
                template = BBCodeTemplate.get_default(self.db)
                if template:
                    logger.debug(f"Using global default BBCode template: {template.name}")

            # Build extra variables from file_entry
            extra_vars = {}
            release_name = file_entry.get_effective_release_name() if hasattr(file_entry, 'get_effective_release_name') else (getattr(file_entry, 'release_name', '') or '')
            if release_name:
                extra_vars["release_name"] = release_name
                if "-" in release_name:
                    extra_vars["release_team"] = release_name.rsplit("-", 1)[-1]

            if template:
                # Use custom template
                bbcode = bbcode_gen.render_template(
                    template.content,
                    media_data,
                    tmdb_data_obj,
                    extra_variables=extra_vars if extra_vars else None
                )
            else:
                # Use built-in default generator
                logger.debug("Using built-in BBCode generator (no template configured)")
                bbcode = bbcode_gen.generate_bbcode(media_data, tmdb_data_obj)

            return bbcode

        except Exception as e:
            logger.warning(f"Failed to generate BBCode description: {e}")
            import traceback
            traceback.print_exc()
            return None


async def process_file_by_id(file_entry_id: int, skip_approval: bool = False) -> dict:
    """
    Process a file entry by ID.

    Convenience function for queue worker that creates its own database session.

    Args:
        file_entry_id: ID of the file entry to process
        skip_approval: Whether to skip approval step

    Returns:
        Dictionary with 'success' and optionally 'error' keys
    """
    from app.database import SessionLocal
    from app.models.settings import Settings

    db = SessionLocal()
    try:
        # Get file entry
        file_entry = db.query(FileEntry).filter(FileEntry.id == file_entry_id).first()
        if not file_entry:
            return {'success': False, 'error': f'File entry {file_entry_id} not found'}

        # Get settings
        settings = Settings.get_settings(db)

        # tracker_adapter is no longer needed here - the pipeline uses TrackerFactory
        tracker_adapter = None

        # Create pipeline and process
        pipeline = ProcessingPipeline(db, tracker_adapter=tracker_adapter)
        await pipeline.process_file(file_entry, skip_approval=skip_approval)

        # Check final status
        if file_entry.status == Status.UPLOADED:
            return {'success': True}
        elif file_entry.status == Status.FAILED:
            return {'success': False, 'error': file_entry.error_message or 'Unknown error'}
        elif file_entry.status == Status.PENDING_APPROVAL:
            return {'success': False, 'error': 'Awaiting approval'}
        else:
            return {'success': False, 'error': f'Unexpected status: {file_entry.status.value}'}

    except Exception as e:
        logger.error(f"Error processing file entry {file_entry_id}: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        db.close()
