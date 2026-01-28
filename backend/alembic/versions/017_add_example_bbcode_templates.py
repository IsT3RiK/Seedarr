"""Add example BBCode templates

Revision ID: 017_add_example_bbcode_templates
Revises: 016_add_bbcode_templates
Create Date: 2026-01-25 15:00:00.000000

This migration adds 3 additional example templates:
- Minimaliste: Simple and clean
- Premium: Full featured with cast
- YGG Style: YGGTorrent inspired format
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '017_add_example_bbcode_templates'
down_revision = '016_add_bbcode_templates'
branch_labels = None
depends_on = None


# Template Minimaliste
MINIMAL_TEMPLATE_CONTENT = """[center]
[img]{{poster_url}}[/img]

[b][size=5]{{title}}[/size][/b]
[i]{{year}} - {{genres}}[/i]

{{overview}}

[b]Qualite:[/b] {{quality}} | [b]Taille:[/b] {{file_size}}
[b]Langues:[/b] {{languages}}
[/center]"""


# Template Premium avec casting
PREMIUM_TEMPLATE_CONTENT = """[center]
[img]{{backdrop_url}}[/img]

[size=7][color=#f59e0b][b]{{title}}[/b][/color][/size]
[size=4][i]{{original_title}}[/i][/size]

[color=#9ca3af]{{year}} | {{runtime}} | {{country}}[/color]
[color=#f59e0b]★[/color] {{rating_10}} sur TMDB

[b]Realisateur :[/b] {{director}}
[b]Genres :[/b] {{genres}}

[quote][i]"{{tagline}}"[/i]

{{overview}}[/quote]

[color=#f59e0b][b]━━━ CASTING PRINCIPAL ━━━[/b][/color]

{{cast_1_card}} {{cast_2_card}} {{cast_3_card}}
{{cast_4_card}} {{cast_5_card}} {{cast_6_card}}

[color=#f59e0b][b]━━━ INFORMATIONS TECHNIQUES ━━━[/b][/color]

[b]Qualite :[/b] {{quality}}
[b]Resolution :[/b] {{resolution}}
[b]Format :[/b] {{format}} ({{video_codec}})
[b]HDR :[/b] {{hdr}}
[b]Debit Video :[/b] {{video_bitrate}}

[b]Pistes Audio :[/b]
{{audio_list}}

[b]Sous-titres :[/b] {{subtitles}}
[b]Taille :[/b] {{file_size}}

[url={{tmdb_url}}]Fiche TMDB[/url] | [url={{trailer_url}}]Bande-annonce[/url]
[/center]"""


# Template YGG Style
YGG_TEMPLATE_CONTENT = """[center]
[img]{{poster_url}}[/img]

[size=6][b]{{title}} ({{year}})[/b][/size]
[/center]

[b][color=#00a8ff]■ INFORMATIONS[/color][/b]
[list]
[*] [b]Titre :[/b] {{title}}
[*] [b]Titre Original :[/b] {{original_title}}
[*] [b]Annee :[/b] {{year}}
[*] [b]Duree :[/b] {{runtime}}
[*] [b]Genre :[/b] {{genres}}
[*] [b]Realisateur :[/b] {{director}}
[*] [b]Acteurs :[/b] {{cast_names}}
[*] [b]Note :[/b] {{rating_10}}
[/list]

[b][color=#00a8ff]■ SYNOPSIS[/color][/b]
[quote]{{overview}}[/quote]

[b][color=#00a8ff]■ DETAILS TECHNIQUES[/color][/b]
[list]
[*] [b]Qualite :[/b] {{quality}}
[*] [b]Format :[/b] {{format}}
[*] [b]Codec Video :[/b] {{video_codec}} @ {{video_bitrate}}
[*] [b]Resolution :[/b] {{resolution}}
[*] [b]HDR :[/b] {{hdr}}
[/list]

[b][color=#00a8ff]■ AUDIO[/color][/b]
{{audio_list}}

[b][color=#00a8ff]■ SOUS-TITRES[/color][/b]
{{subtitles}}

[b][color=#00a8ff]■ TAILLE[/color][/b]
{{file_size}}"""


TEMPLATES = [
    {
        "name": "Minimaliste",
        "description": "Template epure avec uniquement les informations essentielles : poster, titre, synopsis et qualite.",
        "content": MINIMAL_TEMPLATE_CONTENT,
    },
    {
        "name": "Premium",
        "description": "Template complet avec backdrop, casting, liens TMDB et bande-annonce. Ideal pour les releases de qualite.",
        "content": PREMIUM_TEMPLATE_CONTENT,
    },
    {
        "name": "YGG Style",
        "description": "Template inspire du format YGGTorrent avec sections colorees et listes structurees.",
        "content": YGG_TEMPLATE_CONTENT,
    },
]


def upgrade() -> None:
    """Add example BBCode templates if they don't exist."""
    connection = op.get_bind()
    now = datetime.utcnow()

    for template in TEMPLATES:
        # Check if template already exists
        result = connection.execute(
            sa.text("SELECT id FROM bbcode_templates WHERE name = :name"),
            {"name": template["name"]}
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
                    "name": template["name"],
                    "description": template["description"],
                    "content": template["content"],
                    "is_default": False,
                    "created_at": now,
                    "updated_at": now,
                }
            )


def downgrade() -> None:
    """Remove the example templates."""
    connection = op.get_bind()

    for template in TEMPLATES:
        connection.execute(
            sa.text("DELETE FROM bbcode_templates WHERE name = :name"),
            {"name": template["name"]}
        )
