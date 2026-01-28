"""
TMDBCache Database Model for Seedarr v2.0

This module defines the TMDBCache model for persistent storage of TMDB API responses,
reducing API calls and improving performance through cache-first lookup strategy.

Features:
    - Persistent caching of TMDB metadata (survives application restart)
    - Configurable TTL (default 30 days)
    - Automatic expiration on query
    - Indexed by tmdb_id for fast lookups
    - Stores comprehensive metadata: title, year, cast, plot, ratings

Cache Strategy:
    1. Check cache for tmdb_id
    2. If cached and not expired, return cached data
    3. If not cached or expired, fetch from TMDB API
    4. Store/update cache with new data and reset TTL

Expected Cache Performance:
    - Cache hit rate: >90% for repeated lookups
    - Reduction in TMDB API calls: >80%
    - Cache survival: Persists across application restarts
"""

from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, Text, DateTime, Index, JSON
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any

from .base import Base


class TMDBCache(Base):
    """
    Database model for caching TMDB API metadata responses.

    This model stores TMDB API responses with a time-to-live (TTL) mechanism
    to reduce API calls and improve application performance. The cache is
    persistent and survives application restarts.

    Table Structure:
        - id: Primary key (auto-increment)
        - tmdb_id: TMDB movie/TV show ID (indexed for fast lookup)
        - title: Movie/TV show title
        - year: Release/first air year
        - cast: JSON array of cast members
        - plot: Plot summary/overview
        - ratings: JSON object with rating information (vote_average, vote_count, etc.)
        - cached_at: Timestamp when data was cached
        - expires_at: Timestamp when cache entry expires (cached_at + TTL)

    Indexes:
        - tmdb_id: Fast lookup by TMDB ID
        - expires_at: Efficient expiration queries

    Default TTL: 30 days (2,592,000 seconds)
    """

    __tablename__ = 'tmdb_cache'

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # TMDB identifier (indexed for fast lookup)
    tmdb_id = Column(String(50), nullable=False, unique=True, index=True)

    # Basic metadata
    title = Column(String(500), nullable=False)
    year = Column(Integer, nullable=True)

    # Extended metadata (stored as JSON for flexibility)
    cast = Column(JSON, nullable=True, default=list)
    plot = Column(Text, nullable=True)
    ratings = Column(JSON, nullable=True, default=dict)

    # Cache management timestamps
    cached_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)

    # Additional metadata (extensible for future TMDB data)
    # Note: 'metadata' is reserved by SQLAlchemy, so we use 'extra_data'
    extra_data = Column(JSON, nullable=True, default=dict)

    def __init__(
        self,
        tmdb_id: str,
        title: str,
        year: Optional[int] = None,
        cast: Optional[list] = None,
        plot: Optional[str] = None,
        ratings: Optional[dict] = None,
        extra_data: Optional[dict] = None,
        ttl_days: int = 30
    ):
        """
        Initialize TMDBCache entry with metadata and TTL.

        Args:
            tmdb_id: TMDB movie/TV show ID
            title: Movie/TV show title
            year: Release/first air year
            cast: List of cast members (will be stored as JSON)
            plot: Plot summary/overview
            ratings: Rating information dict (vote_average, vote_count, etc.)
            extra_data: Additional extensible metadata
            ttl_days: Time-to-live in days (default: 30)
        """
        self.tmdb_id = str(tmdb_id)
        self.title = title
        self.year = year
        self.cast = cast if cast is not None else []
        self.plot = plot
        self.ratings = ratings if ratings is not None else {}
        self.extra_data = extra_data if extra_data is not None else {}
        self.cached_at = datetime.utcnow()
        self.expires_at = self.cached_at + timedelta(days=ttl_days)

    def is_expired(self) -> bool:
        """
        Check if cache entry has expired.

        Returns:
            True if cache entry is expired, False otherwise
        """
        return datetime.utcnow() >= self.expires_at

    def refresh_ttl(self, ttl_days: int = 30) -> None:
        """
        Refresh the cache entry TTL (extend expiration).

        Args:
            ttl_days: New time-to-live in days (default: 30)
        """
        self.cached_at = datetime.utcnow()
        self.expires_at = self.cached_at + timedelta(days=ttl_days)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert cache entry to dictionary.

        Returns:
            Dictionary representation of cache entry
        """
        return {
            'tmdb_id': self.tmdb_id,
            'title': self.title,
            'year': self.year,
            'cast': self.cast,
            'plot': self.plot,
            'ratings': self.ratings,
            'extra_data': self.extra_data,
            'cached_at': self.cached_at.isoformat() if self.cached_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'is_expired': self.is_expired()
        }

    @classmethod
    def get_cached(cls, db: Session, tmdb_id: str) -> Optional['TMDBCache']:
        """
        Get cached entry by TMDB ID if not expired.

        Args:
            db: SQLAlchemy database session
            tmdb_id: TMDB movie/TV show ID

        Returns:
            TMDBCache entry if found and not expired, None otherwise
        """
        cache_entry = db.query(cls).filter(cls.tmdb_id == str(tmdb_id)).first()

        if cache_entry is None:
            return None

        # Check expiration
        if cache_entry.is_expired():
            # Delete expired entry
            db.delete(cache_entry)
            db.commit()
            return None

        return cache_entry

    @classmethod
    def upsert(
        cls,
        db: Session,
        tmdb_id: str,
        title: str,
        year: Optional[int] = None,
        cast: Optional[list] = None,
        plot: Optional[str] = None,
        ratings: Optional[dict] = None,
        extra_data: Optional[dict] = None,
        ttl_days: int = 30
    ) -> 'TMDBCache':
        """
        Insert or update cache entry (upsert operation).

        If entry exists, updates it with new data and refreshes TTL.
        If entry doesn't exist, creates new entry.

        Args:
            db: SQLAlchemy database session
            tmdb_id: TMDB movie/TV show ID
            title: Movie/TV show title
            year: Release/first air year
            cast: List of cast members
            plot: Plot summary/overview
            ratings: Rating information dict
            extra_data: Additional extensible metadata
            ttl_days: Time-to-live in days (default: 30)

        Returns:
            TMDBCache entry (new or updated)
        """
        # Try to find existing entry
        cache_entry = db.query(cls).filter(cls.tmdb_id == str(tmdb_id)).first()

        if cache_entry:
            # Update existing entry
            cache_entry.title = title
            cache_entry.year = year
            cache_entry.cast = cast if cast is not None else []
            cache_entry.plot = plot
            cache_entry.ratings = ratings if ratings is not None else {}
            cache_entry.extra_data = extra_data if extra_data is not None else {}
            cache_entry.refresh_ttl(ttl_days)
        else:
            # Create new entry
            cache_entry = cls(
                tmdb_id=tmdb_id,
                title=title,
                year=year,
                cast=cast,
                plot=plot,
                ratings=ratings,
                extra_data=extra_data,
                ttl_days=ttl_days
            )
            db.add(cache_entry)

        db.commit()
        db.refresh(cache_entry)
        return cache_entry

    @classmethod
    def cleanup_expired(cls, db: Session) -> int:
        """
        Delete all expired cache entries.

        This can be called periodically (e.g., daily background task)
        to clean up stale cache entries.

        Args:
            db: SQLAlchemy database session

        Returns:
            Number of expired entries deleted
        """
        expired_count = db.query(cls).filter(
            cls.expires_at <= datetime.utcnow()
        ).delete()
        db.commit()
        return expired_count

    def __repr__(self) -> str:
        """String representation of cache entry."""
        expired_status = "EXPIRED" if self.is_expired() else "VALID"
        return (
            f"<TMDBCache(tmdb_id='{self.tmdb_id}', title='{self.title}', "
            f"year={self.year}, status={expired_status})>"
        )


# Create indexes for performance
Index('idx_tmdb_cache_tmdb_id', TMDBCache.tmdb_id)
Index('idx_tmdb_cache_expires_at', TMDBCache.expires_at)
