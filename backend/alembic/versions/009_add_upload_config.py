"""Add upload_config JSON column to trackers table

Revision ID: 009_add_upload_config
Revises: 008_add_v21_fields
Create Date: 2026-01-24 18:30:00.000000

This migration adds the upload_config JSON column to the trackers table
for the configurable uploader system.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '009_add_upload_config'
down_revision = '008_add_v21_fields'
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
    """Add upload_config column to trackers table."""
    connection = op.get_bind()

    if not column_exists(connection, 'trackers', 'upload_config'):
        op.add_column(
            'trackers',
            sa.Column('upload_config', sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    """Remove upload_config column from trackers table."""
    connection = op.get_bind()

    if column_exists(connection, 'trackers', 'upload_config'):
        op.drop_column('trackers', 'upload_config')
