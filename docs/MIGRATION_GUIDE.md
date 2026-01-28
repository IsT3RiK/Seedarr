# Migration Guide: v1.1.0 → v2.0

## Table of Contents

- [Overview](#overview)
- [Breaking Changes](#breaking-changes)
- [Pre-Migration Checklist](#pre-migration-checklist)
- [Migration Steps](#migration-steps)
- [Configuration Migration](#configuration-migration)
- [Database Schema Changes](#database-schema-changes)
- [Code Changes](#code-changes)
- [Post-Migration Verification](#post-migration-verification)
- [Rollback Procedure](#rollback-procedure)
- [Troubleshooting](#troubleshooting)

## Overview

Seedarr v2.0 is a **major refactoring** of v1.1.0 that resolves critical technical debt and establishes a foundation for multi-tracker support. This guide walks you through the migration process from v1.1.0 to v2.0.

### What's New in v2.0

**Architecture Changes**:
- ✅ Modular monolith architecture with clear component boundaries
- ✅ Adapter pattern for tracker-agnostic pipeline
- ✅ Typed exception hierarchy with retry logic
- ✅ Pipeline idempotence with checkpoint-based resumption
- ✅ Circuit breaker pattern for FlareSolverr failures

**Performance Improvements**:
- ✅ Async optimization with ProcessPoolExecutor for file hashing
- ✅ Persistent TMDB cache (>80% API call reduction)
- ✅ Non-blocking I/O throughout pipeline

**Reliability Enhancements**:
- ✅ Mandatory NFO validation enforcement
- ✅ Dynamic tag ID fetching from tracker
- ✅ Database-driven configuration management
- ✅ Comprehensive error handling

### Migration Impact

| Area | Impact Level | Description |
|------|--------------|-------------|
| **Configuration** | 🔴 High | YAML config → Database migration required |
| **Database Schema** | 🟡 Medium | New tables and columns (Alembic migrations) |
| **API Endpoints** | 🟢 Low | Backward compatible (no breaking changes) |
| **File Processing** | 🟢 Low | Same input/output, improved reliability |
| **Docker Deployment** | 🟢 Low | Same docker-compose.yml (no changes) |
| **External Dependencies** | 🟢 Low | Same services (FlareSolverr, qBittorrent, TMDB) |

**Estimated Migration Time**: 30-60 minutes (depending on database size)

## Breaking Changes

### 1. Configuration Files (CRITICAL)

**v1.1.0**: Runtime configuration in `config/config.yaml`

**v2.0**: Runtime configuration in database `Settings` table

**Breaking Change**: `config.yaml` is no longer read for runtime settings (except database connection).

**Migration Required**: ✓ Yes

```yaml
# v1.1.0: config.yaml (DEPRECATED in v2.0)
tracker:
  url: "https://lacale.example.com"
  passkey: "your_passkey_here"

flaresolverr:
  url: "http://localhost:8191"

tmdb:
  api_key: "your_tmdb_key"
```

```python
# v2.0: Database Settings table
settings = [
    {'key': 'tracker_url', 'value': 'https://lacale.example.com'},
    {'key': 'passkey', 'value': 'your_passkey_here'},
    {'key': 'flaresolverr_url', 'value': 'http://localhost:8191'},
    {'key': 'tmdb_api_key', 'value': 'your_tmdb_key'},
]
```

**Action**: Run configuration migration script (see [Configuration Migration](#configuration-migration))

### 2. TrackerUploader Class Removed

**v1.1.0**: Monolithic `TrackerUploader` class

**v2.0**: Decomposed into:
- `CloudflareSessionManager` (FlareSolverr, session management)
- `LaCaleClient` (tracker API logic)
- `LaCaleAdapter` (TrackerAdapter interface implementation)

**Breaking Change**: Direct imports of `TrackerUploader` will fail.

**Migration Required**: ✓ Yes (if custom code imports TrackerUploader)

```python
# v1.1.0 (DEPRECATED)
from backend.app.services.tracker_uploader import TrackerUploader
uploader = TrackerUploader(config)
await uploader.upload(...)

# v2.0 (NEW)
from backend.app.adapters.lacale_adapter import LaCaleAdapter
adapter = LaCaleAdapter(
    flaresolverr_url="http://localhost:8191",
    tracker_url="https://lacale.example.com",
    passkey="your_passkey_here"
)
await adapter.authenticate()
result = await adapter.upload_torrent(...)
```

**Action**: Update custom code to use `LaCaleAdapter` instead of `TrackerUploader`

### 3. Database Schema Changes

**v1.1.0**: Basic `file_entries` table with minimal fields

**v2.0**: Enhanced `file_entries` with checkpoint fields + new tables

**Breaking Change**: Direct database queries may fail due to schema changes.

**Migration Required**: ✓ Yes (Alembic migrations)

**New Tables**:
- `tmdb_cache` - Persistent TMDB metadata cache
- `tags` - Dynamic tag storage from tracker
- `settings` - Database-driven configuration

**New Columns in `file_entries`**:
- `scanned_at` - Scan checkpoint timestamp
- `analyzed_at` - Analysis checkpoint timestamp
- `renamed_at` - Rename checkpoint timestamp
- `metadata_generated_at` - Metadata generation checkpoint
- `uploaded_at` - Upload checkpoint timestamp

**Action**: Run Alembic migrations (see [Database Schema Changes](#database-schema-changes))

### 4. Exception Handling

**v1.1.0**: Generic `Exception` catching with string errors

**v2.0**: Typed exception hierarchy with retry logic

**Breaking Change**: Custom error handlers may need updates.

**Migration Required**: ✗ No (backward compatible, but recommended to update)

```python
# v1.1.0 (DEPRECATED)
try:
    await upload_torrent(...)
except Exception as e:
    logger.error(f"Upload failed: {e}")

# v2.0 (RECOMMENDED)
from backend.app.services.exceptions import (
    TrackerAPIError,
    CloudflareBypassError,
    NetworkRetryableError
)

try:
    await adapter.upload_torrent(...)
except TrackerAPIError as e:
    # Non-retryable error (invalid passkey, bad request)
    logger.error(f"Upload failed permanently: {e}")
except NetworkRetryableError as e:
    # Retryable error (network timeout, service unavailable)
    logger.warning(f"Upload failed temporarily, will retry: {e}")
```

## Pre-Migration Checklist

Before starting migration, ensure:

- [ ] **Backup current database**: `cp backend/data/seedarr.db backend/data/seedarr.db.backup`
- [ ] **Backup config files**: `cp -r backend/config backend/config.backup`
- [ ] **Document current settings**: Note all tracker URLs, passkeys, API keys
- [ ] **Stop all processing**: Ensure no files are currently being processed
- [ ] **Test environment ready**: Have a test environment to validate migration
- [ ] **Docker images pulled**: `docker-compose pull` (if using Docker)
- [ ] **Dependencies updated**: `pip install -r requirements.txt` (if running locally)
- [ ] **FlareSolverr running**: Verify FlareSolverr service is accessible
- [ ] **Database write access**: Ensure application has write permissions to database

## Migration Steps

### Step 1: Backup Current System

```bash
# Navigate to project directory
cd /path/to/seedarr

# Backup database
cp backend/data/seedarr.db backend/data/seedarr.db.v1.1.0.backup

# Backup configuration
cp -r backend/config backend/config.v1.1.0.backup

# Backup processed files list (optional)
sqlite3 backend/data/seedarr.db "SELECT * FROM file_entries;" > file_entries_backup.sql

# Create migration date marker
echo "Migration started: $(date)" > migration.log
```

### Step 2: Stop Application

```bash
# If using Docker Compose
docker-compose down

# If running locally
# Stop uvicorn process (Ctrl+C or kill process)

# Verify no processes running
ps aux | grep uvicorn
```

### Step 3: Update Codebase

```bash
# Pull v2.0 code
git fetch origin
git checkout v2.0

# Or clone fresh
# git clone -b v2.0 https://github.com/yourorg/seedarr.git

# Install updated dependencies
pip install -r backend/requirements.txt

# Or with Docker
docker-compose build
```

### Step 4: Run Database Migrations

```bash
# Navigate to backend directory
cd backend

# Run Alembic migrations
alembic upgrade head

# Verify migrations applied
alembic current

# Expected output:
# 001_add_checkpoint_fields (head)
# 002_add_tmdb_cache
# 003_add_tags_table
# 004_add_settings_table
```

**Migration Scripts Executed**:

1. **001_add_checkpoint_fields.py**: Adds checkpoint timestamp columns to `file_entries`
2. **002_add_tmdb_cache.py**: Creates `tmdb_cache` table with TTL support
3. **003_add_tags_table.py**: Creates `tags` table for dynamic tag storage
4. **004_add_settings_table.py**: Creates `settings` table for configuration

### Step 5: Migrate Configuration to Database

Run the configuration migration script:

```bash
# Run migration script
python scripts/migrate_config_to_db.py --config backend/config/config.yaml

# Expected output:
# ✓ Database connection verified
# ✓ Reading configuration from: backend/config/config.yaml
# ✓ Migrating tracker settings...
# ✓ Migrating FlareSolverr settings...
# ✓ Migrating TMDB settings...
# ✓ Migrating directory paths...
# ✓ Migration complete! 12 settings migrated.
```

**Manual Migration** (if script unavailable):

```python
# backend/scripts/migrate_config_manually.py
import yaml
from backend.app.database import SessionLocal
from backend.app.models.settings import Settings

# Load old config
with open('backend/config/config.yaml') as f:
    config = yaml.safe_load(f)

# Create database session
db = SessionLocal()

# Migrate settings
settings_to_migrate = [
    ('tracker_url', config['tracker']['url']),
    ('passkey', config['tracker']['passkey']),
    ('flaresolverr_url', config['flaresolverr']['url']),
    ('tmdb_api_key', config['tmdb']['api_key']),
    ('input_media_path', config['paths']['input_media_path']),
    ('output_dir', config['paths']['output_dir']),
    # Add other settings...
]

for key, value in settings_to_migrate:
    setting = Settings(key=key, value=value, value_type='string')
    db.add(setting)

db.commit()
db.close()

print("✓ Configuration migrated to database")
```

### Step 6: Initialize Dynamic Tags

```bash
# Run tag synchronization script
python scripts/sync_tags_from_tracker.py

# Expected output:
# ✓ Authenticating with tracker...
# ✓ Fetching tags from tracker API...
# ✓ Synced 25 tags to database
# ✓ Tags: BluRay, 1080p, French Audio, VOSTFR, ...
```

### Step 7: Verify Database State

```bash
# Check settings migrated
sqlite3 backend/data/seedarr.db "SELECT key, value FROM settings LIMIT 10;"

# Check file_entries have new columns
sqlite3 backend/data/seedarr.db "PRAGMA table_info(file_entries);"

# Check new tables exist
sqlite3 backend/data/seedarr.db ".tables"
# Expected: file_entries, tmdb_cache, tags, settings, alembic_version

# Check checkpoint timestamps (should be NULL for existing entries)
sqlite3 backend/data/seedarr.db "SELECT file_path, scanned_at, analyzed_at FROM file_entries LIMIT 5;"
```

### Step 8: Update Environment Variables

```bash
# Edit .env or docker-compose.yml
# REMOVE deprecated variables:
# - CONFIG_FILE (no longer used)

# KEEP required variables:
# - DATABASE_URL
# - ENCRYPTION_KEY (for passkey encryption)
# - LOG_LEVEL

# Example .env
DATABASE_URL=sqlite:///./data/seedarr.db
ENCRYPTION_KEY=your-32-char-encryption-key-here
LOG_LEVEL=INFO
```

### Step 9: Start Application

```bash
# If using Docker Compose
docker-compose up -d

# If running locally
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Monitor logs
docker-compose logs -f backend  # Docker
# OR
tail -f logs/application.log    # Local
```

### Step 10: Verify Application Started

```bash
# Check application health
curl http://localhost:8000/health

# Expected response:
# {
#   "status": "healthy",
#   "database": "connected",
#   "flaresolverr": "available",
#   "tracker": "reachable"
# }

# Check settings loaded
curl http://localhost:8000/settings

# Expected response:
# {
#   "tracker_url": "https://lacale.example.com",
#   "flaresolverr_url": "http://localhost:8191",
#   ...
# }
```

## Configuration Migration

### Automatic Migration Script

Create `backend/scripts/migrate_config_to_db.py`:

```python
#!/usr/bin/env python3
"""
Configuration Migration Script: v1.1.0 → v2.0

Migrates runtime configuration from config.yaml to database Settings table.
"""

import argparse
import yaml
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal, engine
from app.models.base import Base
from app.models.settings import Settings

def migrate_config(config_path: str, dry_run: bool = False):
    """Migrate configuration from YAML to database."""

    # Create tables if needed
    Base.metadata.create_all(bind=engine)

    # Load YAML config
    print(f"✓ Reading configuration from: {config_path}")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Map config to settings
    settings_map = {
        # Tracker settings
        'tracker_url': config.get('tracker', {}).get('url'),
        'passkey': config.get('tracker', {}).get('passkey'),

        # FlareSolverr settings
        'flaresolverr_url': config.get('flaresolverr', {}).get('url'),
        'flaresolverr_timeout': str(config.get('flaresolverr', {}).get('timeout', 60000)),

        # TMDB settings
        'tmdb_api_key': config.get('tmdb', {}).get('api_key'),
        'tmdb_cache_ttl_days': str(config.get('tmdb', {}).get('cache_ttl_days', 30)),

        # Path settings
        'input_media_path': config.get('paths', {}).get('input_media_path'),
        'output_dir': config.get('paths', {}).get('output_dir'),

        # Pipeline settings
        'tag_sync_interval_hours': str(config.get('pipeline', {}).get('tag_sync_interval', 24)),
    }

    # Filter out None values
    settings_map = {k: v for k, v in settings_map.items() if v is not None}

    if dry_run:
        print("\n[DRY RUN] Settings that would be migrated:")
        for key, value in settings_map.items():
            # Mask sensitive values
            display_value = value
            if key in ('passkey', 'tmdb_api_key'):
                display_value = f"***{value[-4:]}" if len(value) > 4 else "***"
            print(f"  {key}: {display_value}")
        print(f"\nTotal: {len(settings_map)} settings")
        return

    # Migrate to database
    db = SessionLocal()
    try:
        migrated_count = 0

        for key, value in settings_map.items():
            # Check if setting exists
            existing = db.query(Settings).filter(Settings.key == key).first()

            if existing:
                print(f"  Updating: {key}")
                existing.value = value
            else:
                print(f"  Creating: {key}")
                setting = Settings(
                    key=key,
                    value=value,
                    value_type='string',
                    description=f'Migrated from config.yaml'
                )
                db.add(setting)

            migrated_count += 1

        db.commit()
        print(f"\n✓ Migration complete! {migrated_count} settings migrated.")

    except Exception as e:
        db.rollback()
        print(f"\n✗ Migration failed: {e}")
        raise
    finally:
        db.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Migrate configuration from YAML to database'
    )
    parser.add_argument(
        '--config',
        default='backend/config/config.yaml',
        help='Path to config.yaml'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview migration without applying changes'
    )

    args = parser.parse_args()

    migrate_config(args.config, dry_run=args.dry_run)
```

**Usage**:

```bash
# Dry run (preview only)
python backend/scripts/migrate_config_to_db.py --dry-run

# Actual migration
python backend/scripts/migrate_config_to_db.py --config backend/config/config.yaml
```

### Manual Configuration Steps

If automatic migration fails, manually insert settings:

```sql
-- backend/scripts/insert_settings.sql

-- Tracker settings
INSERT INTO settings (key, value, value_type, description)
VALUES
('tracker_url', 'https://lacale.example.com', 'string', 'Tracker base URL'),
('passkey', 'your_passkey_here', 'string', 'Tracker passkey'),

-- FlareSolverr settings
('flaresolverr_url', 'http://localhost:8191', 'string', 'FlareSolverr service URL'),
('flaresolverr_timeout', '60000', 'integer', 'FlareSolverr timeout (ms)'),

-- TMDB settings
('tmdb_api_key', 'your_tmdb_key', 'string', 'TMDB API key'),
('tmdb_cache_ttl_days', '30', 'integer', 'TMDB cache TTL (days)'),

-- Path settings
('input_media_path', '/media/input', 'string', 'Input media directory'),
('output_dir', '/media/output', 'string', 'Output releases directory'),

-- Pipeline settings
('tag_sync_interval_hours', '24', 'integer', 'Tag sync interval (hours)');
```

Apply:

```bash
sqlite3 backend/data/seedarr.db < backend/scripts/insert_settings.sql
```

## Database Schema Changes

### Checkpoint Fields Migration

**Migration**: `001_add_checkpoint_fields.py`

```python
def upgrade():
    op.add_column('file_entries', sa.Column('scanned_at', sa.DateTime(), nullable=True))
    op.add_column('file_entries', sa.Column('analyzed_at', sa.DateTime(), nullable=True))
    op.add_column('file_entries', sa.Column('renamed_at', sa.DateTime(), nullable=True))
    op.add_column('file_entries', sa.Column('metadata_generated_at', sa.DateTime(), nullable=True))
    op.add_column('file_entries', sa.Column('uploaded_at', sa.DateTime(), nullable=True))

    # Create indexes for query performance
    op.create_index('idx_file_entries_status', 'file_entries', ['status'])
    op.create_index('idx_file_entries_uploaded_at', 'file_entries', ['uploaded_at'])

def downgrade():
    op.drop_index('idx_file_entries_uploaded_at')
    op.drop_index('idx_file_entries_status')
    op.drop_column('file_entries', 'uploaded_at')
    op.drop_column('file_entries', 'metadata_generated_at')
    op.drop_column('file_entries', 'renamed_at')
    op.drop_column('file_entries', 'analyzed_at')
    op.drop_column('file_entries', 'scanned_at')
```

**Impact**: Existing `file_entries` rows will have NULL checkpoint timestamps. This is expected and correct - they represent pre-v2.0 entries.

### TMDB Cache Table

**Migration**: `002_add_tmdb_cache.py`

```python
def upgrade():
    op.create_table(
        'tmdb_cache',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tmdb_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('year', sa.Integer()),
        sa.Column('cast', sa.JSON()),
        sa.Column('plot', sa.Text()),
        sa.Column('ratings', sa.JSON()),
        sa.Column('extra_data', sa.JSON()),
        sa.Column('cached_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), default=datetime.utcnow),
        sa.Column('updated_at', sa.DateTime(), default=datetime.utcnow, onupdate=datetime.utcnow)
    )

    # Indexes
    op.create_index('idx_tmdb_cache_tmdb_id', 'tmdb_cache', ['tmdb_id'], unique=True)
    op.create_index('idx_tmdb_cache_expires_at', 'tmdb_cache', ['expires_at'])

def downgrade():
    op.drop_table('tmdb_cache')
```

**Impact**: New table, no data loss. TMDB cache will populate automatically as files are processed.

### Tags Table

**Migration**: `003_add_tags_table.py`

```python
def upgrade():
    op.create_table(
        'tags',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tag_id', sa.String(), nullable=False),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('category', sa.String()),
        sa.Column('description', sa.Text()),
        sa.Column('created_at', sa.DateTime(), default=datetime.utcnow),
        sa.Column('updated_at', sa.DateTime(), default=datetime.utcnow, onupdate=datetime.utcnow)
    )

    # Indexes
    op.create_index('idx_tags_tag_id', 'tags', ['tag_id'], unique=True)
    op.create_index('idx_tags_label', 'tags', ['label'])

def downgrade():
    op.drop_table('tags')
```

**Impact**: New table, no data loss. Tags will sync automatically on application startup.

## Code Changes

### If Using Custom Extensions

If you have custom code that imports v1.1.0 components:

#### 1. TrackerUploader → LaCaleAdapter

```python
# Before (v1.1.0)
from backend.app.services.tracker_uploader import TrackerUploader

uploader = TrackerUploader(config)
result = await uploader.upload_torrent(torrent_data, metadata)

# After (v2.0)
from backend.app.adapters.lacale_adapter import LaCaleAdapter

adapter = LaCaleAdapter(
    flaresolverr_url=settings.flaresolverr_url,
    tracker_url=settings.tracker_url,
    passkey=settings.passkey
)
await adapter.authenticate()
result = await adapter.upload_torrent(
    torrent_data=torrent_data,
    release_name=metadata['name'],
    category_id=metadata['category_id'],
    tag_ids=metadata['tag_ids'],
    nfo_content=metadata['nfo']
)
```

#### 2. Configuration Access

```python
# Before (v1.1.0)
from backend.app.config.settings import load_config
config = load_config('config/config.yaml')
tracker_url = config['tracker']['url']

# After (v2.0)
from backend.app.models.settings import Settings
from backend.app.database import SessionLocal

db = SessionLocal()
tracker_url = Settings.get_value(db, 'tracker_url')
db.close()

# Or in FastAPI route with dependency injection
from fastapi import Depends
from backend.app.dependencies import get_settings

@app.get("/endpoint")
async def endpoint(settings = Depends(get_settings)):
    tracker_url = settings.tracker_url
```

#### 3. Exception Handling

```python
# Before (v1.1.0)
try:
    await process_file(file_path)
except Exception as e:
    logger.error(f"Processing failed: {e}")

# After (v2.0)
from backend.app.services.exceptions import (
    TrackerAPIError,
    NetworkRetryableError,
    CloudflareBypassError
)

try:
    await process_file(file_path)
except CloudflareBypassError as e:
    logger.error(f"FlareSolverr unavailable: {e}")
    # Circuit breaker will handle retry
except NetworkRetryableError as e:
    logger.warning(f"Network error, will retry: {e}")
    # Auto-retry with backoff
except TrackerAPIError as e:
    logger.error(f"Permanent error: {e}")
    # Fail fast, no retry
```

## Post-Migration Verification

### 1. Health Check

```bash
# API health endpoint
curl http://localhost:8000/health

# Expected response:
{
  "status": "healthy",
  "database": "connected",
  "flaresolverr": {
    "available": true,
    "circuit_breaker": "closed"
  },
  "tracker": {
    "reachable": true,
    "authenticated": true
  }
}
```

### 2. Settings Verification

```bash
# Check settings loaded
curl http://localhost:8000/settings | jq

# Expected: All settings from config.yaml now in database
# Verify critical settings:
# - tracker_url
# - passkey (should be masked: ***XXXX)
# - flaresolverr_url
# - tmdb_api_key (should be masked)
```

### 3. Database Verification

```bash
# Check migrations applied
sqlite3 backend/data/seedarr.db "SELECT * FROM alembic_version;"

# Expected: Latest migration version

# Check new tables exist
sqlite3 backend/data/seedarr.db ".tables"

# Expected tables:
# - file_entries (with new checkpoint columns)
# - tmdb_cache
# - tags
# - settings
# - alembic_version

# Check settings populated
sqlite3 backend/data/seedarr.db "SELECT COUNT(*) FROM settings;"

# Expected: At least 8-10 settings
```

### 4. Tag Synchronization

```bash
# Check tags loaded
sqlite3 backend/data/seedarr.db "SELECT COUNT(*) FROM tags;"

# Expected: 20-30 tags (tracker-dependent)

# View tags
sqlite3 backend/data/seedarr.db "SELECT tag_id, label, category FROM tags LIMIT 10;"

# Expected: Tag IDs and labels from tracker
```

### 5. TMDB Cache

```bash
# TMDB cache starts empty (populates during processing)
sqlite3 backend/data/seedarr.db "SELECT COUNT(*) FROM tmdb_cache;"

# Expected: 0 (initially)

# Process a test file to verify cache works
# After processing, check cache populated:
sqlite3 backend/data/seedarr.db "SELECT tmdb_id, title, year FROM tmdb_cache;"
```

### 6. Pipeline Idempotence Test

```bash
# Create test file entry
sqlite3 backend/data/seedarr.db <<EOF
INSERT INTO file_entries (file_path, status, created_at, updated_at)
VALUES ('/test/Movie.2024.1080p.mkv', 'pending', datetime('now'), datetime('now'));
EOF

# Process file (will go through all stages)
# Then simulate upload failure and retry

# Check checkpoint timestamps set
sqlite3 backend/data/seedarr.db "
SELECT file_path, scanned_at, analyzed_at, renamed_at, metadata_generated_at, uploaded_at
FROM file_entries
WHERE file_path = '/test/Movie.2024.1080p.mkv';
"

# Expected: All timestamps set (indicating checkpoints working)
```

### 7. End-to-End Test

```bash
# Place test file in input directory
cp /path/to/test.mkv /media/input/Test.Movie.2024.1080p.mkv

# Monitor logs
docker-compose logs -f backend

# Expected log sequence:
# 1. "Starting pipeline processing for: Test.Movie.2024.1080p.mkv"
# 2. "Stage 1/5: Scanning file"
# 3. "✓ Scan stage completed"
# 4. "Stage 2/5: Analyzing file"
# 5. "✓ Analysis stage completed"
# 6. "Stage 3/5: Renaming file"
# 7. "✓ Rename stage completed"
# 8. "Stage 4/5: Generating metadata"
# 9. "✓ NFO validation passed"
# 10. "✓ Metadata generation stage completed"
# 11. "Stage 5/5: Uploading to tracker"
# 12. "✓ Upload stage completed"
# 13. "Pipeline processing completed successfully"

# Verify upload on tracker
# Check qBittorrent seeding
```

## Rollback Procedure

If migration fails or issues arise, rollback to v1.1.0:

### Step 1: Stop v2.0 Application

```bash
docker-compose down
# OR
# Kill uvicorn process
```

### Step 2: Restore Database Backup

```bash
# Restore database
cp backend/data/seedarr.db.v1.1.0.backup backend/data/seedarr.db

# Verify restored
sqlite3 backend/data/seedarr.db ".tables"
# Should NOT have: tmdb_cache, tags, settings (v2.0 tables)
```

### Step 3: Restore Configuration

```bash
# Restore config files
cp -r backend/config.v1.1.0.backup/* backend/config/

# Verify config.yaml exists
cat backend/config/config.yaml
```

### Step 4: Checkout v1.1.0 Code

```bash
git checkout v1.1.0

# OR restore from backup
# cp -r /path/to/v1.1.0/backup/* .

# Reinstall v1.1.0 dependencies
pip install -r backend/requirements.txt
```

### Step 5: Restart Application

```bash
# Docker
docker-compose up -d

# Local
cd backend
python -m uvicorn app.main:app --reload
```

### Step 6: Verify Rollback

```bash
# Check application responds
curl http://localhost:8000/health

# Verify using config.yaml (not database settings)
# Process test file to ensure functionality intact
```

## Troubleshooting

### Issue: Database Migration Fails

**Symptoms**:
```
alembic.util.exc.CommandError: Can't locate revision identified by 'XXX'
```

**Solution**:
```bash
# Reset Alembic version
sqlite3 backend/data/seedarr.db "DELETE FROM alembic_version;"

# Stamp current version
alembic stamp head

# Re-run migrations
alembic upgrade head
```

### Issue: Settings Not Loaded

**Symptoms**: Application logs show "Setting 'tracker_url' not found"

**Solution**:
```bash
# Check settings table populated
sqlite3 backend/data/seedarr.db "SELECT * FROM settings;"

# If empty, re-run configuration migration
python backend/scripts/migrate_config_to_db.py --config backend/config/config.yaml
```

### Issue: FlareSolverr Circuit Breaker Open

**Symptoms**: All uploads fail with "Circuit breaker OPEN"

**Solution**:
```bash
# Check FlareSolverr running
curl http://localhost:8191

# Restart FlareSolverr
docker-compose restart flaresolverr

# Reset circuit breaker via admin API
curl -X POST http://localhost:8000/admin/circuit-breaker/reset
```

### Issue: Tags Not Synced

**Symptoms**: Tags table empty, uploads fail with "Invalid tag ID"

**Solution**:
```bash
# Manually sync tags
python backend/scripts/sync_tags_from_tracker.py

# Or via API
curl -X POST http://localhost:8000/admin/sync-tags
```

### Issue: TMDB Cache Not Working

**Symptoms**: TMDB API rate limit errors despite cache

**Solution**:
```bash
# Check TMDB cache table
sqlite3 backend/data/seedarr.db "SELECT COUNT(*) FROM tmdb_cache;"

# Check cache TTL setting
sqlite3 backend/data/seedarr.db "SELECT value FROM settings WHERE key = 'tmdb_cache_ttl_days';"

# Should be 30 or higher

# Manually populate cache for common movies
# (Run test processing on known files)
```

### Issue: Checkpoint Timestamps NULL

**Symptoms**: Retries don't skip stages, all stages re-run

**Solution**:
```bash
# This is expected for files processed before v2.0 migration

# Option 1: Let them re-process (recommended for small datasets)
# New checkpoints will be set during processing

# Option 2: Manually set checkpoints for completed uploads
sqlite3 backend/data/seedarr.db <<EOF
UPDATE file_entries
SET
  scanned_at = created_at,
  analyzed_at = created_at,
  renamed_at = created_at,
  metadata_generated_at = created_at,
  uploaded_at = created_at
WHERE status = 'uploaded';
EOF
```

---

## Support

If you encounter issues during migration:

1. **Check logs**: `docker-compose logs -f` or `tail -f logs/application.log`
2. **Review documentation**: [ARCHITECTURE.md](./ARCHITECTURE.md), [ADAPTER_PATTERN.md](./ADAPTER_PATTERN.md)
3. **Open issue**: Include migration logs, database state, error messages
4. **Community forum**: Ask questions in project discussions

## Next Steps After Migration

1. **Monitor first few uploads**: Ensure pipeline works end-to-end
2. **Review settings**: Adjust cache TTL, tag sync interval via Settings UI
3. **Set up backups**: Automate database backups (v2.0 database now critical)
4. **Optimize performance**: Review ProcessPoolExecutor workers, TMDB cache hit rate
5. **Explore new features**: Circuit breaker monitoring, health dashboard, adapter pattern

---

**Migration Guide Version**: 1.0
**Last Updated**: 2024-01-10
**Tested with**: Seedarr v1.1.0 → v2.0
**Author**: Claude Sonnet 4.5
