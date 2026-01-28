"""Add BBCode templates table

Revision ID: 016_add_bbcode_templates
Revises: 015_add_statistics
Create Date: 2026-01-25 10:00:00.000000

This migration creates the bbcode_templates table for storing
customizable BBCode templates with a default template.
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '016_add_bbcode_templates'
down_revision = '015_add_statistics'
branch_labels = None
depends_on = None


# Default template content (La Cale format)
DEFAULT_TEMPLATE_CONTENT = """[center]
[img]{{poster_url}}[/img]

[size=6][color=#eab308][b]{{title}} ({{year}})[/b][/color][/size]

[b]Note :[/b] {{rating}}
[b]Genre :[/b] {{genres}}

[quote]{{overview}}[/quote]

[color=#eab308][b]--- DETAILS ---[/b][/color]

[b]Qualite :[/b] {{quality}}
[b]Format :[/b] {{format}}
[b]Rendu :[/b] {{hdr}}
[b]Duree :[/b] {{duration}}
[b]Codec Video :[/b] {{video_codec}}

[b]Codec Audio :[/b]
{{audio_list}}

[b]Langues :[/b] {{languages}}
[b]Sous-titres :[/b] {{subtitles}}
[b]Taille :[/b] {{file_size}}

[/center]"""


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


def table_exists(connection, table_name):
    """Check if a table exists."""
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Create bbcode_templates table and insert default template."""
    connection = op.get_bind()

    if not table_exists(connection, 'bbcode_templates'):
        op.create_table(
            'bbcode_templates',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
            sa.Column('name', sa.String(100), nullable=False, unique=True),
            sa.Column('description', sa.String(500), nullable=True),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('is_default', sa.Boolean(), nullable=False, default=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )

        # Create index on name
        op.create_index('ix_bbcode_templates_name', 'bbcode_templates', ['name'])

        # Insert the default template and additional templates
        now = datetime.utcnow()

        # Template 1: La Cale (Default)
        op.execute(
            sa.text(
                """
                INSERT INTO bbcode_templates (name, description, content, is_default, created_at, updated_at)
                VALUES (:name, :description, :content, :is_default, :created_at, :updated_at)
                """
            ).bindparams(
                name="La Cale (Default)",
                description="Template par defaut au format La Cale avec poster, infos TMDB et details techniques MediaInfo.",
                content=DEFAULT_TEMPLATE_CONTENT,
                is_default=True,
                created_at=now,
                updated_at=now,
            )
        )

        # Template 2: Minimaliste
        op.execute(
            sa.text(
                """
                INSERT INTO bbcode_templates (name, description, content, is_default, created_at, updated_at)
                VALUES (:name, :description, :content, :is_default, :created_at, :updated_at)
                """
            ).bindparams(
                name="Minimaliste",
                description="Template epure avec uniquement les informations essentielles : poster, titre, synopsis et qualite.",
                content=MINIMAL_TEMPLATE_CONTENT,
                is_default=False,
                created_at=now,
                updated_at=now,
            )
        )

        # Template 3: Premium
        op.execute(
            sa.text(
                """
                INSERT INTO bbcode_templates (name, description, content, is_default, created_at, updated_at)
                VALUES (:name, :description, :content, :is_default, :created_at, :updated_at)
                """
            ).bindparams(
                name="Premium",
                description="Template complet avec backdrop, casting, liens TMDB et bande-annonce. Ideal pour les releases de qualite.",
                content=PREMIUM_TEMPLATE_CONTENT,
                is_default=False,
                created_at=now,
                updated_at=now,
            )
        )

        # Template 4: YGG Style
        op.execute(
            sa.text(
                """
                INSERT INTO bbcode_templates (name, description, content, is_default, created_at, updated_at)
                VALUES (:name, :description, :content, :is_default, :created_at, :updated_at)
                """
            ).bindparams(
                name="YGG Style",
                description="Template inspire du format YGGTorrent avec sections colorees et listes structurees.",
                content=YGG_TEMPLATE_CONTENT,
                is_default=False,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    """Drop bbcode_templates table."""
    connection = op.get_bind()

    if table_exists(connection, 'bbcode_templates'):
        op.drop_index('ix_bbcode_templates_name', table_name='bbcode_templates')
        op.drop_table('bbcode_templates')
