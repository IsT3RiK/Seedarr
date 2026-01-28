"""Add default_template_id to trackers table

Revision ID: 019_add_tracker_default_template
Revises: 018_add_c411_categories
Create Date: 2026-01-26 12:00:00.000000

This migration adds a default_template_id column to the trackers table
to allow per-tracker BBCode template selection.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '019_add_tracker_default_template'
down_revision = '018_add_c411_categories'
branch_labels = None
depends_on = None


def column_exists(connection, table_name, column_name):
    """Check if a column exists in a table."""
    inspector = sa.inspect(connection)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add default_template_id column to trackers table."""
    connection = op.get_bind()

    if not column_exists(connection, 'trackers', 'default_template_id'):
        op.add_column(
            'trackers',
            sa.Column('default_template_id', sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    """Remove default_template_id column from trackers table."""
    connection = op.get_bind()

    if column_exists(connection, 'trackers', 'default_template_id'):
        op.drop_column('trackers', 'default_template_id')
