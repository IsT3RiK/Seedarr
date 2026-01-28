"""
Categories Database Model for Seedarr v2.0

This module defines the Categories model for dynamic storage of tracker category IDs and names.
Categories are fetched from the tracker API and cached in the database to eliminate hardcoded
category IDs and support automatic synchronization with tracker changes.

Features:
    - Dynamic category storage (loaded from tracker API)
    - Automatic sync at application startup
    - Name-to-ID mapping for user-friendly references
    - Slug-based lookup for URL-friendly access
    - Timestamp tracking for staleness detection

Category Management Strategy:
    1. Fetch current category list from tracker API at startup
    2. Store/update categories in database
    3. Application references categories by name (e.g., "Films", "Séries TV")
    4. Dynamic lookup resolves name to current category_id
    5. Periodic refresh to stay synchronized with tracker
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Index
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from .base import Base


class Categories(Base):
    """
    Database model for storing tracker category IDs and names.

    This model caches category information fetched from the tracker API,
    enabling dynamic category ID resolution without hardcoded values.

    Table Structure:
        - id: Primary key (auto-increment)
        - category_id: Tracker's category ID (unique, indexed)
        - name: Human-readable category name (e.g., "Films", "Séries TV")
        - slug: URL-friendly identifier
        - description: Optional category description
        - updated_at: Timestamp of last update from tracker
        - created_at: Timestamp when category first added to database

    Common La Cale Categories:
        - Films (Movies)
        - Séries TV (TV Shows)
        - Musique (Music)
        - Livres (Books)
        - Jeux (Games)
    """

    __tablename__ = 'categories'

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Tracker category identifier (indexed for fast lookup)
    category_id = Column(String(50), nullable=False, unique=True, index=True)

    # Human-readable name
    name = Column(String(200), nullable=False, index=True)

    # URL-friendly slug
    slug = Column(String(200), nullable=True, index=True)

    # Optional description
    description = Column(String(500), nullable=True)

    # Timestamp tracking
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __init__(
        self,
        category_id: str,
        name: str,
        slug: Optional[str] = None,
        description: Optional[str] = None
    ):
        """
        Initialize Categories entry.

        Args:
            category_id: Tracker's category ID
            name: Human-readable category name
            slug: URL-friendly identifier
            description: Optional category description
        """
        self.category_id = str(category_id)
        self.name = name
        self.slug = slug
        self.description = description
        self.updated_at = datetime.utcnow()
        self.created_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert category entry to dictionary."""
        return {
            'id': self.id,
            'category_id': self.category_id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    @classmethod
    def get_by_category_id(cls, db: Session, category_id: str) -> Optional['Categories']:
        """Get category entry by tracker category ID."""
        return db.query(cls).filter(cls.category_id == str(category_id)).first()

    @classmethod
    def get_by_name(cls, db: Session, name: str) -> Optional['Categories']:
        """Get category entry by name (case-insensitive)."""
        return db.query(cls).filter(cls.name.ilike(name)).first()

    @classmethod
    def get_by_slug(cls, db: Session, slug: str) -> Optional['Categories']:
        """Get category entry by slug."""
        return db.query(cls).filter(cls.slug == slug).first()

    @classmethod
    def get_all(cls, db: Session) -> List['Categories']:
        """Get all categories from database."""
        return db.query(cls).order_by(cls.name).all()

    @classmethod
    def upsert(
        cls,
        db: Session,
        category_id: str,
        name: str,
        slug: Optional[str] = None,
        description: Optional[str] = None
    ) -> 'Categories':
        """Insert or update category entry (upsert operation)."""
        category_entry = db.query(cls).filter(cls.category_id == str(category_id)).first()

        if category_entry:
            # Update existing entry
            category_entry.name = name
            category_entry.slug = slug
            category_entry.description = description
            category_entry.updated_at = datetime.utcnow()
        else:
            # Create new entry
            category_entry = cls(
                category_id=category_id,
                name=name,
                slug=slug,
                description=description
            )
            db.add(category_entry)

        db.commit()
        db.refresh(category_entry)
        return category_entry

    @classmethod
    def bulk_upsert(cls, db: Session, categories_data: List[Dict[str, Any]]) -> int:
        """Bulk insert or update multiple categories."""
        count = 0
        for cat_data in categories_data:
            cls.upsert(
                db=db,
                category_id=cat_data['category_id'],
                name=cat_data['name'],
                slug=cat_data.get('slug'),
                description=cat_data.get('description')
            )
            count += 1
        return count

    @classmethod
    def get_category_id_for_type(cls, db: Session, content_type: str) -> Optional[str]:
        """
        Get category ID for content type (movie/tv).

        Args:
            db: SQLAlchemy session
            content_type: "movie" or "tv"

        Returns:
            Category ID if found
        """
        # Common name mappings (La Cale uses "Vidéo" for both movies and TV)
        type_to_names = {
            'movie': ['Vidéo', 'Video', 'Films', 'Film', 'Movies', 'Movie'],
            'tv': ['Vidéo', 'Video', 'Séries TV', 'Series TV', 'TV Shows', 'TV Series', 'Séries', 'Series'],
        }

        names_to_try = type_to_names.get(content_type.lower(), [])
        for name in names_to_try:
            category = cls.get_by_name(db, name)
            if category:
                return category.category_id

        return None

    def __repr__(self) -> str:
        """String representation of category entry."""
        return f"<Categories(category_id='{self.category_id}', name='{self.name}')>"


# Create indexes for performance
Index('idx_categories_category_id', Categories.category_id)
Index('idx_categories_name', Categories.name)
Index('idx_categories_slug', Categories.slug)
