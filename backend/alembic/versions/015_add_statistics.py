"""Add statistics tables

Revision ID: 015_add_statistics
Revises: 014_add_batch_jobs
Create Date: 2026-01-24 22:00:00.000000

This migration creates tables for tracking upload statistics.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '015_add_statistics'
down_revision = '014_add_batch_jobs'
branch_labels = None
depends_on = None


def table_exists(connection, table_name):
    """Check if a table exists."""
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Create statistics tables."""
    connection = op.get_bind()

    # Create daily_statistics table
    if not table_exists(connection, 'daily_statistics'):
        op.create_table(
            'daily_statistics',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
            sa.Column('date', sa.Date(), nullable=False, unique=True),
            sa.Column('total_uploads', sa.Integer(), nullable=False, default=0),
            sa.Column('successful_uploads', sa.Integer(), nullable=False, default=0),
            sa.Column('failed_uploads', sa.Integer(), nullable=False, default=0),
            sa.Column('avg_processing_time_seconds', sa.Float(), nullable=True),
            sa.Column('total_processing_time_seconds', sa.Float(), nullable=False, default=0),
            sa.Column('total_bytes_processed', sa.BigInteger(), nullable=False, default=0),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )

        # Create index on date
        op.create_index('ix_daily_statistics_date', 'daily_statistics', ['date'])

    # Create tracker_statistics table
    if not table_exists(connection, 'tracker_statistics'):
        op.create_table(
            'tracker_statistics',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
            sa.Column('date', sa.Date(), nullable=False),
            sa.Column('tracker_name', sa.String(100), nullable=False),
            sa.Column('total_uploads', sa.Integer(), nullable=False, default=0),
            sa.Column('successful_uploads', sa.Integer(), nullable=False, default=0),
            sa.Column('failed_uploads', sa.Integer(), nullable=False, default=0),
            sa.Column('avg_processing_time_seconds', sa.Float(), nullable=True),
            sa.Column('total_processing_time_seconds', sa.Float(), nullable=False, default=0),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )

        # Create indexes
        op.create_index('ix_tracker_statistics_date', 'tracker_statistics', ['date'])
        op.create_index('ix_tracker_statistics_tracker', 'tracker_statistics', ['tracker_name'])
        op.create_index(
            'ix_tracker_statistics_date_tracker',
            'tracker_statistics',
            ['date', 'tracker_name'],
            unique=True
        )


def downgrade() -> None:
    """Drop statistics tables."""
    connection = op.get_bind()

    if table_exists(connection, 'tracker_statistics'):
        op.drop_index('ix_tracker_statistics_date_tracker', table_name='tracker_statistics')
        op.drop_index('ix_tracker_statistics_tracker', table_name='tracker_statistics')
        op.drop_index('ix_tracker_statistics_date', table_name='tracker_statistics')
        op.drop_table('tracker_statistics')

    if table_exists(connection, 'daily_statistics'):
        op.drop_index('ix_daily_statistics_date', table_name='daily_statistics')
        op.drop_table('daily_statistics')
