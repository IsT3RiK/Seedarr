"""Add rate limit settings to settings table

Revision ID: 011_add_rate_limit_settings
Revises: 010_add_duplicate_check_results
Create Date: 2026-01-24 20:00:00.000000

This migration adds rate limiting configuration fields to the settings table.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '011_add_rate_limit_settings'
down_revision = '010_add_duplicate_check_results'
branch_labels = None
depends_on = None


def column_exists(connection, table_name, column_name):
    """Check if a column exists in a table."""
    inspector = sa.inspect(connection)
    if table_name not in inspector.get_table_names():
        return False
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add rate limit columns to settings table."""
    connection = op.get_bind()

    # Add TMDB rate limit setting
    if not column_exists(connection, 'settings', 'tmdb_rate_limit'):
        op.add_column(
            'settings',
            sa.Column('tmdb_rate_limit', sa.Integer(), nullable=True, default=40)
        )

    # Add tracker rate limit setting
    if not column_exists(connection, 'settings', 'tracker_rate_limit'):
        op.add_column(
            'settings',
            sa.Column('tracker_rate_limit', sa.Integer(), nullable=True, default=10)
        )


def downgrade() -> None:
    """Remove rate limit columns from settings table."""
    connection = op.get_bind()

    if column_exists(connection, 'settings', 'tmdb_rate_limit'):
        op.drop_column('settings', 'tmdb_rate_limit')

    if column_exists(connection, 'settings', 'tracker_rate_limit'):
        op.drop_column('settings', 'tracker_rate_limit')
