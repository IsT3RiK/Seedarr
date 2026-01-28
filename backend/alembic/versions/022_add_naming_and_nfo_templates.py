"""Add naming_templates and nfo_templates tables

Revision ID: 022_add_naming_and_nfo_templates
Revises: 021_add_naming_templates
Create Date: 2026-01-28 12:00:00.000000

This migration adds:
- naming_templates table for storing release name format templates
- nfo_templates table for storing NFO file templates
- Default templates for both types

These templates are used by the unified Templates page to manage
all template types in one place.
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '022_add_naming_and_nfo_templates'
down_revision = '021_add_naming_templates'
branch_labels = None
depends_on = None


# Default NFO template content
DEFAULT_NFO_TEMPLATE = """-------------------------------------------------------------------------------
                             INFORMATION GENERALE
-------------------------------------------------------------------------------
Type.................: {{media_type}}

-------------------------------------------------------------------------------
                               RESUME TECHNIQUE
-------------------------------------------------------------------------------
Source...............: {{source}}
Resolution...........: {{resolution_label}}
Codec Video..........: {{video_codec}}
Codec Audio..........: {{audio_codec}}

-------------------------------------------------------------------------------
                              DETAILS TECHNIQUES
-------------------------------------------------------------------------------
-------------------------------------------------------------------------------
                                 GENERAL INFO
-------------------------------------------------------------------------------
File Name............: {{release_name}}
Format...............: {{format}}
File Size............: {{file_size}}
Duration.............: {{duration}}
Overall Bitrate......: {{overall_bitrate}}

-------------------------------------------------------------------------------
                                 VIDEO INFO
-------------------------------------------------------------------------------
Format...............: {{video_format}}
Bitrate..............: {{video_bitrate}}
Resolution...........: {{resolution}}
Frame Rate...........: {{frame_rate}}
Bit Depth............: {{bit_depth}}
HDR Format...........: {{hdr_format}}

-------------------------------------------------------------------------------
                                 AUDIO INFO
-------------------------------------------------------------------------------
{{audio_list}}

-------------------------------------------------------------------------------
                                   SUBTITLES
-------------------------------------------------------------------------------
{{subtitle_list}}

-------------------------------------------------------------------------------
                             Partager & Preserver
-------------------------------------------------------------------------------
"""


def upgrade() -> None:
    """Create naming_templates and nfo_templates tables."""
    # Create naming_templates table
    op.create_table(
        'naming_templates',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('template', sa.Text, nullable=False),
        sa.Column('is_default', sa.Boolean, nullable=False, default=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
    )

    # Create nfo_templates table
    op.create_table(
        'nfo_templates',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('is_default', sa.Boolean, nullable=False, default=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
    )

    # Insert default naming templates
    connection = op.get_bind()
    now = datetime.utcnow()

    # Default naming template (Standard Scene)
    connection.execute(
        sa.text("""
            INSERT INTO naming_templates (name, description, template, is_default, created_at, updated_at)
            VALUES (:name, :description, :template, :is_default, :created_at, :updated_at)
        """),
        {
            "name": "Standard Scene",
            "description": "Format scene standard: Titre.Annee.Langue.Resolution.Source.Codec-GROUPE",
            "template": "{titre}.{annee}.{langue}.{resolution}.{source}.{codec_video}-{group}",
            "is_default": True,
            "created_at": now,
            "updated_at": now,
        }
    )

    # Additional naming templates
    connection.execute(
        sa.text("""
            INSERT INTO naming_templates (name, description, template, is_default, created_at, updated_at)
            VALUES (:name, :description, :template, :is_default, :created_at, :updated_at)
        """),
        {
            "name": "Complet",
            "description": "Format complet avec codec audio",
            "template": "{titre}.{annee}.{langue}.{resolution}.{source}.{codec_audio}.{codec_video}-{group}",
            "is_default": False,
            "created_at": now,
            "updated_at": now,
        }
    )

    connection.execute(
        sa.text("""
            INSERT INTO naming_templates (name, description, template, is_default, created_at, updated_at)
            VALUES (:name, :description, :template, :is_default, :created_at, :updated_at)
        """),
        {
            "name": "Serie TV",
            "description": "Format pour series TV avec saison/episode",
            "template": "{titre}.{saison}{episode}.{langue}.{resolution}.{source}.{codec_video}-{group}",
            "is_default": False,
            "created_at": now,
            "updated_at": now,
        }
    )

    # Default NFO template
    connection.execute(
        sa.text("""
            INSERT INTO nfo_templates (name, description, content, is_default, created_at, updated_at)
            VALUES (:name, :description, :content, :is_default, :created_at, :updated_at)
        """),
        {
            "name": "Standard",
            "description": "Template NFO standard avec toutes les informations techniques",
            "content": DEFAULT_NFO_TEMPLATE,
            "is_default": True,
            "created_at": now,
            "updated_at": now,
        }
    )


def downgrade() -> None:
    """Drop naming_templates and nfo_templates tables."""
    op.drop_table('nfo_templates')
    op.drop_table('naming_templates')
