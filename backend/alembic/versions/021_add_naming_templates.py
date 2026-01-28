"""Add naming_template to trackers and tracker_release_names to file_entries

Revision ID: 021_add_naming_templates
Revises: 020_add_c411_bbcode_template
Create Date: 2026-01-28 10:00:00.000000

This migration adds:
- naming_template column to trackers table for per-tracker release name formatting
- tracker_release_names column to file_entries table to store computed names per tracker

The naming_template allows trackers to define their own naming convention using variables:
- {titre}, {titre_fr}, {titre_en}, {annee}, {langue}, {resolution}
- {source}, {codec_audio}, {codec_video}, {group}, {hdr}, {saison}, {episode}

Example: "{titre}.{annee}.{langue}.{resolution}.{source}.{codec_audio}.{codec_video}-{group}"
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '021_add_naming_templates'
down_revision = '020_add_c411_bbcode_template'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add naming_template to trackers and tracker_release_names to file_entries."""
    # Add naming_template column to trackers table
    op.add_column(
        'trackers',
        sa.Column('naming_template', sa.String(500), nullable=True)
    )

    # Add tracker_release_names column to file_entries table
    op.add_column(
        'file_entries',
        sa.Column('tracker_release_names', sa.JSON, nullable=True)
    )


def downgrade() -> None:
    """Remove naming_template from trackers and tracker_release_names from file_entries."""
    op.drop_column('trackers', 'naming_template')
    op.drop_column('file_entries', 'tracker_release_names')
