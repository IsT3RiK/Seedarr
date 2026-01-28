"""Add upload metadata fields to file_entries

Revision ID: 005_add_upload_metadata_fields
Revises: 004_remove_tracker_announce_url
Create Date: 2026-01-14 10:00:00.000000

This migration adds upload metadata fields to the file_entries table for
storing tracker category, tags, TMDB metadata, and upload results.

New Fields:
    Release Information:
        - release_name: Formatted release name (e.g., "Movie.2024.1080p.BluRay.x264-GROUP")

    Tracker Metadata:
        - category_id: Tracker category ID
        - tag_ids: JSON array of tracker tag IDs

    TMDB Metadata:
        - tmdb_id: TMDB ID
        - tmdb_type: "movie" or "tv"
        - cover_url: Cover image URL from TMDB
        - description: Plot/description from TMDB

    Generated Files:
        - torrent_path: Path to generated .torrent file
        - nfo_path: Path to generated .nfo file

    MediaInfo:
        - mediainfo_data: JSON object with full MediaInfo extraction

    Upload Result:
        - tracker_torrent_id: Torrent ID returned by tracker
        - tracker_torrent_url: Torrent URL on tracker
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005_add_upload_metadata_fields'
down_revision = '004_remove_tracker_announce_url'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add upload metadata fields to file_entries table.
    """
    # Release information
    op.add_column('file_entries',
        sa.Column('release_name', sa.String(length=500), nullable=True))

    # Tracker metadata
    op.add_column('file_entries',
        sa.Column('category_id', sa.String(length=50), nullable=True))
    op.add_column('file_entries',
        sa.Column('tag_ids', sa.JSON(), nullable=True))

    # TMDB metadata
    op.add_column('file_entries',
        sa.Column('tmdb_id', sa.String(length=50), nullable=True))
    op.add_column('file_entries',
        sa.Column('tmdb_type', sa.String(length=20), nullable=True))
    op.add_column('file_entries',
        sa.Column('cover_url', sa.String(length=1000), nullable=True))
    op.add_column('file_entries',
        sa.Column('description', sa.Text(), nullable=True))

    # Generated file paths
    op.add_column('file_entries',
        sa.Column('torrent_path', sa.String(length=1000), nullable=True))
    op.add_column('file_entries',
        sa.Column('nfo_path', sa.String(length=1000), nullable=True))

    # MediaInfo data
    op.add_column('file_entries',
        sa.Column('mediainfo_data', sa.JSON(), nullable=True))

    # Upload result
    op.add_column('file_entries',
        sa.Column('tracker_torrent_id', sa.String(length=100), nullable=True))
    op.add_column('file_entries',
        sa.Column('tracker_torrent_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    """
    Remove upload metadata fields from file_entries table.
    """
    op.drop_column('file_entries', 'tracker_torrent_url')
    op.drop_column('file_entries', 'tracker_torrent_id')
    op.drop_column('file_entries', 'mediainfo_data')
    op.drop_column('file_entries', 'nfo_path')
    op.drop_column('file_entries', 'torrent_path')
    op.drop_column('file_entries', 'description')
    op.drop_column('file_entries', 'cover_url')
    op.drop_column('file_entries', 'tmdb_type')
    op.drop_column('file_entries', 'tmdb_id')
    op.drop_column('file_entries', 'tag_ids')
    op.drop_column('file_entries', 'category_id')
    op.drop_column('file_entries', 'release_name')
