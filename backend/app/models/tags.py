"""
Tags Database Model for Seedarr v2.0

This module defines the Tags model for dynamic storage of tracker tag IDs and labels.
Tags are fetched from the tracker API at application startup and cached in the database
to eliminate hardcoded tag IDs and support automatic synchronization with tracker changes.

Features:
    - Dynamic tag storage (loaded from tracker API)
    - Automatic sync at application startup
    - Label-to-ID mapping for user-friendly references
    - Timestamp tracking for staleness detection
    - Graceful degradation with cached values if fetch fails

Tag Management Strategy:
    1. Fetch current tag list from tracker API at startup
    2. Store/update tags in database
    3. Application references tags by label (e.g., "Film", "Serie")
    4. Dynamic lookup resolves label to current tag_id
    5. Daily background task refreshes tag list
    6. Log warnings if configured tags no longer exist

Expected Behavior:
    - No hardcoded tag IDs in application code
    - Automatic adaptation to tracker tag changes
    - Graceful degradation if tracker API unavailable (uses cached values)
    - Admin visibility into current tag mappings
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Index
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from .base import Base


class Tags(Base):
    """
    Database model for storing tracker tag IDs and labels.

    This model caches tag information fetched from the tracker API,
    enabling dynamic tag ID resolution without hardcoded values.
    Tags are refreshed at application startup and periodically to
    stay synchronized with tracker changes.

    Table Structure:
        - id: Primary key (auto-increment)
        - tag_id: Tracker's tag ID (unique, indexed)
        - label: Human-readable tag name/label (e.g., "Film", "Serie")
        - category: Optional tag category for grouping
        - description: Optional tag description
        - updated_at: Timestamp of last update from tracker
        - created_at: Timestamp when tag first added to database

    Indexes:
        - tag_id: Fast lookup by tracker tag ID
        - label: Fast lookup by tag label
        - updated_at: Efficient staleness queries

    Usage Example:
        # Fetch tag ID by label
        film_tag = Tags.get_by_label(db, "Film")
        tag_id = film_tag.tag_id if film_tag else None

        # Bulk upsert from tracker API response
        Tags.bulk_upsert(db, [
            {"tag_id": "1", "label": "Film", "category": "Type"},
            {"tag_id": "2", "label": "Serie", "category": "Type"}
        ])
    """

    __tablename__ = 'tags'

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Tracker tag identifier (indexed for fast lookup)
    tag_id = Column(String(50), nullable=False, unique=True, index=True)

    # Human-readable label
    label = Column(String(200), nullable=False, index=True)

    # Optional categorization (e.g., "Type", "Quality", "Source")
    category = Column(String(100), nullable=True)

    # Optional description from tracker
    description = Column(String(500), nullable=True)

    # Timestamp tracking
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __init__(
        self,
        tag_id: str,
        label: str,
        category: Optional[str] = None,
        description: Optional[str] = None
    ):
        """
        Initialize Tags entry.

        Args:
            tag_id: Tracker's tag ID
            label: Human-readable tag name/label
            category: Optional tag category for grouping
            description: Optional tag description
        """
        self.tag_id = str(tag_id)
        self.label = label
        self.category = category
        self.description = description
        self.updated_at = datetime.utcnow()
        self.created_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert tag entry to dictionary.

        Returns:
            Dictionary representation of tag entry
        """
        return {
            'id': self.id,
            'tag_id': self.tag_id,
            'label': self.label,
            'category': self.category,
            'description': self.description,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    @classmethod
    def get_by_tag_id(cls, db: Session, tag_id: str) -> Optional['Tags']:
        """
        Get tag entry by tracker tag ID.

        Args:
            db: SQLAlchemy database session
            tag_id: Tracker's tag ID

        Returns:
            Tags entry if found, None otherwise
        """
        return db.query(cls).filter(cls.tag_id == str(tag_id)).first()

    @classmethod
    def get_by_label(cls, db: Session, label: str) -> Optional['Tags']:
        """
        Get tag entry by label (case-insensitive).

        Args:
            db: SQLAlchemy database session
            label: Tag label to search for

        Returns:
            Tags entry if found, None otherwise
        """
        return db.query(cls).filter(cls.label.ilike(label)).first()

    @classmethod
    def get_all(cls, db: Session) -> List['Tags']:
        """
        Get all tags from database.

        Args:
            db: SQLAlchemy database session

        Returns:
            List of all Tags entries
        """
        return db.query(cls).order_by(cls.category, cls.label).all()

    @classmethod
    def get_by_category(cls, db: Session, category: str) -> List['Tags']:
        """
        Get all tags in a specific category.

        Args:
            db: SQLAlchemy database session
            category: Tag category to filter by

        Returns:
            List of Tags entries in the specified category
        """
        return db.query(cls).filter(cls.category == category).order_by(cls.label).all()

    @classmethod
    def upsert(
        cls,
        db: Session,
        tag_id: str,
        label: str,
        category: Optional[str] = None,
        description: Optional[str] = None
    ) -> 'Tags':
        """
        Insert or update tag entry (upsert operation).

        If entry with tag_id exists, updates it with new data.
        If entry doesn't exist, creates new entry.

        Args:
            db: SQLAlchemy database session
            tag_id: Tracker's tag ID
            label: Human-readable tag name/label
            category: Optional tag category
            description: Optional tag description

        Returns:
            Tags entry (new or updated)
        """
        # Try to find existing entry by tag_id
        tag_entry = db.query(cls).filter(cls.tag_id == str(tag_id)).first()

        if tag_entry:
            # Update existing entry
            tag_entry.label = label
            tag_entry.category = category
            tag_entry.description = description
            tag_entry.updated_at = datetime.utcnow()
        else:
            # Create new entry
            tag_entry = cls(
                tag_id=tag_id,
                label=label,
                category=category,
                description=description
            )
            db.add(tag_entry)

        db.commit()
        db.refresh(tag_entry)
        return tag_entry

    @classmethod
    def bulk_upsert(cls, db: Session, tags_data: List[Dict[str, Any]]) -> int:
        """
        Bulk insert or update multiple tags.

        Useful for syncing entire tag list from tracker API.

        Args:
            db: SQLAlchemy database session
            tags_data: List of dicts with tag information
                      Each dict should have: tag_id, label, and optionally category, description

        Returns:
            Number of tags upserted

        Example:
            Tags.bulk_upsert(db, [
                {"tag_id": "1", "label": "Film", "category": "Type"},
                {"tag_id": "2", "label": "Serie", "category": "Type"},
                {"tag_id": "10", "label": "BluRay", "category": "Source"}
            ])
        """
        count = 0
        for tag_data in tags_data:
            cls.upsert(
                db=db,
                tag_id=tag_data['tag_id'],
                label=tag_data['label'],
                category=tag_data.get('category'),
                description=tag_data.get('description')
            )
            count += 1

        return count

    @classmethod
    def delete_stale_tags(cls, db: Session, current_tag_ids: List[str]) -> int:
        """
        Delete tags that are no longer present in tracker API.

        Compares current tag IDs with provided list and removes obsolete entries.

        Args:
            db: SQLAlchemy database session
            current_tag_ids: List of currently valid tag IDs from tracker

        Returns:
            Number of stale tags deleted
        """
        if not current_tag_ids:
            return 0

        # Delete tags not in current_tag_ids list
        deleted_count = db.query(cls).filter(
            cls.tag_id.notin_(current_tag_ids)
        ).delete(synchronize_session=False)

        db.commit()
        return deleted_count

    def __repr__(self) -> str:
        """String representation of tag entry."""
        category_str = f", category='{self.category}'" if self.category else ""
        return (
            f"<Tags(tag_id='{self.tag_id}', label='{self.label}'{category_str})>"
        )


# Create indexes for performance
Index('idx_tags_tag_id', Tags.tag_id)
Index('idx_tags_label', Tags.label)
Index('idx_tags_updated_at', Tags.updated_at)
