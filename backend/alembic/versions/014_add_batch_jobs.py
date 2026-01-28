"""Add batch_jobs table

Revision ID: 014_add_batch_jobs
Revises: 013_add_notifications
Create Date: 2026-01-24 21:30:00.000000

This migration creates the batch_jobs table for batch processing support.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '014_add_batch_jobs'
down_revision = '013_add_notifications'
branch_labels = None
depends_on = None


def table_exists(connection, table_name):
    """Check if a table exists."""
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Create batch_jobs table."""
    connection = op.get_bind()

    if not table_exists(connection, 'batch_jobs'):
        op.create_table(
            'batch_jobs',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
            sa.Column('name', sa.String(255), nullable=True),
            sa.Column('status', sa.String(50), nullable=False, default='pending'),
            sa.Column('file_entry_ids', sa.JSON(), nullable=False),
            sa.Column('total_count', sa.Integer(), nullable=False, default=0),
            sa.Column('processed_count', sa.Integer(), nullable=False, default=0),
            sa.Column('success_count', sa.Integer(), nullable=False, default=0),
            sa.Column('failed_count', sa.Integer(), nullable=False, default=0),
            sa.Column('priority', sa.String(20), nullable=False, default='normal'),
            sa.Column('skip_approval', sa.Integer(), nullable=False, default=0),
            sa.Column('max_concurrent', sa.Integer(), nullable=False, default=2),
            sa.Column('results', sa.JSON(), nullable=True),
            sa.Column('error_summary', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )

        # Create index on status
        op.create_index('ix_batch_jobs_status', 'batch_jobs', ['status'])


def downgrade() -> None:
    """Drop batch_jobs table."""
    connection = op.get_bind()

    if table_exists(connection, 'batch_jobs'):
        op.drop_index('ix_batch_jobs_status', table_name='batch_jobs')
        op.drop_table('batch_jobs')
