"""
Duplicate Check Service

Centralized service for checking duplicate releases across trackers.

Features:
- Multi-tracker duplicate checking
- Cascade search strategy (TMDB -> IMDB -> Release name)
- Result caching to avoid repeated API calls
- Aggregated results across all enabled trackers
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

from sqlalchemy.orm import Session

from app.models.tracker import Tracker
from app.models.file_entry import FileEntry
from app.adapters.tracker_factory import TrackerFactory

logger = logging.getLogger(__name__)


@dataclass
class DuplicateResult:
    """Result of a duplicate check for a single tracker."""
    tracker_id: int
    tracker_name: str
    is_duplicate: bool
    search_method: str
    existing_torrents: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""
    error: Optional[str] = None
    checked_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = asdict(self)
        if result.get('checked_at'):
            result['checked_at'] = result['checked_at'].isoformat()
        return result


@dataclass
class AggregatedDuplicateResult:
    """Aggregated result across all trackers."""
    has_duplicates: bool
    total_duplicates_found: int
    trackers_checked: int
    trackers_with_duplicates: int
    results_by_tracker: Dict[str, DuplicateResult] = field(default_factory=dict)
    checked_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'has_duplicates': self.has_duplicates,
            'total_duplicates_found': self.total_duplicates_found,
            'trackers_checked': self.trackers_checked,
            'trackers_with_duplicates': self.trackers_with_duplicates,
            'results_by_tracker': {
                k: v.to_dict() for k, v in self.results_by_tracker.items()
            },
            'checked_at': self.checked_at.isoformat() if self.checked_at else None
        }


class DuplicateCheckService:
    """
    Service for checking duplicate releases across trackers.

    Provides:
    - Single tracker duplicate checks
    - Multi-tracker aggregate checks
    - Result caching
    - File entry result persistence
    """

    def __init__(self, db: Session, cache_ttl_minutes: int = 60):
        """
        Initialize duplicate check service.

        Args:
            db: Database session
            cache_ttl_minutes: How long to cache results
        """
        self.db = db
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._cache: Dict[str, DuplicateResult] = {}
        self._cache_times: Dict[str, datetime] = {}

    def _cache_key(
        self,
        tracker_id: int,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        release_name: Optional[str] = None
    ) -> str:
        """Generate cache key."""
        return f"{tracker_id}:{tmdb_id or ''}:{imdb_id or ''}:{release_name or ''}"

    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cached result is still valid."""
        if cache_key not in self._cache_times:
            return False
        return datetime.utcnow() - self._cache_times[cache_key] < self.cache_ttl

    def _update_cache(self, cache_key: str, result: DuplicateResult) -> None:
        """Update cache with result."""
        self._cache[cache_key] = result
        self._cache_times[cache_key] = datetime.utcnow()

    async def check_single_tracker(
        self,
        tracker_id: int,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        release_name: Optional[str] = None,
        quality: Optional[str] = None,
        use_cache: bool = True
    ) -> DuplicateResult:
        """
        Check for duplicates on a single tracker.

        Args:
            tracker_id: Tracker ID to check
            tmdb_id: TMDB ID to search for
            imdb_id: IMDB ID to search for
            release_name: Release name to search for
            quality: Quality filter (optional)
            use_cache: Whether to use cached results

        Returns:
            DuplicateResult for the tracker
        """
        # Check cache
        cache_key = self._cache_key(tracker_id, tmdb_id, imdb_id, release_name)
        if use_cache and self._is_cache_valid(cache_key):
            logger.debug(f"Using cached duplicate check result for tracker {tracker_id}")
            return self._cache[cache_key]

        # Get tracker
        tracker = self.db.query(Tracker).filter(Tracker.id == tracker_id).first()
        if not tracker:
            return DuplicateResult(
                tracker_id=tracker_id,
                tracker_name="Unknown",
                is_duplicate=False,
                search_method="none",
                error="Tracker not found",
                checked_at=datetime.utcnow()
            )

        # Get adapter
        try:
            adapter = await TrackerFactory.create_adapter(tracker, self.db)
        except Exception as e:
            logger.error(f"Failed to create adapter for tracker {tracker.name}: {e}")
            return DuplicateResult(
                tracker_id=tracker_id,
                tracker_name=tracker.name,
                is_duplicate=False,
                search_method="none",
                error=f"Failed to create adapter: {str(e)}",
                checked_at=datetime.utcnow()
            )

        # Perform duplicate check
        try:
            result = await adapter.check_duplicate(
                tmdb_id=tmdb_id,
                imdb_id=imdb_id,
                release_name=release_name,
                quality=quality
            )

            duplicate_result = DuplicateResult(
                tracker_id=tracker_id,
                tracker_name=tracker.name,
                is_duplicate=result.get('is_duplicate', False),
                search_method=result.get('search_method', 'none'),
                existing_torrents=result.get('existing_torrents', []),
                message=result.get('message', ''),
                checked_at=datetime.utcnow()
            )

            # Cache result
            self._update_cache(cache_key, duplicate_result)

            return duplicate_result

        except Exception as e:
            logger.error(f"Duplicate check failed for tracker {tracker.name}: {e}")
            return DuplicateResult(
                tracker_id=tracker_id,
                tracker_name=tracker.name,
                is_duplicate=False,
                search_method="none",
                error=str(e),
                checked_at=datetime.utcnow()
            )

    async def check_all_trackers(
        self,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        release_name: Optional[str] = None,
        quality: Optional[str] = None,
        tracker_ids: Optional[List[int]] = None,
        use_cache: bool = True
    ) -> AggregatedDuplicateResult:
        """
        Check for duplicates across all (or specified) trackers.

        Args:
            tmdb_id: TMDB ID to search for
            imdb_id: IMDB ID to search for
            release_name: Release name to search for
            quality: Quality filter
            tracker_ids: Specific trackers to check (None = all enabled)
            use_cache: Whether to use cached results

        Returns:
            AggregatedDuplicateResult with results from all trackers
        """
        # Get trackers to check
        if tracker_ids:
            trackers = (
                self.db.query(Tracker)
                .filter(Tracker.id.in_(tracker_ids))
                .filter(Tracker.enabled == True)
                .all()
            )
        else:
            trackers = Tracker.get_enabled(self.db)

        if not trackers:
            return AggregatedDuplicateResult(
                has_duplicates=False,
                total_duplicates_found=0,
                trackers_checked=0,
                trackers_with_duplicates=0,
                checked_at=datetime.utcnow()
            )

        # Check each tracker
        results_by_tracker = {}
        total_duplicates = 0
        trackers_with_dupes = 0

        for tracker in trackers:
            result = await self.check_single_tracker(
                tracker_id=tracker.id,
                tmdb_id=tmdb_id,
                imdb_id=imdb_id,
                release_name=release_name,
                quality=quality,
                use_cache=use_cache
            )
            results_by_tracker[tracker.name] = result

            if result.is_duplicate:
                trackers_with_dupes += 1
                total_duplicates += len(result.existing_torrents)

        return AggregatedDuplicateResult(
            has_duplicates=trackers_with_dupes > 0,
            total_duplicates_found=total_duplicates,
            trackers_checked=len(trackers),
            trackers_with_duplicates=trackers_with_dupes,
            results_by_tracker=results_by_tracker,
            checked_at=datetime.utcnow()
        )

    async def check_file_entry(
        self,
        file_entry: FileEntry,
        tracker_ids: Optional[List[int]] = None,
        use_cache: bool = True,
        persist_results: bool = True
    ) -> AggregatedDuplicateResult:
        """
        Check for duplicates for a file entry.

        Args:
            file_entry: FileEntry to check
            tracker_ids: Specific trackers to check
            use_cache: Whether to use cached results
            persist_results: Whether to save results to file entry

        Returns:
            AggregatedDuplicateResult
        """
        result = await self.check_all_trackers(
            tmdb_id=str(file_entry.tmdb_id) if file_entry.tmdb_id else None,
            imdb_id=file_entry.imdb_id if hasattr(file_entry, 'imdb_id') else None,
            release_name=file_entry.release_name,
            tracker_ids=tracker_ids,
            use_cache=use_cache
        )

        # Persist results to file entry
        if persist_results:
            file_entry.duplicate_check_results = result.to_dict()
            self.db.commit()

        return result

    def get_cached_result(
        self,
        tracker_id: int,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        release_name: Optional[str] = None
    ) -> Optional[DuplicateResult]:
        """Get cached result if available and valid."""
        cache_key = self._cache_key(tracker_id, tmdb_id, imdb_id, release_name)
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key]
        return None

    def clear_cache(self) -> int:
        """Clear all cached results. Returns count of cleared entries."""
        count = len(self._cache)
        self._cache.clear()
        self._cache_times.clear()
        return count


def get_duplicate_check_service(db: Session) -> DuplicateCheckService:
    """Get a duplicate check service instance."""
    return DuplicateCheckService(db)
