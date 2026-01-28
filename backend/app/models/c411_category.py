"""
C411 Category Model for Seedarr v2.0

This module defines the C411Category model for storing C411 tracker
categories and subcategories fetched from the API.

Features:
    - Store category ID, name, and subcategories
    - Track sync timestamps for cache invalidation
    - Support category/subcategory lookups by name or ID
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Session

from .base import Base


class C411Category(Base):
    """
    C411 category storage model.

    Stores categories and subcategories from C411's API for use during uploads.
    Categories are synced automatically when testing tracker connection.

    Table Structure:
        - id: Primary key
        - tracker_id: Foreign key to trackers table
        - category_id: C411's category ID
        - name: Category name (e.g., "Films", "Séries TV")
        - subcategories: JSON array of subcategories
        - synced_at: Last sync timestamp

    Subcategory Structure (JSON):
        [
            {"id": 1, "name": "Subcategory 1"},
            {"id": 2, "name": "Subcategory 2"},
            ...
        ]
    """

    __tablename__ = 'c411_categories'

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracker_id = Column(Integer, ForeignKey('trackers.id'), nullable=False)
    category_id = Column(String(50), nullable=False)
    name = Column(String(200), nullable=False)
    subcategories = Column(JSON, nullable=True)  # List of subcategory dicts
    synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __init__(
        self,
        tracker_id: int,
        category_id: str,
        name: str,
        subcategories: Optional[List[Dict[str, Any]]] = None
    ):
        self.tracker_id = tracker_id
        self.category_id = str(category_id)
        self.name = name
        self.subcategories = subcategories or []
        self.synced_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'tracker_id': self.tracker_id,
            'category_id': self.category_id,
            'name': self.name,
            'subcategories': self.subcategories or [],
            'synced_at': self.synced_at.isoformat() if self.synced_at else None
        }

    @classmethod
    def get_all_for_tracker(cls, db: Session, tracker_id: int) -> List['C411Category']:
        """Get all categories for a tracker."""
        return db.query(cls).filter(cls.tracker_id == tracker_id).order_by(cls.name).all()

    @classmethod
    def get_by_category_id(cls, db: Session, tracker_id: int, category_id: str) -> Optional['C411Category']:
        """Get category by C411 category ID."""
        return db.query(cls).filter(
            cls.tracker_id == tracker_id,
            cls.category_id == str(category_id)
        ).first()

    @classmethod
    def get_by_name(cls, db: Session, tracker_id: int, name: str) -> Optional['C411Category']:
        """Get category by name (case-insensitive)."""
        return db.query(cls).filter(
            cls.tracker_id == tracker_id,
            cls.name.ilike(name)
        ).first()

    @classmethod
    def upsert(
        cls,
        db: Session,
        tracker_id: int,
        category_id: str,
        name: str,
        subcategories: Optional[List[Dict[str, Any]]] = None
    ) -> 'C411Category':
        """Insert or update a category."""
        existing = cls.get_by_category_id(db, tracker_id, category_id)

        if existing:
            existing.name = name
            existing.subcategories = subcategories or []
            existing.synced_at = datetime.utcnow()
        else:
            existing = cls(
                tracker_id=tracker_id,
                category_id=category_id,
                name=name,
                subcategories=subcategories
            )
            db.add(existing)

        db.commit()
        db.refresh(existing)
        return existing

    @classmethod
    def sync_from_api(
        cls,
        db: Session,
        tracker_id: int,
        api_categories: List[Dict[str, Any]]
    ) -> int:
        """
        Sync categories from C411 API response.

        Args:
            db: Database session
            tracker_id: Tracker ID
            api_categories: List of category dicts from C411 API

        Returns:
            Number of categories synced
        """
        count = 0
        for cat in api_categories:
            # Extract category info from API response
            # Structure may vary - adapt based on actual C411 response
            cat_id = cat.get('id') or cat.get('category_id')
            cat_name = cat.get('name') or cat.get('label')
            subcats = cat.get('subcategories') or cat.get('children') or []

            if cat_id and cat_name:
                cls.upsert(
                    db=db,
                    tracker_id=tracker_id,
                    category_id=str(cat_id),
                    name=cat_name,
                    subcategories=subcats
                )
                count += 1

        return count

    @classmethod
    def delete_all_for_tracker(cls, db: Session, tracker_id: int) -> int:
        """Delete all categories for a tracker."""
        count = db.query(cls).filter(cls.tracker_id == tracker_id).delete()
        db.commit()
        return count

    def get_subcategory_id(self, name: str) -> Optional[str]:
        """
        Get subcategory ID by name.

        Args:
            name: Subcategory name to look up

        Returns:
            Subcategory ID if found, None otherwise
        """
        if not self.subcategories:
            return None

        name_lower = name.lower()
        for sub in self.subcategories:
            sub_name = sub.get('name', '').lower()
            if sub_name == name_lower or name_lower in sub_name:
                return str(sub.get('id'))

        return None

    def __repr__(self) -> str:
        return f"<C411Category(id={self.id}, category_id='{self.category_id}', name='{self.name}')>"
