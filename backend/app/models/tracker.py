"""
Tracker Database Model for Seedarr v2.0

This module defines the Tracker model for multi-tracker support.
Each tracker represents a private tracker configuration with its own
authentication, piece size strategy, and upload settings.

Features:
    - Support for multiple trackers (La Cale, C411, etc.)
    - Per-tracker piece size strategies
    - Flexible authentication (passkey, API key)
    - Configurable announce URL templates
    - Adapter type selection for tracker-specific logic
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, JSON
from sqlalchemy.orm import Session
from typing import Optional, List, Any

from .base import Base


class Tracker(Base):
    """
    Database model for tracker configuration.

    Each tracker entry represents a private tracker with its own
    authentication credentials, piece size strategy, and upload configuration.

    Table Structure:
        Identity:
            - id: Primary key
            - name: Human-readable tracker name (e.g., "La Cale", "C411")
            - slug: URL-safe identifier (e.g., "lacale", "c411")
            - tracker_url: Base URL of the tracker

        Authentication:
            - passkey: Passkey for announce URL (most trackers)
            - api_key: API key for Bearer auth (C411)

        Torrent Configuration:
            - source_flag: Source flag for torrent (makes hash unique per tracker)
            - piece_size_strategy: How to calculate piece size ("auto", "c411", "standard")
            - announce_url_template: Template for announce URL

        Upload Configuration:
            - adapter_type: Which adapter to use ("lacale", "c411", "generic")
            - default_category_id: Default category for uploads
            - default_subcategory_id: Default subcategory (C411)

        Options:
            - requires_cloudflare: Whether FlareSolverr is needed
            - upload_enabled: Whether uploads are enabled for this tracker
            - priority: Upload priority (lower = first)
            - enabled: Whether this tracker is active

    Piece Size Strategies:
        - "auto": Automatic based on file size (default)
        - "c411": C411-specific piece sizes
        - "standard": Standard piece sizes (16MB max)
    """

    __tablename__ = 'trackers'

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Identity
    name = Column(String(100), unique=True, nullable=False)  # "La Cale", "C411"
    slug = Column(String(50), unique=True, nullable=False)   # "lacale", "c411"
    tracker_url = Column(String(500), nullable=False)        # Base URL

    # Authentication (varies by tracker)
    passkey = Column(String(500), nullable=True)             # For announce URL
    api_key = Column(String(500), nullable=True)             # For API Bearer auth (C411)

    # Torrent configuration
    source_flag = Column(String(50), nullable=True)          # "lacale", "C411" (hash unique)
    piece_size_strategy = Column(String(20), default="auto") # "auto", "c411", "standard"
    announce_url_template = Column(String(500), nullable=True)  # Ex: "{url}/announce?passkey={passkey}"

    # Upload configuration
    adapter_type = Column(String(50), default="generic")     # "lacale", "c411", "generic"
    default_category_id = Column(String(50), nullable=True)  # Default category
    default_subcategory_id = Column(String(50), nullable=True)  # Subcategory (C411)

    # Category mapping (v2.1) - Maps media type + resolution to tracker category IDs
    # Structure: {"movie_4k": "42", "movie_1080p": "1", "movie_720p": "2", "series_hd": "5", "series_sd": "6"}
    category_mapping = Column(JSON, nullable=True)

    # Upload configuration (v2.1) - JSON config for configurable uploader
    # Allows uploads without custom Python adapters - just JSON config
    # Structure: see UPLOAD_CONFIG_TEMPLATES in configurable_uploader.py
    upload_config = Column(JSON, nullable=True)

    # BBCode template (v2.1) - Default template for this tracker's upload descriptions
    # References bbcode_templates.id - None means use global default
    default_template_id = Column(Integer, nullable=True)

    # Naming template (v2.2) - Custom release name template for this tracker
    # Format: "{titre}.{annee}.{langue}.{resolution}.{source}.{codec_audio}.{codec_video}-{group}"
    # Variables: {titre}, {titre_fr}, {titre_en}, {annee}, {langue}, {resolution}, {source},
    #            {codec_audio}, {codec_video}, {group}, {hdr}, {saison}, {episode}
    # If None, the original file name (without extension) is used as-is
    naming_template = Column(String(500), nullable=True)

    # Hardlink & torrent management (v2.5)
    hardlink_dir = Column(String(1000), nullable=True)       # Per-tracker hardlink output directory
    torrent_dir = Column(String(1000), nullable=True)        # Per-tracker torrent output directory
    inject_to_qbit = Column(Boolean, default=True)           # Auto-inject torrent to qBittorrent

    # Options
    requires_cloudflare = Column(Boolean, default=False)
    upload_enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=0)                    # Lower = first
    enabled = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, **kwargs):
        """Initialize Tracker entry."""
        super().__init__(**kwargs)

    @property
    def announce_url(self) -> Optional[str]:
        """
        Compute the tracker announce URL from template or default pattern.

        The announce URL is built using the template if provided,
        otherwise falls back to the standard pattern: {url}/announce?passkey={passkey}

        Returns:
            Computed announce URL, or None if required fields are missing
        """
        if not self.tracker_url or not self.passkey:
            return None

        base_url = self.tracker_url.rstrip('/')

        if self.announce_url_template:
            return self.announce_url_template.format(
                url=base_url,
                passkey=self.passkey
            )

        # Default: La Cale style
        return f"{base_url}/announce?passkey={self.passkey}"

    def get_category_id(self, media_type: str, resolution: Optional[str] = None) -> Optional[str]:
        """
        Get the tracker category ID for a given media type and resolution.

        Uses the category_mapping JSON to resolve the appropriate category ID
        based on media type (movie, series) and resolution (4k, 1080p, 720p, sd).

        Args:
            media_type: Type of media ("movie" or "series"/"tv")
            resolution: Resolution string (e.g., "2160p", "1080p", "720p")

        Returns:
            Category ID string if found in mapping, else default_category_id, else None

        Mapping key format:
            - "movie_4k", "movie_1080p", "movie_720p", "movie_sd"
            - "series_4k", "series_1080p", "series_720p", "series_sd"
            - Fallbacks: "movie", "series" (without resolution)

        Example:
            >>> tracker.category_mapping = {"movie_4k": "42", "movie_1080p": "1", "series_hd": "5"}
            >>> tracker.get_category_id("movie", "2160p")  # Returns "42"
            >>> tracker.get_category_id("movie", "1080p")  # Returns "1"
        """
        if not self.category_mapping:
            return self.default_category_id

        # Normalize media type
        media_type = media_type.lower()
        if media_type in ('tv', 'series', 'show'):
            media_type = 'series'
        elif media_type in ('film', 'movie'):
            media_type = 'movie'

        # Normalize resolution to category suffix
        res_suffix = None
        if resolution:
            resolution = resolution.lower().replace('p', '')
            if resolution in ('2160', '4k', 'uhd'):
                res_suffix = '4k'
            elif resolution in ('1080', 'fhd'):
                res_suffix = '1080p'
            elif resolution in ('720', 'hd'):
                res_suffix = '720p'
            else:
                res_suffix = 'sd'

        # Try specific resolution key first
        if res_suffix:
            key = f"{media_type}_{res_suffix}"
            if key in self.category_mapping:
                return str(self.category_mapping[key])

            # Try HD grouping (1080p and 720p together)
            if res_suffix in ('1080p', '720p'):
                hd_key = f"{media_type}_hd"
                if hd_key in self.category_mapping:
                    return str(self.category_mapping[hd_key])

        # Fallback to media type only
        if media_type in self.category_mapping:
            return str(self.category_mapping[media_type])

        # Final fallback to default category
        return self.default_category_id

    def get_category_mapping(self) -> dict:
        """Get category mapping dictionary."""
        return self.category_mapping if self.category_mapping else {}

    def set_category_mapping(self, mapping: dict) -> None:
        """
        Set category mapping.

        Args:
            mapping: Dict mapping category keys to tracker category IDs
                     e.g., {"movie_4k": "42", "movie_1080p": "1", "series_hd": "5"}
        """
        self.category_mapping = mapping
        self.updated_at = datetime.utcnow()

    def to_dict(self, mask_secrets: bool = True) -> dict:
        """
        Convert tracker to dictionary.

        Args:
            mask_secrets: If True, mask sensitive values with asterisks

        Returns:
            Dictionary representation of tracker
        """
        def mask_value(value: Optional[str]) -> Optional[str]:
            """Mask sensitive value."""
            if not value or not mask_secrets:
                return value
            if len(value) > 4:
                return value[:4] + '*' * (len(value) - 4)
            return '****'

        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'tracker_url': self.tracker_url,
            'passkey': mask_value(self.passkey),
            'api_key': mask_value(self.api_key),
            'announce_url': self.announce_url,
            'source_flag': self.source_flag,
            'piece_size_strategy': self.piece_size_strategy,
            'announce_url_template': self.announce_url_template,
            'adapter_type': self.adapter_type,
            'default_category_id': self.default_category_id,
            'default_subcategory_id': self.default_subcategory_id,
            'category_mapping': self.get_category_mapping(),
            'upload_config': self.upload_config,
            'default_template_id': self.default_template_id,
            'naming_template': self.naming_template,
            'hardlink_dir': self.hardlink_dir,
            'torrent_dir': self.torrent_dir,
            'inject_to_qbit': bool(self.inject_to_qbit) if self.inject_to_qbit is not None else True,
            'requires_cloudflare': self.requires_cloudflare,
            'upload_enabled': self.upload_enabled,
            'priority': self.priority,
            'enabled': self.enabled,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def get_all(cls, db: Session) -> List['Tracker']:
        """
        Get all trackers.

        Args:
            db: SQLAlchemy database session

        Returns:
            List of all Tracker instances
        """
        return db.query(cls).order_by(cls.priority, cls.name).all()

    @classmethod
    def get_enabled(cls, db: Session) -> List['Tracker']:
        """
        Get all enabled trackers ordered by priority.

        Args:
            db: SQLAlchemy database session

        Returns:
            List of enabled Tracker instances
        """
        return db.query(cls).filter(
            cls.enabled == True
        ).order_by(cls.priority, cls.name).all()

    @classmethod
    def get_upload_enabled(cls, db: Session) -> List['Tracker']:
        """
        Get all trackers with upload enabled, ordered by priority.

        Args:
            db: SQLAlchemy database session

        Returns:
            List of Tracker instances with upload enabled
        """
        return db.query(cls).filter(
            cls.enabled == True,
            cls.upload_enabled == True
        ).order_by(cls.priority, cls.name).all()

    @classmethod
    def get_by_id(cls, db: Session, tracker_id: int) -> Optional['Tracker']:
        """
        Get tracker by ID.

        Args:
            db: SQLAlchemy database session
            tracker_id: Tracker ID

        Returns:
            Tracker if found, None otherwise
        """
        return db.query(cls).filter(cls.id == tracker_id).first()

    @classmethod
    def get_by_slug(cls, db: Session, slug: str) -> Optional['Tracker']:
        """
        Get tracker by slug.

        Args:
            db: SQLAlchemy database session
            slug: Tracker slug (e.g., "lacale", "c411")

        Returns:
            Tracker if found, None otherwise
        """
        return db.query(cls).filter(cls.slug == slug).first()

    @classmethod
    def create(cls, db: Session, **kwargs) -> 'Tracker':
        """
        Create a new tracker.

        Args:
            db: SQLAlchemy database session
            **kwargs: Tracker attributes

        Returns:
            Created Tracker instance
        """
        tracker = cls(**kwargs)
        db.add(tracker)
        db.commit()
        db.refresh(tracker)
        return tracker

    @classmethod
    def update(cls, db: Session, tracker_id: int, **kwargs) -> Optional['Tracker']:
        """
        Update an existing tracker.

        Args:
            db: SQLAlchemy database session
            tracker_id: Tracker ID to update
            **kwargs: Fields to update

        Returns:
            Updated Tracker if found, None otherwise
        """
        tracker = cls.get_by_id(db, tracker_id)
        if not tracker:
            return None

        for key, value in kwargs.items():
            if hasattr(tracker, key):
                setattr(tracker, key, value)

        tracker.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(tracker)
        return tracker

    @classmethod
    def delete(cls, db: Session, tracker_id: int) -> bool:
        """
        Delete a tracker.

        Args:
            db: SQLAlchemy database session
            tracker_id: Tracker ID to delete

        Returns:
            True if deleted, False if not found
        """
        tracker = cls.get_by_id(db, tracker_id)
        if not tracker:
            return False

        db.delete(tracker)
        db.commit()
        return True

    def __repr__(self) -> str:
        """String representation of tracker."""
        return (
            f"<Tracker(id={self.id}, name='{self.name}', "
            f"slug='{self.slug}', enabled={self.enabled})>"
        )
