"""Add radarr/sonarr settings

Revision ID: 026_add_radarr_sonarr_settings
Revises: 025_add_qbittorrent_content_path
Create Date: 2026-02-19 12:00:00.000000

Adds radarr_url, radarr_api_key, sonarr_url, sonarr_api_key columns to settings table.
These fields enable Radarr/Sonarr integration for sceneName lookup.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '026_add_radarr_sonarr_settings'
down_revision = '025_add_qbittorrent_content_path'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('settings') as batch_op:
        batch_op.add_column(sa.Column('radarr_url', sa.String(500), nullable=True))
        batch_op.add_column(sa.Column('radarr_api_key', sa.String(200), nullable=True))
        batch_op.add_column(sa.Column('sonarr_url', sa.String(500), nullable=True))
        batch_op.add_column(sa.Column('sonarr_api_key', sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('settings') as batch_op:
        batch_op.drop_column('sonarr_api_key')
        batch_op.drop_column('sonarr_url')
        batch_op.drop_column('radarr_api_key')
        batch_op.drop_column('radarr_url')
