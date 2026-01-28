"""Add Tags table for dynamic tag storage

Revision ID: 003_add_tags_table
Revises: 002_add_tmdb_cache
Create Date: 2026-01-06 12:28:00.000000

This migration creates the tags table for dynamic storage of tracker tag IDs
and labels. Tags are fetched from the tracker API at application startup,
eliminating hardcoded tag IDs and enabling automatic synchronization with
tracker changes.

Tag Management Strategy:
    - Fetch tag list from tracker API at startup
    - Store/update tags in database
    - Application references tags by label (user-friendly)
    - Dynamic lookup resolves label to current tag_id
    - Daily background task refreshes tag list
    - Graceful degradation with cached values if API unavailable

Benefits:
    - No hardcoded tag IDs in application code
    - Automatic adaptation to tracker tag changes
    - Admin visibility into current tag mappings
    - Warning logs if configured tags no longer exist
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003_add_tags_table'
down_revision = '002_add_tmdb_cache'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create tags table for dynamic tracker tag storage.

    Table Structure:
        - id: Primary key (auto-increment)
        - tag_id: Tracker's tag ID (unique, indexed)
        - label: Human-readable tag name/label (indexed)
        - category: Optional tag category for grouping
        - description: Optional tag description from tracker
        - updated_at: Timestamp of last update from tracker (indexed)
        - created_at: Timestamp when tag first added to database
    """
    op.create_table(
        'tags',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tag_id', sa.String(length=50), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for performance
    op.create_index('idx_tags_tag_id', 'tags', ['tag_id'], unique=True)
    op.create_index('idx_tags_label', 'tags', ['label'])
    op.create_index('idx_tags_updated_at', 'tags', ['updated_at'])


def downgrade() -> None:
    """
    Drop tags table.

    WARNING: This will delete all tag mappings, reverting to
    hardcoded tag IDs (if any exist in codebase).
    """
    # Drop indexes
    op.drop_index('idx_tags_updated_at', table_name='tags')
    op.drop_index('idx_tags_label', table_name='tags')
    op.drop_index('idx_tags_tag_id', table_name='tags')

    # Drop table
    op.drop_table('tags')
