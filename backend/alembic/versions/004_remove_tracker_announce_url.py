"""Remove tracker_announce_url column - now computed from tracker_url + passkey

Revision ID: 004_remove_tracker_announce_url
Revises: 003_add_tags_table
Create Date: 2026-01-14 12:00:00.000000

This migration removes the tracker_announce_url column from the settings table.
The announce URL is now computed dynamically as a property:

    announce_url = f"{tracker_url}/announce?passkey={tracker_passkey}"

Benefits:
    - Eliminates redundant data storage
    - Prevents inconsistency between passkey and announce URL
    - Simplifies configuration (2 fields instead of 3)
    - Passkey changes automatically reflected in announce URL
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004_remove_tracker_announce_url'
down_revision = '003_add_tags_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Remove tracker_announce_url column from settings table.

    The announce URL is now computed dynamically from tracker_url and tracker_passkey.
    """
    # SQLite doesn't support DROP COLUMN directly, need to handle this
    # For SQLite, we'll use batch mode
    with op.batch_alter_table('settings') as batch_op:
        batch_op.drop_column('tracker_announce_url')


def downgrade() -> None:
    """
    Re-add tracker_announce_url column to settings table.

    Note: The column will be empty after downgrade. Users will need to
    manually configure the announce URL if reverting.
    """
    with op.batch_alter_table('settings') as batch_op:
        batch_op.add_column(
            sa.Column('tracker_announce_url', sa.String(length=500), nullable=True)
        )
