"""
Settings Database Model for Seedarr v2.0

This module defines the Settings model for database-driven runtime configuration,
migrating away from the hybrid YAML/DB approach to 100% database-driven settings
(except DATABASE_URL which remains in environment variables).

Features:
    - All runtime configuration stored in database
    - Settings editable via Settings UI
    - Settings persist across application restarts
    - Secure storage of sensitive data (passkey, passwords)
    - Single-row singleton pattern for application settings

Configuration Strategy:
    - All tracker settings (URL, passkey) stored in database
    - External service URLs (FlareSolverr, qBittorrent, TMDB) in database
    - Directory paths (input_media_path, output_dir) in database
    - Only DATABASE_URL remains in environment/YAML

Migration from YAML:
    The migrate_config_to_db.py script handles migration from existing config.yaml
    files to the database Settings model. After migration, config.yaml is no longer
    required for runtime settings.

Note: This uses a singleton pattern - only one row exists in the settings table.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.orm import Session
from typing import Optional

from .base import Base


class Settings(Base):
    """
    Database model for application runtime configuration settings.

    This model replaces the previous hybrid YAML/DB configuration system with
    a 100% database-driven approach. All settings are editable via the Settings
    UI and persist across application restarts.

    Singleton Pattern:
        This table should contain exactly one row with all application settings.
        The get_settings() class method ensures the singleton pattern is maintained.

    Table Structure:
        Tracker Settings:
            - tracker_url: La Cale tracker base URL
            - tracker_passkey: User's personal passkey for tracker authentication (sensitive)
            - announce_url: (computed property) Tracker announce URL built from tracker_url + passkey

        External Services:
            - flaresolverr_url: FlareSolverr service URL for Cloudflare bypass
            - qbittorrent_host: qBittorrent Web UI host:port
            - qbittorrent_username: qBittorrent Web UI username
            - qbittorrent_password: qBittorrent Web UI password (sensitive)
            - tmdb_api_key: TMDB API key for metadata fetching (sensitive)

        Directory Paths:
            - input_media_path: Directory where media files are scanned
            - output_dir: Directory where processed releases are saved (if empty, uses source file folder)

        Application Settings:
            - log_level: Application logging level (DEBUG, INFO, WARNING, ERROR)
            - tmdb_cache_ttl_days: TMDB cache TTL in days (default: 30)
            - tag_sync_interval_hours: Tag sync interval in hours (default: 24)

    Security Note:
        Sensitive fields (passkey, passwords, API keys) should be encrypted at rest.
        Current implementation stores them as plain text but this should be
        enhanced with encryption before production deployment.
    """

    __tablename__ = 'settings'

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Tracker Settings
    tracker_url = Column(String(500), nullable=True)
    tracker_passkey = Column(String(500), nullable=True)  # Should be encrypted
    # Note: tracker_announce_url is now computed from tracker_url + passkey (see announce_url property)

    # External Services
    flaresolverr_url = Column(String(500), nullable=True)
    qbittorrent_host = Column(String(500), nullable=True)
    qbittorrent_username = Column(String(255), nullable=True)
    qbittorrent_password = Column(String(500), nullable=True)  # Should be encrypted
    qbittorrent_content_path = Column(String(1000), nullable=True)  # Base path as seen by qBittorrent (e.g., /data)
    tmdb_api_key = Column(String(500), nullable=True)  # Should be encrypted

    # Directory Paths
    input_media_path = Column(String(1000), nullable=True)
    output_dir = Column(String(1000), nullable=True)
    torrent_output_dir = Column(String(1000), nullable=True)  # Dedicated folder for .torrent files (default: {output_dir}/.torrents)

    # Hardlink settings (v2.5)
    hardlink_enabled = Column(Boolean, nullable=True, default=True)  # Enable hardlink creation for release structures
    hardlink_fallback_copy = Column(Boolean, nullable=True, default=True)  # Fallback to copy if hardlink fails (cross-partition)

    # Application Settings
    log_level = Column(String(50), nullable=True, default='INFO')
    tmdb_cache_ttl_days = Column(Integer, nullable=True, default=30)
    tag_sync_interval_hours = Column(Integer, nullable=True, default=24)

    # Image hosting (v2.1)
    imgbb_api_key = Column(String(200), nullable=True)  # ImgBB API key for screenshot uploads

    # Approval workflow settings (v2.1)
    auto_resume_after_approval = Column(Boolean, nullable=True, default=True)  # Auto-resume pipeline after user approval

    # Prowlarr integration (v2.1)
    prowlarr_url = Column(String(500), nullable=True)  # Prowlarr instance URL (e.g., http://localhost:9696)
    prowlarr_api_key = Column(String(200), nullable=True)  # Prowlarr API key

    # Radarr/Sonarr integration (v2.4)
    radarr_url = Column(String(500), nullable=True)  # Radarr instance URL (e.g., http://localhost:7878)
    radarr_api_key = Column(String(200), nullable=True)  # Radarr API key
    sonarr_url = Column(String(500), nullable=True)  # Sonarr instance URL (e.g., http://localhost:8989)
    sonarr_api_key = Column(String(200), nullable=True)  # Sonarr API key

    # Rate limiting settings (v2.2)
    tmdb_rate_limit = Column(Integer, nullable=True, default=40)  # TMDB requests per 10 seconds
    tracker_rate_limit = Column(Integer, nullable=True, default=10)  # Tracker requests per 10 seconds

    # Notification settings (v2.2)
    discord_webhook_url = Column(String(500), nullable=True)  # Discord webhook URL
    notification_email = Column(String(255), nullable=True)  # Email for notifications
    smtp_host = Column(String(255), nullable=True)  # SMTP server
    smtp_port = Column(Integer, nullable=True, default=587)  # SMTP port
    smtp_username = Column(String(255), nullable=True)  # SMTP username
    smtp_password = Column(String(500), nullable=True)  # SMTP password
    smtp_from = Column(String(255), nullable=True)  # From email address
    smtp_use_tls = Column(Integer, nullable=True, default=1)  # Use TLS (1=yes, 0=no)

    # Additional configuration (JSON for extensibility)
    extra_config = Column(Text, nullable=True)

    # Wizard state (v2.3)
    wizard_completed = Column(Boolean, nullable=True, default=False)  # Whether setup wizard has been completed

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, **kwargs):
        """
        Initialize Settings entry.

        Args:
            **kwargs: Setting values as keyword arguments
        """
        super().__init__(**kwargs)

    @property
    def announce_url(self) -> Optional[str]:
        """
        Compute the tracker announce URL from tracker_url and passkey.

        The announce URL follows the pattern: {tracker_url}/announce?passkey={passkey}
        This eliminates the need for a separate tracker_announce_url field.

        Returns:
            Computed announce URL, or None if tracker_url or passkey is missing
        """
        if self.tracker_url and self.tracker_passkey:
            base_url = self.tracker_url.rstrip('/')
            return f"{base_url}/announce?passkey={self.tracker_passkey}"
        return None

    def to_dict(self, mask_secrets: bool = True) -> dict:
        """
        Convert settings to dictionary.

        Args:
            mask_secrets: If True, mask sensitive values with asterisks

        Returns:
            Dictionary representation of settings
        """
        def mask_value(value: Optional[str]) -> Optional[str]:
            """Mask sensitive value."""
            if not value or not mask_secrets:
                return value
            if len(value) > 3:
                return value[:3] + '*' * (len(value) - 3)
            return '***'

        return {
            'id': self.id,
            # Tracker settings
            'tracker_url': self.tracker_url,
            'tracker_passkey': mask_value(self.tracker_passkey),
            'announce_url': self.announce_url,  # Computed from tracker_url + passkey
            # External services
            'flaresolverr_url': self.flaresolverr_url,
            'qbittorrent_host': self.qbittorrent_host,
            'qbittorrent_username': self.qbittorrent_username,
            'qbittorrent_password': mask_value(self.qbittorrent_password),
            'qbittorrent_content_path': self.qbittorrent_content_path,
            'tmdb_api_key': mask_value(self.tmdb_api_key),
            # Directory paths
            'input_media_path': self.input_media_path,
            'output_dir': self.output_dir,
            'torrent_output_dir': self.torrent_output_dir,
            # Hardlink settings (v2.5)
            'hardlink_enabled': bool(self.hardlink_enabled) if self.hardlink_enabled is not None else True,
            'hardlink_fallback_copy': bool(self.hardlink_fallback_copy) if self.hardlink_fallback_copy is not None else True,
            # Application settings
            'log_level': self.log_level,
            'tmdb_cache_ttl_days': self.tmdb_cache_ttl_days,
            'tag_sync_interval_hours': self.tag_sync_interval_hours,
            # Image hosting (v2.1)
            'imgbb_api_key': mask_value(self.imgbb_api_key),
            # Approval workflow (v2.1)
            'auto_resume_after_approval': self.auto_resume_after_approval,
            # Prowlarr integration (v2.1)
            'prowlarr_url': self.prowlarr_url,
            'prowlarr_api_key': mask_value(self.prowlarr_api_key),
            # Radarr/Sonarr integration (v2.4)
            'radarr_url': self.radarr_url,
            'radarr_api_key': mask_value(self.radarr_api_key),
            'sonarr_url': self.sonarr_url,
            'sonarr_api_key': mask_value(self.sonarr_api_key),
            # Rate limiting (v2.2)
            'tmdb_rate_limit': self.tmdb_rate_limit,
            'tracker_rate_limit': self.tracker_rate_limit,
            # Notifications (v2.2)
            'discord_webhook_url': mask_value(self.discord_webhook_url),
            'notification_email': self.notification_email,
            'smtp_host': self.smtp_host,
            'smtp_port': self.smtp_port,
            'smtp_username': self.smtp_username,
            'smtp_password': mask_value(self.smtp_password),
            'smtp_from': self.smtp_from,
            'smtp_use_tls': bool(self.smtp_use_tls) if self.smtp_use_tls is not None else True,
            # Additional
            'extra_config': self.extra_config,
            # Wizard state
            'wizard_completed': bool(self.wizard_completed) if self.wizard_completed is not None else False,
            # Timestamps
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    @classmethod
    def get_settings(cls, db: Session) -> 'Settings':
        """
        Get the singleton settings instance.

        If no settings exist, creates a new settings row with default values.
        This ensures the singleton pattern is maintained.

        Args:
            db: SQLAlchemy database session

        Returns:
            Settings instance (the only row in the table)
        """
        settings = db.query(cls).first()

        if not settings:
            # Create default settings
            settings = cls(
                log_level='INFO',
                tmdb_cache_ttl_days=30,
                tag_sync_interval_hours=24
            )
            db.add(settings)
            db.commit()
            db.refresh(settings)

        return settings

    @classmethod
    def update_settings(cls, db: Session, **kwargs) -> 'Settings':
        """
        Update settings with provided values.

        Args:
            db: SQLAlchemy database session
            **kwargs: Setting values to update

        Returns:
            Updated Settings instance

        Example:
            Settings.update_settings(
                db,
                tracker_url='https://lacale.example',
                tracker_passkey='abc123xyz',
                flaresolverr_url='http://localhost:8191'
            )
        """
        settings = cls.get_settings(db)

        # Update provided fields
        for key, value in kwargs.items():
            if hasattr(settings, key):
                setattr(settings, key, value)

        settings.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(settings)
        return settings

    def resolve_path(self, path_value: Optional[str]) -> Optional[str]:
        """
        Resolve a path, making relative paths absolute using input_media_path as base.

        If the path is relative (no drive letter on Windows, doesn't start with / on Linux),
        it is resolved against input_media_path. If input_media_path is not set, falls back
        to the current working directory.

        Args:
            path_value: The path to resolve (may be relative or absolute)

        Returns:
            Absolute path string, or None if path_value is empty/None
        """
        if not path_value:
            return None
        from pathlib import Path
        p = Path(path_value)
        if not p.is_absolute():
            base = Path(self.input_media_path) if self.input_media_path else Path.cwd()
            p = base / p
        return str(p)

    def get_output_dir_for_file(self, source_file_path: str) -> str:
        """
        Get the output directory for a given source file.

        If output_dir is configured, resolves it (relative paths use input_media_path as base).
        Otherwise, returns the parent directory of the source file
        (files are generated in the same folder as the source).

        Args:
            source_file_path: Path to the source media file

        Returns:
            Path to the directory where output files should be saved
        """
        if self.output_dir:
            return self.resolve_path(self.output_dir)
        # Default: same folder as source file
        from pathlib import Path
        return str(Path(source_file_path).parent)

    def __repr__(self) -> str:
        """String representation of settings."""
        return (
            f"<Settings(id={self.id}, tracker_url='{self.tracker_url}', "
            f"flaresolverr_url='{self.flaresolverr_url}')>"
        )
