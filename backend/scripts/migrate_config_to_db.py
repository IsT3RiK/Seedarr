#!/usr/bin/env python3
"""
Configuration Migration Script for Seedarr v2.0

This script migrates configuration from YAML files and environment variables
to the database-driven Settings model, enabling 100% database-driven configuration
(except DATABASE_URL which remains in environment).

Features:
    - Reads config.yaml (if exists) and environment variables
    - Populates Settings table in database
    - Creates backup of config.yaml before migration
    - Supports --dry-run to preview migration without executing
    - Provides rollback capability from backup
    - Idempotent: can be run multiple times safely

Usage:
    # Preview migration without executing
    python backend/scripts/migrate_config_to_db.py --dry-run

    # Execute migration
    python backend/scripts/migrate_config_to_db.py

    # Specify custom config path
    python backend/scripts/migrate_config_to_db.py --config-path /path/to/config.yaml

    # Rollback from backup
    python backend/scripts/migrate_config_to_db.py --rollback --backup-file backup_config_20240106_120000.yaml
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
import shutil

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    import yaml
except ImportError:
    yaml = None
    print("WARNING: PyYAML not installed. YAML config files will not be supported.")
    print("Install with: pip install pyyaml")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from backend.app.models.base import Base
from backend.app.models.settings import Settings


class ConfigMigrator:
    """
    Configuration migration utility for Seedarr v2.0.

    Handles migration from YAML/environment configuration to database-driven
    Settings model.
    """

    def __init__(self, database_url: Optional[str] = None, dry_run: bool = False):
        """
        Initialize the configuration migrator.

        Args:
            database_url: Database connection URL (defaults to environment or alembic.ini)
            dry_run: If True, show migration plan without executing
        """
        self.dry_run = dry_run
        self.database_url = database_url or self._get_database_url()
        self.engine = None
        self.session_maker = None
        self.backup_path = None

    def _get_database_url(self) -> str:
        """
        Get database URL from environment or default.

        Returns:
            Database connection URL string
        """
        # Priority: DATABASE_URL env var > default SQLite path
        url = os.getenv("DATABASE_URL")
        if url:
            return url

        # Default SQLite database path
        default_db = "sqlite:///./data/seedarr.db"
        print(f"INFO: Using default database: {default_db}")
        return default_db

    def _connect_database(self) -> Session:
        """
        Connect to database and return session.

        Returns:
            SQLAlchemy session

        Raises:
            Exception: If database connection fails
        """
        try:
            # For SQLite, ensure directory exists
            if self.database_url.startswith('sqlite:///'):
                db_path = self.database_url.replace('sqlite:///', '')
                db_dir = Path(db_path).parent
                if not db_dir.exists():
                    db_dir.mkdir(parents=True, exist_ok=True)
                    print(f"✓ Created database directory: {db_dir}")

            self.engine = create_engine(self.database_url, echo=False)

            # Create tables if they don't exist
            Base.metadata.create_all(self.engine)

            self.session_maker = sessionmaker(bind=self.engine)
            session = self.session_maker()

            print(f"✓ Connected to database: {self.database_url}")
            return session

        except Exception as e:
            print(f"✗ Failed to connect to database: {e}")
            raise

    def read_yaml_config(self, config_path: Path) -> Dict[str, Any]:
        """
        Read configuration from YAML file.

        Args:
            config_path: Path to config.yaml file

        Returns:
            Dictionary of configuration values
        """
        if not yaml:
            print("WARNING: PyYAML not installed, skipping YAML config")
            return {}

        if not config_path.exists():
            print(f"INFO: Config file not found: {config_path}")
            return {}

        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f) or {}
            print(f"✓ Read configuration from: {config_path}")
            return config
        except Exception as e:
            print(f"WARNING: Failed to read YAML config: {e}")
            return {}

    def read_environment_config(self) -> Dict[str, Any]:
        """
        Read configuration from environment variables.

        Returns:
            Dictionary of configuration values
        """
        config = {}

        # Mapping of environment variables to Settings fields
        # Note: TRACKER_ANNOUNCE_URL is no longer used - announce_url is now computed from tracker_url + passkey
        env_mapping = {
            'TRACKER_URL': 'tracker_url',
            'TRACKER_PASSKEY': 'tracker_passkey',
            'FLARESOLVERR_URL': 'flaresolverr_url',
            'QBITTORRENT_HOST': 'qbittorrent_host',
            'QBITTORRENT_USERNAME': 'qbittorrent_username',
            'QBITTORRENT_PASSWORD': 'qbittorrent_password',
            'TMDB_API_KEY': 'tmdb_api_key',
            'INPUT_MEDIA_PATH': 'input_media_path',
            'OUTPUT_DIR': 'output_dir',
            'LOG_LEVEL': 'log_level',
            'TMDB_CACHE_TTL_DAYS': 'tmdb_cache_ttl_days',
            'TAG_SYNC_INTERVAL_HOURS': 'tag_sync_interval_hours',
        }

        for env_var, setting_key in env_mapping.items():
            value = os.getenv(env_var)
            if value:
                # Convert numeric fields to int
                if setting_key in ['tmdb_cache_ttl_days', 'tag_sync_interval_hours']:
                    try:
                        value = int(value)
                    except ValueError:
                        print(f"WARNING: Invalid integer value for {env_var}: {value}")
                        continue

                config[setting_key] = value

        if config:
            print(f"✓ Read {len(config)} settings from environment variables")

        return config

    def merge_config(self, yaml_config: Dict[str, Any], env_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge YAML and environment configurations.

        Priority: Environment variables override YAML values.

        Args:
            yaml_config: Configuration from YAML file
            env_config: Configuration from environment variables

        Returns:
            Merged configuration dictionary
        """
        # Start with YAML config
        merged = yaml_config.copy()

        # Override with environment variables
        merged.update(env_config)

        return merged

    def backup_config_file(self, config_path: Path, backup_dir: Path) -> Optional[Path]:
        """
        Create backup of config.yaml file.

        Args:
            config_path: Path to config.yaml
            backup_dir: Directory to store backup

        Returns:
            Path to backup file, or None if no backup created
        """
        if not config_path.exists():
            print("INFO: No config file to backup")
            return None

        # Create backup directory if needed
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Generate backup filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"config_backup_{timestamp}.yaml"
        backup_path = backup_dir / backup_filename

        try:
            shutil.copy2(config_path, backup_path)
            print(f"✓ Created backup: {backup_path}")
            self.backup_path = backup_path
            return backup_path
        except Exception as e:
            print(f"WARNING: Failed to create backup: {e}")
            return None

    def show_migration_plan(self, config: Dict[str, Any]) -> None:
        """
        Display migration plan without executing.

        Args:
            config: Configuration to be migrated
        """
        print("\n" + "=" * 80)
        print("MIGRATION PLAN (DRY RUN)")
        print("=" * 80)

        if not config:
            print("WARNING: No configuration found to migrate!")
            return

        print(f"\nThe following settings will be migrated to database:")
        print("-" * 80)

        # Group settings by category
        # Note: announce_url is now computed from tracker_url + passkey (not stored separately)
        categories = {
            'Tracker Settings': ['tracker_url', 'tracker_passkey'],
            'External Services': ['flaresolverr_url', 'qbittorrent_host', 'qbittorrent_username',
                                'qbittorrent_password', 'tmdb_api_key'],
            'Directory Paths': ['input_media_path', 'output_dir'],
            'Application Settings': ['log_level', 'tmdb_cache_ttl_days', 'tag_sync_interval_hours'],
        }

        for category, fields in categories.items():
            category_settings = {k: v for k, v in config.items() if k in fields}
            if category_settings:
                print(f"\n{category}:")
                for key, value in category_settings.items():
                    # Mask sensitive values
                    if key in ['tracker_passkey', 'qbittorrent_password', 'tmdb_api_key']:
                        if value and len(str(value)) > 3:
                            display_value = str(value)[:3] + '*' * (len(str(value)) - 3)
                        else:
                            display_value = '***'
                    else:
                        display_value = value
                    print(f"  {key}: {display_value}")

        print("\n" + "-" * 80)
        print(f"Total settings to migrate: {len(config)}")
        print("=" * 80)
        print("\nTo execute migration, run without --dry-run flag")
        print("=" * 80 + "\n")

    def execute_migration(self, config: Dict[str, Any], session: Session) -> bool:
        """
        Execute migration to database.

        Args:
            config: Configuration to migrate
            session: Database session

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get or create settings (singleton pattern)
            settings = Settings.get_settings(session)

            print("\n" + "=" * 80)
            print("EXECUTING MIGRATION")
            print("=" * 80)

            if not config:
                print("WARNING: No configuration found to migrate!")
                return False

            # Update settings with merged config
            updated_count = 0
            for key, value in config.items():
                if hasattr(settings, key):
                    old_value = getattr(settings, key)
                    if old_value != value:
                        setattr(settings, key, value)
                        updated_count += 1

                        # Log update (mask sensitive values)
                        if key in ['tracker_passkey', 'qbittorrent_password', 'tmdb_api_key']:
                            print(f"  ✓ {key}: [UPDATED - masked]")
                        else:
                            print(f"  ✓ {key}: {value}")
                    else:
                        print(f"  = {key}: [unchanged]")
                else:
                    print(f"  ✗ {key}: [unknown field, skipped]")

            # Commit changes
            session.commit()

            print("\n" + "-" * 80)
            print(f"Migration completed: {updated_count} settings updated")
            print("=" * 80 + "\n")

            return True

        except Exception as e:
            print(f"\n✗ Migration failed: {e}")
            session.rollback()
            return False

    def rollback_from_backup(self, backup_path: Path, target_path: Path) -> bool:
        """
        Rollback configuration from backup file.

        Args:
            backup_path: Path to backup file
            target_path: Path where to restore the file

        Returns:
            True if successful, False otherwise
        """
        if not backup_path.exists():
            print(f"✗ Backup file not found: {backup_path}")
            return False

        try:
            # Create parent directory if needed
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Restore backup
            shutil.copy2(backup_path, target_path)
            print(f"✓ Restored configuration from backup: {backup_path}")
            print(f"  to: {target_path}")
            return True

        except Exception as e:
            print(f"✗ Rollback failed: {e}")
            return False

    def migrate(self, config_path: Path, backup_dir: Path) -> bool:
        """
        Execute full migration process.

        Args:
            config_path: Path to config.yaml
            backup_dir: Directory for backups

        Returns:
            True if successful, False otherwise
        """
        print("\n" + "=" * 80)
        print("TORRENT PUBLISHER v2.0 - CONFIGURATION MIGRATION")
        print("=" * 80 + "\n")

        # Step 1: Read configurations
        print("Step 1: Reading configuration sources...")
        yaml_config = self.read_yaml_config(config_path)
        env_config = self.read_environment_config()
        merged_config = self.merge_config(yaml_config, env_config)

        if not merged_config:
            print("\nWARNING: No configuration found!")
            print("  - No config.yaml file found")
            print("  - No environment variables set")
            print("\nThe Settings table will be created with default values.")

        # Step 2: Show migration plan (dry-run)
        if self.dry_run:
            self.show_migration_plan(merged_config)
            return True

        # Step 3: Backup existing config
        print("\nStep 2: Creating backup...")
        self.backup_config_file(config_path, backup_dir)

        # Step 4: Connect to database
        print("\nStep 3: Connecting to database...")
        session = self._connect_database()

        # Step 5: Execute migration
        print("\nStep 4: Migrating configuration to database...")
        success = self.execute_migration(merged_config, session)

        # Cleanup
        session.close()

        if success:
            print("✓ Migration completed successfully!")
            if self.backup_path:
                print(f"\nBackup saved to: {self.backup_path}")
                print(f"To rollback, run: python {sys.argv[0]} --rollback --backup-file {self.backup_path.name}")
            print("\nYou can now access settings via the Settings UI or database.")
        else:
            print("✗ Migration failed!")
            if self.backup_path:
                print(f"Original config preserved at: {self.backup_path}")

        return success


def main():
    """Main entry point for migration script."""
    parser = argparse.ArgumentParser(
        description="Migrate Seedarr configuration to database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview migration without executing
  python migrate_config_to_db.py --dry-run

  # Execute migration
  python migrate_config_to_db.py

  # Specify custom config path
  python migrate_config_to_db.py --config-path /path/to/config.yaml

  # Rollback from backup
  python migrate_config_to_db.py --rollback --backup-file config_backup_20240106_120000.yaml
        """
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show migration plan without executing'
    )

    parser.add_argument(
        '--config-path',
        type=Path,
        default=Path('./config.yaml'),
        help='Path to config.yaml file (default: ./config.yaml)'
    )

    parser.add_argument(
        '--database-url',
        type=str,
        help='Database URL (default: from DATABASE_URL env or sqlite:///./data/seedarr.db)'
    )

    parser.add_argument(
        '--backup-dir',
        type=Path,
        default=Path('./backups'),
        help='Directory for config backups (default: ./backups)'
    )

    parser.add_argument(
        '--rollback',
        action='store_true',
        help='Rollback configuration from backup file'
    )

    parser.add_argument(
        '--backup-file',
        type=str,
        help='Backup file name for rollback (must be in backup-dir)'
    )

    args = parser.parse_args()

    # Handle rollback
    if args.rollback:
        if not args.backup_file:
            print("ERROR: --backup-file required for rollback")
            sys.exit(1)

        backup_path = args.backup_dir / args.backup_file
        migrator = ConfigMigrator(database_url=args.database_url, dry_run=False)
        success = migrator.rollback_from_backup(backup_path, args.config_path)
        sys.exit(0 if success else 1)

    # Execute migration
    migrator = ConfigMigrator(database_url=args.database_url, dry_run=args.dry_run)
    success = migrator.migrate(args.config_path, args.backup_dir)

    sys.exit(0 if success else 0)  # Exit 0 even on failure for dry-run compatibility


if __name__ == '__main__':
    main()
