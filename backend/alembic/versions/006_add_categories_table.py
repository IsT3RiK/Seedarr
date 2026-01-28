"""Add Categories table for dynamic category storage

Revision ID: 006_add_categories_table
Revises: 005_add_upload_metadata_fields
Create Date: 2026-01-14 11:00:00.000000

This migration creates the categories table for dynamic storage of tracker
category IDs and names. Categories are fetched from the tracker API at
application startup, eliminating hardcoded category IDs.

Category Management Strategy:
    - Fetch category list from tracker API at startup
    - Store/update categories in database
    - Application references categories by name (user-friendly)
    - Dynamic lookup resolves name to current category_id
    - Periodic refresh to stay synchronized with tracker
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '006_add_categories_table'
down_revision = '005_add_upload_metadata_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create categories table for dynamic tracker category storage.

    Table Structure:
        - id: Primary key (auto-increment)
        - category_id: Tracker's category ID (unique, indexed)
        - name: Human-readable category name (indexed)
        - slug: URL-friendly identifier (indexed)
        - description: Optional category description
        - updated_at: Timestamp of last update from tracker
        - created_at: Timestamp when category first added to database
    """
    op.create_table(
        'categories',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('category_id', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('slug', sa.String(length=200), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for performance
    op.create_index('idx_categories_category_id', 'categories', ['category_id'], unique=True)
    op.create_index('idx_categories_name', 'categories', ['name'])
    op.create_index('idx_categories_slug', 'categories', ['slug'])


def downgrade() -> None:
    """Drop categories table."""
    # Drop indexes
    op.drop_index('idx_categories_slug', table_name='categories')
    op.drop_index('idx_categories_name', table_name='categories')
    op.drop_index('idx_categories_category_id', table_name='categories')

    # Drop table
    op.drop_table('categories')
