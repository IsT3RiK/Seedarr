"""Add v2.1 fields for approval workflow, screenshots, and granular tracker status

Revision ID: 008_add_v21_fields
Revises: 007_add_trackers_table
Create Date: 2026-01-24 12:00:00.000000

This migration adds support for v2.1 features:

1. Approval Workflow:
   - file_entries: approval_requested_at, approved_at, preparing_at, approved_by,
     corrections, final_release_name

2. Screenshots & Image Hosting:
   - file_entries: release_dir, prepared_media_path, screenshot_paths, screenshot_urls
   - settings: imgbb_api_key, auto_resume_after_approval

3. Granular Tracker Status:
   - file_entries: tracker_statuses (JSON with per-tracker status tracking)

4. Category Mapping:
   - trackers: category_mapping (JSON for media_type/resolution -> category_id mapping)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '008_add_v21_fields'
down_revision = '007_add_trackers_table'
branch_labels = None
depends_on = None


def table_exists(connection, table_name):
    """Check if a table exists in the database."""
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def column_exists(connection, table_name, column_name):
    """Check if a column exists in a table."""
    inspector = sa.inspect(connection)
    if not table_exists(connection, table_name):
        return False
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add v2.1 columns to file_entries, settings, and trackers tables."""
    connection = op.get_bind()

    # ==========================================================================
    # FileEntry table - Approval Workflow columns
    # ==========================================================================

    # Approval workflow timestamps
    if not column_exists(connection, 'file_entries', 'approval_requested_at'):
        op.add_column(
            'file_entries',
            sa.Column('approval_requested_at', sa.DateTime(), nullable=True)
        )

    if not column_exists(connection, 'file_entries', 'approved_at'):
        op.add_column(
            'file_entries',
            sa.Column('approved_at', sa.DateTime(), nullable=True)
        )

    if not column_exists(connection, 'file_entries', 'preparing_at'):
        op.add_column(
            'file_entries',
            sa.Column('preparing_at', sa.DateTime(), nullable=True)
        )

    # Approval metadata
    if not column_exists(connection, 'file_entries', 'approved_by'):
        op.add_column(
            'file_entries',
            sa.Column('approved_by', sa.String(length=100), nullable=True)
        )

    if not column_exists(connection, 'file_entries', 'corrections'):
        op.add_column(
            'file_entries',
            sa.Column('corrections', sa.JSON(), nullable=True)
        )

    if not column_exists(connection, 'file_entries', 'final_release_name'):
        op.add_column(
            'file_entries',
            sa.Column('final_release_name', sa.String(length=500), nullable=True)
        )

    # ==========================================================================
    # FileEntry table - Release Structure columns
    # ==========================================================================

    if not column_exists(connection, 'file_entries', 'release_dir'):
        op.add_column(
            'file_entries',
            sa.Column('release_dir', sa.String(length=1000), nullable=True)
        )

    if not column_exists(connection, 'file_entries', 'prepared_media_path'):
        op.add_column(
            'file_entries',
            sa.Column('prepared_media_path', sa.String(length=1000), nullable=True)
        )

    # ==========================================================================
    # FileEntry table - Screenshots columns
    # ==========================================================================

    if not column_exists(connection, 'file_entries', 'screenshot_paths'):
        op.add_column(
            'file_entries',
            sa.Column('screenshot_paths', sa.JSON(), nullable=True)
        )

    if not column_exists(connection, 'file_entries', 'screenshot_urls'):
        op.add_column(
            'file_entries',
            sa.Column('screenshot_urls', sa.JSON(), nullable=True)
        )

    # ==========================================================================
    # FileEntry table - Granular Tracker Status
    # ==========================================================================

    if not column_exists(connection, 'file_entries', 'tracker_statuses'):
        op.add_column(
            'file_entries',
            sa.Column('tracker_statuses', sa.JSON(), nullable=True)
        )

    # ==========================================================================
    # Settings table - Image hosting and approval workflow
    # ==========================================================================

    if not column_exists(connection, 'settings', 'imgbb_api_key'):
        op.add_column(
            'settings',
            sa.Column('imgbb_api_key', sa.String(length=200), nullable=True)
        )

    if not column_exists(connection, 'settings', 'auto_resume_after_approval'):
        op.add_column(
            'settings',
            sa.Column('auto_resume_after_approval', sa.Boolean(), nullable=True, default=True)
        )
        # Set default value for existing rows
        connection.execute(
            sa.text("UPDATE settings SET auto_resume_after_approval = 1 WHERE auto_resume_after_approval IS NULL")
        )

    # Prowlarr integration
    if not column_exists(connection, 'settings', 'prowlarr_url'):
        op.add_column(
            'settings',
            sa.Column('prowlarr_url', sa.String(length=500), nullable=True)
        )

    if not column_exists(connection, 'settings', 'prowlarr_api_key'):
        op.add_column(
            'settings',
            sa.Column('prowlarr_api_key', sa.String(length=200), nullable=True)
        )

    # ==========================================================================
    # Trackers table - Category mapping
    # ==========================================================================

    if not column_exists(connection, 'trackers', 'category_mapping'):
        op.add_column(
            'trackers',
            sa.Column('category_mapping', sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    """Remove v2.1 columns from file_entries, settings, and trackers tables."""
    connection = op.get_bind()

    # Remove trackers columns
    if column_exists(connection, 'trackers', 'category_mapping'):
        op.drop_column('trackers', 'category_mapping')

    # Remove settings columns
    if column_exists(connection, 'settings', 'prowlarr_api_key'):
        op.drop_column('settings', 'prowlarr_api_key')

    if column_exists(connection, 'settings', 'prowlarr_url'):
        op.drop_column('settings', 'prowlarr_url')

    if column_exists(connection, 'settings', 'auto_resume_after_approval'):
        op.drop_column('settings', 'auto_resume_after_approval')

    if column_exists(connection, 'settings', 'imgbb_api_key'):
        op.drop_column('settings', 'imgbb_api_key')

    # Remove file_entries columns - Tracker status
    if column_exists(connection, 'file_entries', 'tracker_statuses'):
        op.drop_column('file_entries', 'tracker_statuses')

    # Remove file_entries columns - Screenshots
    if column_exists(connection, 'file_entries', 'screenshot_urls'):
        op.drop_column('file_entries', 'screenshot_urls')

    if column_exists(connection, 'file_entries', 'screenshot_paths'):
        op.drop_column('file_entries', 'screenshot_paths')

    # Remove file_entries columns - Release structure
    if column_exists(connection, 'file_entries', 'prepared_media_path'):
        op.drop_column('file_entries', 'prepared_media_path')

    if column_exists(connection, 'file_entries', 'release_dir'):
        op.drop_column('file_entries', 'release_dir')

    # Remove file_entries columns - Approval workflow
    if column_exists(connection, 'file_entries', 'final_release_name'):
        op.drop_column('file_entries', 'final_release_name')

    if column_exists(connection, 'file_entries', 'corrections'):
        op.drop_column('file_entries', 'corrections')

    if column_exists(connection, 'file_entries', 'approved_by'):
        op.drop_column('file_entries', 'approved_by')

    if column_exists(connection, 'file_entries', 'preparing_at'):
        op.drop_column('file_entries', 'preparing_at')

    if column_exists(connection, 'file_entries', 'approved_at'):
        op.drop_column('file_entries', 'approved_at')

    if column_exists(connection, 'file_entries', 'approval_requested_at'):
        op.drop_column('file_entries', 'approval_requested_at')
