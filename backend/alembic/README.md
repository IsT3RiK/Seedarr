# Alembic Database Migrations

This directory contains Alembic database migrations for Seedarr v2.0.

## Overview

The migrations in this directory implement the new database schema for the refactoring initiative:

1. **001_add_checkpoint_fields.py** - Adds pipeline checkpoint timestamp fields to `file_entries` table
2. **002_add_tmdb_cache.py** - Creates `tmdb_cache` table for persistent TMDB metadata caching
3. **003_add_tags_table.py** - Creates `tags` table for dynamic tracker tag storage

## Setup

### Prerequisites

Ensure Alembic is installed:
```bash
pip install alembic
```

### Configuration

Database URL is configured via environment variable or `alembic.ini`:

**Option 1: Environment Variable (Recommended)**
```bash
export DATABASE_URL="sqlite:///./data/seedarr.db"
```

**Option 2: Edit alembic.ini**
```ini
sqlalchemy.url = sqlite:///./data/seedarr.db
```

## Running Migrations

### Apply All Migrations

To upgrade to the latest schema:
```bash
cd backend
alembic upgrade head
```

### Apply Specific Migration

To upgrade to a specific revision:
```bash
alembic upgrade 001_add_checkpoint_fields
```

### Rollback Migration

To downgrade one revision:
```bash
alembic downgrade -1
```

To downgrade to a specific revision:
```bash
alembic downgrade 001_add_checkpoint_fields
```

### Check Current Revision

To see current database revision:
```bash
alembic current
```

### View Migration History

To see all available revisions:
```bash
alembic history
```

## Migration Details

### Migration 001: Checkpoint Fields

**Purpose**: Enable idempotent pipeline processing

**Changes**:
- Adds `scanned_at` timestamp to `file_entries`
- Adds `analyzed_at` timestamp to `file_entries`
- Adds `renamed_at` timestamp to `file_entries`
- Adds `metadata_generated_at` timestamp to `file_entries`
- Adds `uploaded_at` timestamp to `file_entries`
- Creates indexes on `scanned_at` and `uploaded_at`

**Impact**: Allows pipeline to resume from last successful stage on failure

### Migration 002: TMDB Cache

**Purpose**: Reduce TMDB API calls through persistent caching

**Changes**:
- Creates `tmdb_cache` table with fields:
  - `tmdb_id` (indexed, unique)
  - `title`, `year`, `plot`
  - `cast` (JSON), `ratings` (JSON), `extra_data` (JSON)
  - `cached_at`, `expires_at` (indexed)

**Impact**:
- >80% reduction in TMDB API calls
- >90% cache hit rate for repeated lookups
- Cache survives application restart

### Migration 003: Tags Table

**Purpose**: Eliminate hardcoded tag IDs through dynamic tag storage

**Changes**:
- Creates `tags` table with fields:
  - `tag_id` (indexed, unique)
  - `label` (indexed), `category`, `description`
  - `updated_at` (indexed), `created_at`

**Impact**:
- No hardcoded tag IDs in application
- Automatic synchronization with tracker tag changes
- Graceful degradation if tracker API unavailable

## Verification

To verify the setup without running migrations:
```bash
cd backend
python verify_alembic_setup.py
```

## Creating New Migrations

To create a new migration:
```bash
alembic revision -m "description of changes"
```

To autogenerate migration from model changes:
```bash
alembic revision --autogenerate -m "description of changes"
```

## Troubleshooting

### "No module named 'alembic'"

Install Alembic:
```bash
pip install alembic
```

### "Can't locate revision identified by 'XXX'"

Check migration file naming and revision identifiers match.

### Database locked errors (SQLite)

Ensure no other processes are accessing the database file.

### Import errors in env.py

Verify all models are properly imported in `env.py` for autogenerate support.

## References

- [Alembic Documentation](https://alembic.sqlalchemy.org/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- Project Spec: `.auto-claude/specs/001-voici-un-r-capitulatif-technique-complet-et-struct/spec.md`
