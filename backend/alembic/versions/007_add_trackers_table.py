"""Add Trackers table for multi-tracker support

Revision ID: 007_add_trackers_table
Revises: 006_add_categories_table
Create Date: 2026-01-24 10:00:00.000000

This migration adds support for multiple trackers:
1. Creates the trackers table for tracker configuration
2. Adds torrent_paths and upload_results JSON columns to file_entries
   for multi-tracker torrent generation and upload tracking
3. Migrates existing Settings tracker configuration to a default La Cale tracker

Multi-Tracker Architecture:
    - Each tracker has its own authentication, piece size strategy, and adapter
    - File entries track torrent paths and upload results per tracker
    - Pipeline generates one torrent per enabled tracker
    - Upload stage iterates through enabled trackers
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '007_add_trackers_table'
down_revision = '006_add_categories_table'
branch_labels = None
depends_on = None


def table_exists(connection, table_name):
    """Check if a table exists in the database."""
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def column_exists(connection, table_name, column_name):
    """Check if a column exists in a table."""
    inspector = sa.inspect(connection)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def index_exists(connection, table_name, index_name):
    """Check if an index exists."""
    inspector = sa.inspect(connection)
    indexes = inspector.get_indexes(table_name)
    return any(idx['name'] == index_name for idx in indexes)


def upgrade() -> None:
    """
    Create trackers table and add multi-tracker columns to file_entries.
    """
    connection = op.get_bind()

    # Create trackers table if it doesn't exist
    if not table_exists(connection, 'trackers'):
        op.create_table(
            'trackers',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            # Identity
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('slug', sa.String(length=50), nullable=False),
            sa.Column('tracker_url', sa.String(length=500), nullable=False),
            # Authentication
            sa.Column('passkey', sa.String(length=500), nullable=True),
            sa.Column('api_key', sa.String(length=500), nullable=True),
            # Torrent configuration
            sa.Column('source_flag', sa.String(length=50), nullable=True),
            sa.Column('piece_size_strategy', sa.String(length=20), nullable=True, default='auto'),
            sa.Column('announce_url_template', sa.String(length=500), nullable=True),
            # Upload configuration
            sa.Column('adapter_type', sa.String(length=50), nullable=True, default='generic'),
            sa.Column('default_category_id', sa.String(length=50), nullable=True),
            sa.Column('default_subcategory_id', sa.String(length=50), nullable=True),
            # Options
            sa.Column('requires_cloudflare', sa.Boolean(), nullable=True, default=False),
            sa.Column('upload_enabled', sa.Boolean(), nullable=True, default=True),
            sa.Column('priority', sa.Integer(), nullable=True, default=0),
            sa.Column('enabled', sa.Boolean(), nullable=True, default=True),
            # Timestamps
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )

        # Create indexes for trackers table
        op.create_index('idx_trackers_name', 'trackers', ['name'], unique=True)
        op.create_index('idx_trackers_slug', 'trackers', ['slug'], unique=True)
        op.create_index('idx_trackers_enabled', 'trackers', ['enabled'])
        op.create_index('idx_trackers_priority', 'trackers', ['priority'])
    else:
        # Table exists but indexes might not - create them if missing
        if not index_exists(connection, 'trackers', 'idx_trackers_name'):
            op.create_index('idx_trackers_name', 'trackers', ['name'], unique=True)
        if not index_exists(connection, 'trackers', 'idx_trackers_slug'):
            op.create_index('idx_trackers_slug', 'trackers', ['slug'], unique=True)
        if not index_exists(connection, 'trackers', 'idx_trackers_enabled'):
            op.create_index('idx_trackers_enabled', 'trackers', ['enabled'])
        if not index_exists(connection, 'trackers', 'idx_trackers_priority'):
            op.create_index('idx_trackers_priority', 'trackers', ['priority'])

    # Add multi-tracker columns to file_entries if they don't exist
    if not column_exists(connection, 'file_entries', 'torrent_paths'):
        op.add_column(
            'file_entries',
            sa.Column('torrent_paths', sa.JSON(), nullable=True)
        )

    if not column_exists(connection, 'file_entries', 'upload_results'):
        op.add_column(
            'file_entries',
            sa.Column('upload_results', sa.JSON(), nullable=True)
        )

    # Migrate existing Settings tracker configuration to trackers table
    # This creates a default "La Cale" tracker from existing settings
    try:
        # Check if La Cale tracker already exists
        result = connection.execute(
            sa.text("SELECT COUNT(*) FROM trackers WHERE slug = 'lacale'")
        )
        lacale_count = result.scalar()

        if lacale_count == 0:
            # Check if settings table exists and has tracker configuration
            result = connection.execute(
                sa.text("SELECT tracker_url, tracker_passkey FROM settings WHERE id = 1")
            )
            row = result.fetchone()

            if row and row[0] and row[1]:
                # Insert La Cale tracker from existing settings
                now = datetime.utcnow().isoformat()
                connection.execute(
                    sa.text("""
                        INSERT INTO trackers (
                            name, slug, tracker_url, passkey, source_flag,
                            piece_size_strategy, adapter_type, requires_cloudflare,
                            upload_enabled, priority, enabled, created_at, updated_at
                        ) VALUES (
                            'La Cale', 'lacale', :tracker_url, :passkey, 'lacale',
                            'auto', 'lacale', 1,
                            1, 0, 1, :created_at, :updated_at
                        )
                    """),
                    {
                        'tracker_url': row[0],
                        'passkey': row[1],
                        'created_at': now,
                        'updated_at': now
                    }
                )
    except Exception:
        # Settings table might not exist or have different structure
        # Skip migration of existing settings
        pass


def downgrade() -> None:
    """Remove trackers table and multi-tracker columns from file_entries."""
    connection = op.get_bind()

    # Remove multi-tracker columns from file_entries if they exist
    if column_exists(connection, 'file_entries', 'upload_results'):
        op.drop_column('file_entries', 'upload_results')

    if column_exists(connection, 'file_entries', 'torrent_paths'):
        op.drop_column('file_entries', 'torrent_paths')

    # Drop trackers table if it exists
    if table_exists(connection, 'trackers'):
        # Drop indexes first
        if index_exists(connection, 'trackers', 'idx_trackers_priority'):
            op.drop_index('idx_trackers_priority', table_name='trackers')
        if index_exists(connection, 'trackers', 'idx_trackers_enabled'):
            op.drop_index('idx_trackers_enabled', table_name='trackers')
        if index_exists(connection, 'trackers', 'idx_trackers_slug'):
            op.drop_index('idx_trackers_slug', table_name='trackers')
        if index_exists(connection, 'trackers', 'idx_trackers_name'):
            op.drop_index('idx_trackers_name', table_name='trackers')

        # Drop table
        op.drop_table('trackers')
