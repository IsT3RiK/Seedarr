"""Add checkpoint fields to FileEntry model

Revision ID: 001_add_checkpoint_fields
Revises:
Create Date: 2026-01-06 12:26:00.000000

This migration adds pipeline checkpoint timestamp fields to the file_entries table
to enable idempotent pipeline processing. These fields track completion of each
pipeline stage, allowing the system to resume from the last successful checkpoint
on failure/retry.

Checkpoint fields added:
    - scanned_at: Timestamp when file scan completed
    - analyzed_at: Timestamp when MediaInfo analysis completed
    - renamed_at: Timestamp when file rename completed
    - metadata_generated_at: Timestamp when .torrent and NFO generation completed
    - uploaded_at: Timestamp when tracker upload completed
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001_add_checkpoint_fields'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add pipeline checkpoint timestamp fields to file_entries table.

    These fields enable idempotent pipeline operations by tracking
    which stages have been completed for each file entry.
    """
    # Add checkpoint timestamp columns
    op.add_column('file_entries', sa.Column('scanned_at', sa.DateTime(), nullable=True))
    op.add_column('file_entries', sa.Column('analyzed_at', sa.DateTime(), nullable=True))
    op.add_column('file_entries', sa.Column('renamed_at', sa.DateTime(), nullable=True))
    op.add_column('file_entries', sa.Column('metadata_generated_at', sa.DateTime(), nullable=True))
    op.add_column('file_entries', sa.Column('uploaded_at', sa.DateTime(), nullable=True))

    # Create indexes for efficient querying by checkpoint status
    op.create_index('idx_file_entries_scanned_at', 'file_entries', ['scanned_at'])
    op.create_index('idx_file_entries_uploaded_at', 'file_entries', ['uploaded_at'])


def downgrade() -> None:
    """
    Remove pipeline checkpoint timestamp fields from file_entries table.

    WARNING: This will delete all checkpoint data, preventing pipeline
    resumption functionality.
    """
    # Drop indexes
    op.drop_index('idx_file_entries_uploaded_at', table_name='file_entries')
    op.drop_index('idx_file_entries_scanned_at', table_name='file_entries')

    # Drop checkpoint columns
    op.drop_column('file_entries', 'uploaded_at')
    op.drop_column('file_entries', 'metadata_generated_at')
    op.drop_column('file_entries', 'renamed_at')
    op.drop_column('file_entries', 'analyzed_at')
    op.drop_column('file_entries', 'scanned_at')
