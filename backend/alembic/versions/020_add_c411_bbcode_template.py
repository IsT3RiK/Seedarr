"""Add C411 BBCode template

Revision ID: 020_add_c411_bbcode_template
Revises: 019_add_tracker_default_template
Create Date: 2026-01-26 10:00:00.000000

This migration adds a C411-specific template that includes all required fields:
- Resolution (from MediaInfo)
- Video bitrate
- File count
- All standard TMDB and technical info
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '020_add_c411_bbcode_template'
down_revision = '019_add_tracker_default_template'
branch_labels = None
depends_on = None


# C411 Template - Full format matching tracker requirements
C411_TEMPLATE_CONTENT = """[center]
[img]{{poster_url}}[/img]

[size=6][color=#eab308][b]{{title}} ({{year}})[/b][/color][/size]

[b]Note :[/b] {{rating_10}}
[b]Genre :[/b] {{genres}}

[quote]{{overview}}[/quote]

[color=#eab308][b]--- DETAILS ---[/b][/color]

[b]Qualite :[/b] {{quality}}
[b]Resolution :[/b] {{resolution}}
[b]Format :[/b] {{format}}
[b]Rendu :[/b] {{hdr}}
[b]Duree :[/b] {{duration}}
[b]Codec Video :[/b] {{video_codec}}
[b]Debit Video :[/b] {{video_bitrate}}

[b]Codec Audio :[/b]
{{audio_list}}

[b]Langues :[/b] {{languages}}
[b]Sous-titres :[/b] {{subtitles}}
[b]Taille :[/b] {{file_size}}
[b]Fichiers :[/b] {{file_count}}

[/center]"""


def upgrade() -> None:
    """Add C411 BBCode template if it doesn't exist."""
    connection = op.get_bind()
    now = datetime.utcnow()

    # Check if template already exists
    result = connection.execute(
        sa.text("SELECT id FROM bbcode_templates WHERE name = :name"),
        {"name": "C411"}
    ).fetchone()

    if result is None:
        # Insert the template
        connection.execute(
            sa.text(
                """
                INSERT INTO bbcode_templates (name, description, content, is_default, created_at, updated_at)
                VALUES (:name, :description, :content, :is_default, :created_at, :updated_at)
                """
            ),
            {
                "name": "C411",
                "description": "Template complet pour C411 avec resolution, bitrate video et nombre de fichiers. Conforme aux exigences du tracker.",
                "content": C411_TEMPLATE_CONTENT,
                "is_default": False,
                "created_at": now,
                "updated_at": now,
            }
        )


def downgrade() -> None:
    """Remove the C411 template."""
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM bbcode_templates WHERE name = :name"),
        {"name": "C411"}
    )
