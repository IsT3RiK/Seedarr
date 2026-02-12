"""Add qbittorrent_content_path setting

Revision ID: 025_add_qbittorrent_content_path
Revises: 024_migrate_adapter_types
Create Date: 2026-02-12 12:00:00.000000

Adds qbittorrent_content_path column to settings table.
This allows mapping Seedarr's internal paths to qBittorrent's mount paths
(e.g., Seedarr /media -> qBit /data).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '025_add_qbittorrent_content_path'
down_revision = '024_migrate_adapter_types'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('settings') as batch_op:
        batch_op.add_column(
            sa.Column('qbittorrent_content_path', sa.String(1000), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('settings') as batch_op:
        batch_op.drop_column('qbittorrent_content_path')
