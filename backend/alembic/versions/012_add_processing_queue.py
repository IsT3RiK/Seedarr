"""Add processing_queue table

Revision ID: 012_add_processing_queue
Revises: 011_add_rate_limit_settings
Create Date: 2026-01-24 20:30:00.000000

This migration creates the processing_queue table for persistent
queue-based processing with priority support.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '012_add_processing_queue'
down_revision = '011_add_rate_limit_settings'
branch_labels = None
depends_on = None


def table_exists(connection, table_name):
    """Check if a table exists."""
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Create processing_queue table."""
    connection = op.get_bind()

    if not table_exists(connection, 'processing_queue'):
        op.create_table(
            'processing_queue',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
            sa.Column('file_entry_id', sa.Integer(), sa.ForeignKey('file_entries.id'), nullable=False),
            sa.Column('priority', sa.String(20), nullable=False, default='normal'),
            sa.Column('status', sa.String(20), nullable=False, default='pending'),
            sa.Column('attempts', sa.Integer(), nullable=False, default=0),
            sa.Column('max_attempts', sa.Integer(), nullable=False, default=3),
            sa.Column('last_error', sa.String(2000), nullable=True),
            sa.Column('skip_approval', sa.Integer(), nullable=False, default=0),
            sa.Column('added_at', sa.DateTime(), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )

        # Create indexes
        op.create_index('ix_processing_queue_file_entry_id', 'processing_queue', ['file_entry_id'])
        op.create_index('ix_processing_queue_status', 'processing_queue', ['status'])


def downgrade() -> None:
    """Drop processing_queue table."""
    connection = op.get_bind()

    if table_exists(connection, 'processing_queue'):
        op.drop_index('ix_processing_queue_status', table_name='processing_queue')
        op.drop_index('ix_processing_queue_file_entry_id', table_name='processing_queue')
        op.drop_table('processing_queue')
