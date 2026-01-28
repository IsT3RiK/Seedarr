"""Add C411 categories table

Revision ID: 018_add_c411_categories
Revises: 017_add_example_bbcode_templates
Create Date: 2026-01-26
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '018_add_c411_categories'
down_revision = '017_add_example_bbcode_templates'
branch_labels = None
depends_on = None


def upgrade():
    """Create c411_categories table."""
    op.create_table(
        'c411_categories',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tracker_id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.String(50), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('subcategories', sa.JSON(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tracker_id'], ['trackers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # Create index for faster lookups
    op.create_index(
        'ix_c411_categories_tracker_category',
        'c411_categories',
        ['tracker_id', 'category_id'],
        unique=True
    )


def downgrade():
    """Drop c411_categories table."""
    op.drop_index('ix_c411_categories_tracker_category', table_name='c411_categories')
    op.drop_table('c411_categories')
