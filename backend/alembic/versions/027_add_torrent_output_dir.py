"""Add torrent_output_dir setting

Revision ID: 027_add_torrent_output_dir
Revises: 026_add_radarr_sonarr_settings
Create Date: 2026-02-19 18:00:00.000000

Adds torrent_output_dir column to settings table.
Dedicated folder for .torrent files, defaults to {output_dir}/.torrents.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '027_add_torrent_output_dir'
down_revision = '026_add_radarr_sonarr_settings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('settings') as batch_op:
        batch_op.add_column(sa.Column('torrent_output_dir', sa.String(1000), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('settings') as batch_op:
        batch_op.drop_column('torrent_output_dir')
