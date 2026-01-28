"""
NFO Validator Service for Seedarr v2.0

This module implements mandatory NFO file validation and generation enforcement,
acting as a pipeline blocker if NFO files are missing, invalid, or cannot be generated.

NFO Validation Requirements:
    - NFO file must exist (either pre-existing or generated)
    - NFO must contain required fields: title, year, plot
    - NFO content must be non-empty and properly formatted
    - If NFO missing or invalid, attempt generation from TMDB cache
    - If generation fails, pipeline MUST abort with descriptive error

Pipeline Integration:
    This validator is called during the metadata generation stage (Stage 4) and
    acts as a mandatory gate before proceeding to upload (Stage 5). No uploads
    can occur without a valid NFO file.

Usage Example:
    >>> from app.services.nfo_validator import NFOValidator
    >>> from app.database import SessionLocal
    >>>
    >>> db = SessionLocal()
    >>> validator = NFOValidator(db)
    >>>
    >>> # Validate existing NFO file
    >>> nfo_path = "/path/to/Movie.2023.1080p.BluRay.x264.nfo"
    >>> is_valid, error = validator.validate_nfo_file(nfo_path)
    >>>
    >>> # Generate NFO from TMDB cache if invalid
    >>> if not is_valid:
    >>>     nfo_content = validator.generate_nfo_from_tmdb(
    >>>         tmdb_id="12345",
    >>>         release_name="Movie.2023.1080p.BluRay.x264"
    >>>     )
"""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from ..models.tmdb_cache import TMDBCache
from .exceptions import TrackerAPIError

logger = logging.getLogger(__name__)


class NFOValidator:
    """
    NFO file validator and generator service.

    This class enforces mandatory NFO validation as a pipeline blocker,
    ensuring that no uploads occur without a valid NFO file. It validates
    existing NFO files and generates new ones from TMDB cache when needed.

    Required NFO Fields:
        - title: Movie/TV show title
        - year: Release/first air year
        - plot: Plot summary/overview

    Architecture:
        1. Check if NFO file exists
        2. If exists, validate required fields
        3. If missing/invalid, attempt generation from TMDB cache
        4. If generation fails, raise TrackerAPIError to block pipeline

    Example:
        >>> validator = NFOValidator(db)
        >>> nfo_path = validator.ensure_valid_nfo(
        ...     file_path="/path/to/Movie.mkv",
        ...     tmdb_id="12345",
        ...     release_name="Movie.2023.1080p.BluRay.x264"
        ... )
        >>> # nfo_path is guaranteed to be valid or exception raised
    """

    # Required fields that MUST be present in NFO
    REQUIRED_FIELDS = ['title', 'year', 'plot']

    def __init__(self, db: Session):
        """
        Initialize NFOValidator.

        Args:
            db: SQLAlchemy database session for TMDB cache access
        """
        self.db = db

    def validate_nfo_file(self, nfo_path: str) -> Tuple[bool, Optional[str]]:
        """
        Validate existing NFO file for required fields.

        This method checks if an NFO file exists and contains all required fields
        (title, year, plot). NFO files are expected to be text files with key-value
        pairs or structured content.

        Args:
            nfo_path: Path to NFO file to validate

        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if NFO is valid, False otherwise
            - error_message: Description of validation error if invalid, None if valid

        Example:
            >>> is_valid, error = validator.validate_nfo_file("/path/to/movie.nfo")
            >>> if not is_valid:
            >>>     logger.error(f"NFO validation failed: {error}")
        """
        # Check file exists
        if not os.path.exists(nfo_path):
            return False, f"NFO file does not exist: {nfo_path}"

        # Check file is readable
        if not os.path.isfile(nfo_path):
            return False, f"NFO path is not a file: {nfo_path}"

        try:
            # Read NFO content
            with open(nfo_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read().strip()

            # Check content is not empty
            if not content:
                return False, "NFO file is empty"

            # Check minimum content length (basic sanity check)
            if len(content) < 50:
                return False, f"NFO content too short ({len(content)} chars, minimum 50)"

            # Validate required fields are present in content
            # Note: NFO files are typically freeform text, so we do basic keyword checks
            content_lower = content.lower()

            missing_fields = []
            if 'title' not in content_lower and 'name' not in content_lower:
                missing_fields.append('title')

            if 'year' not in content_lower and not any(str(y) in content for y in range(1900, 2100)):
                missing_fields.append('year')

            if 'plot' not in content_lower and 'overview' not in content_lower and 'description' not in content_lower:
                # Also accept if there's substantial text (>200 chars likely has plot)
                if len(content) < 200:
                    missing_fields.append('plot')

            if missing_fields:
                return False, f"NFO missing required fields: {', '.join(missing_fields)}"

            logger.info(f"✓ NFO validation passed: {nfo_path}")
            return True, None

        except Exception as e:
            error_msg = f"Error reading NFO file {nfo_path}: {type(e).__name__}: {e}"
            logger.error(error_msg)
            return False, error_msg

    def generate_nfo_from_tmdb(
        self,
        tmdb_id: str,
        release_name: str,
        output_path: Optional[str] = None
    ) -> Optional[str]:
        """
        Generate NFO file from TMDB cache data.

        This method retrieves metadata from the TMDB cache and generates a
        formatted NFO file. If TMDB data is not cached, this will fail and
        return None, requiring the caller to handle the failure.

        Args:
            tmdb_id: TMDB movie/TV show ID
            release_name: Release name for the file (used in NFO header)
            output_path: Optional path to write NFO file (if None, returns content only)

        Returns:
            NFO file content as string if successful, None if generation fails

        Raises:
            TrackerAPIError: If TMDB cache data is missing or incomplete

        Example:
            >>> nfo_content = validator.generate_nfo_from_tmdb(
            ...     tmdb_id="12345",
            ...     release_name="Movie.2023.1080p.BluRay.x264",
            ...     output_path="/path/to/Movie.nfo"
            ... )
        """
        logger.info(f"Attempting to generate NFO from TMDB cache for tmdb_id={tmdb_id}")

        # Retrieve from TMDB cache
        cache_entry = TMDBCache.get_cached(self.db, tmdb_id)

        if cache_entry is None:
            error_msg = f"Cannot generate NFO: TMDB data not cached for tmdb_id={tmdb_id}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg)

        # Validate cache entry has required fields
        if not cache_entry.title:
            error_msg = f"Cannot generate NFO: TMDB cache missing title for tmdb_id={tmdb_id}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg)

        if not cache_entry.plot:
            error_msg = f"Cannot generate NFO: TMDB cache missing plot for tmdb_id={tmdb_id}"
            logger.error(error_msg)
            raise TrackerAPIError(error_msg)

        # Generate NFO content
        nfo_lines = []
        nfo_lines.append("=" * 80)
        nfo_lines.append(f"  {release_name}")
        nfo_lines.append("=" * 80)
        nfo_lines.append("")
        nfo_lines.append(f"Title: {cache_entry.title}")

        if cache_entry.year:
            nfo_lines.append(f"Year: {cache_entry.year}")

        nfo_lines.append("")
        nfo_lines.append("Plot:")
        nfo_lines.append("-" * 80)
        nfo_lines.append(cache_entry.plot)
        nfo_lines.append("-" * 80)
        nfo_lines.append("")

        # Add cast if available
        if cache_entry.cast and len(cache_entry.cast) > 0:
            nfo_lines.append("Cast:")
            for actor in cache_entry.cast[:10]:  # Limit to top 10
                if isinstance(actor, dict):
                    actor_name = actor.get('name', str(actor))
                else:
                    actor_name = str(actor)
                nfo_lines.append(f"  - {actor_name}")
            nfo_lines.append("")

        # Add ratings if available
        if cache_entry.ratings and isinstance(cache_entry.ratings, dict):
            vote_average = cache_entry.ratings.get('vote_average')
            vote_count = cache_entry.ratings.get('vote_count')
            if vote_average:
                nfo_lines.append(f"Rating: {vote_average}/10")
                if vote_count:
                    nfo_lines.append(f"Votes: {vote_count}")
                nfo_lines.append("")

        nfo_lines.append("=" * 80)
        nfo_lines.append("Generated from TMDB metadata")
        nfo_lines.append("=" * 80)

        nfo_content = "\n".join(nfo_lines)

        # Write to file if output path provided
        if output_path:
            try:
                # Ensure parent directory exists
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)

                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(nfo_content)

                logger.info(f"✓ NFO file generated and saved to: {output_path}")
            except Exception as e:
                error_msg = f"Failed to write NFO file to {output_path}: {type(e).__name__}: {e}"
                logger.error(error_msg)
                raise TrackerAPIError(error_msg) from e

        return nfo_content

    def ensure_valid_nfo(
        self,
        file_path: str,
        tmdb_id: Optional[str] = None,
        release_name: Optional[str] = None
    ) -> str:
        """
        Ensure a valid NFO file exists, generating if necessary.

        This is the main entry point for NFO validation in the pipeline.
        It enforces the mandatory NFO requirement:
        1. Check if NFO exists alongside media file
        2. If exists, validate it has required fields
        3. If missing/invalid, attempt generation from TMDB cache
        4. If generation fails, raise TrackerAPIError to block pipeline

        Args:
            file_path: Path to media file (NFO path derived from this)
            tmdb_id: TMDB ID for NFO generation (required if NFO needs generation)
            release_name: Release name for NFO header (defaults to file basename)

        Returns:
            Path to valid NFO file

        Raises:
            TrackerAPIError: If NFO is invalid and cannot be generated

        Example:
            >>> nfo_path = validator.ensure_valid_nfo(
            ...     file_path="/media/Movie.2023.1080p.mkv",
            ...     tmdb_id="12345",
            ...     release_name="Movie.2023.1080p.BluRay.x264"
            ... )
        """
        # Derive NFO path from media file path
        file_path_obj = Path(file_path)
        nfo_path = file_path_obj.with_suffix('.nfo')

        logger.info(f"Validating NFO for: {file_path}")
        logger.info(f"Expected NFO path: {nfo_path}")

        # Validate existing NFO if present
        is_valid, error = self.validate_nfo_file(str(nfo_path))

        if is_valid:
            logger.info(f"✓ Existing NFO is valid: {nfo_path}")
            return str(nfo_path)

        # NFO missing or invalid
        logger.warning(f"⚠ NFO validation failed: {error}")

        # Attempt generation from TMDB cache
        if not tmdb_id:
            error_msg = (
                f"NFO validation failed and cannot generate: TMDB ID not provided. "
                f"Error: {error}"
            )
            logger.error(error_msg)
            raise TrackerAPIError(error_msg)

        logger.info(f"Attempting to generate NFO from TMDB cache (tmdb_id={tmdb_id})")

        # Use release_name or derive from filename
        if not release_name:
            release_name = file_path_obj.stem  # Filename without extension

        try:
            # Generate NFO and write to file
            nfo_content = self.generate_nfo_from_tmdb(
                tmdb_id=tmdb_id,
                release_name=release_name,
                output_path=str(nfo_path)
            )

            # Verify generated NFO
            is_valid, error = self.validate_nfo_file(str(nfo_path))

            if not is_valid:
                error_msg = f"Generated NFO failed validation: {error}"
                logger.error(error_msg)
                raise TrackerAPIError(error_msg)

            logger.info(f"✓ NFO successfully generated and validated: {nfo_path}")
            return str(nfo_path)

        except TrackerAPIError:
            # Re-raise TrackerAPIError as-is
            raise
        except Exception as e:
            error_msg = f"NFO generation failed: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise TrackerAPIError(error_msg) from e

    def read_nfo_content(self, nfo_path: str) -> Optional[str]:
        """
        Read NFO file content.

        Args:
            nfo_path: Path to NFO file

        Returns:
            NFO file content as string, or None if file cannot be read

        Example:
            >>> content = validator.read_nfo_content("/path/to/movie.nfo")
            >>> if content:
            >>>     # Use NFO content for upload
            >>>     pass
        """
        try:
            with open(nfo_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read NFO file {nfo_path}: {e}")
            return None
