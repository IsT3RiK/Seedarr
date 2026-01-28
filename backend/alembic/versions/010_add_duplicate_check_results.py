"""Add duplicate_check_results column to file_entries table

Revision ID: 010_add_duplicate_check_results
Revises: 009_add_upload_config
Create Date: 2026-01-24 19:50:00.000000

This migration adds the duplicate_check_results JSON column to store
persisted duplicate check results per release.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '010_add_duplicate_check_results'
down_revision = '009_add_upload_config'
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
    """Add duplicate_check_results column to file_entries table."""
    connection = op.get_bind()

    if not column_exists(connection, 'file_entries', 'duplicate_check_results'):
        op.add_column(
            'file_entries',
            sa.Column('duplicate_check_results', sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    """Remove duplicate_check_results column from file_entries table."""
    connection = op.get_bind()

    if column_exists(connection, 'file_entries', 'duplicate_check_results'):
        op.drop_column('file_entries', 'duplicate_check_results')
