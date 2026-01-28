"""
BBCode Template Model for Seedarr v2.0

This module defines the BBCodeTemplate model for storing customizable
BBCode templates that users can create and use for torrent presentations.
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import Session

from .base import Base


class BBCodeTemplate(Base):
    """
    BBCode template for customizable torrent presentations.

    Attributes:
        id: Primary key
        name: Unique template name
        description: Optional description
        content: BBCode content with placeholders like {{title}}, {{year}}
        is_default: Whether this is the default template
        created_at: Creation timestamp
        updated_at: Last update timestamp

    Placeholders:
        TMDB Data:
            {{title}} - Movie/show title
            {{original_title}} - Original title
            {{year}} - Release year
            {{poster_url}} - Full poster URL
            {{rating}} - TMDB rating (e.g., 7.5/10)
            {{genres}} - Comma-separated genres
            {{overview}} - Synopsis/description
            {{tmdb_id}} - TMDB ID
            {{imdb_id}} - IMDB ID

        MediaInfo Data:
            {{quality}} - Quality string (e.g., "1080p BluRay (Full HD)")
            {{format}} - Container + codec (e.g., "MKV (HEVC Main 10)")
            {{hdr}} - HDR type (e.g., "Dolby Vision / HDR10")
            {{duration}} - Duration
            {{video_codec}} - Video codec with bitrate
            {{audio_list}} - Formatted audio tracks list
            {{languages}} - Available languages
            {{subtitles}} - Available subtitles
            {{file_size}} - File size
    """

    __tablename__ = 'bbcode_templates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(500), nullable=True)
    content = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<BBCodeTemplate(id={self.id}, name='{self.name}', is_default={self.is_default})>"

    @classmethod
    def get_default(cls, db: Session) -> Optional["BBCodeTemplate"]:
        """Get the default template."""
        return db.query(cls).filter(cls.is_default == True).first()

    @classmethod
    def get_all(cls, db: Session) -> List["BBCodeTemplate"]:
        """Get all templates ordered by name."""
        return db.query(cls).order_by(cls.name).all()

    @classmethod
    def get_by_id(cls, db: Session, template_id: int) -> Optional["BBCodeTemplate"]:
        """Get a template by ID."""
        return db.query(cls).filter(cls.id == template_id).first()

    @classmethod
    def get_by_name(cls, db: Session, name: str) -> Optional["BBCodeTemplate"]:
        """Get a template by name."""
        return db.query(cls).filter(cls.name == name).first()

    def set_as_default(self, db: Session) -> None:
        """Set this template as the default (unsets other defaults)."""
        # Unset all other defaults
        db.query(BBCodeTemplate).filter(
            BBCodeTemplate.id != self.id
        ).update({BBCodeTemplate.is_default: False})

        # Set this as default
        self.is_default = True
        db.commit()

    @classmethod
    def get_available_variables(cls) -> dict:
        """Return the list of available template variables with descriptions."""
        return {
            "tmdb": {
                "title": "Titre du film/serie",
                "original_title": "Titre original",
                "year": "Annee de sortie",
                "release_date": "Date de sortie (ex: mardi 13 janvier 2026)",
                "poster_url": "URL du poster (auto [img])",
                "backdrop_url": "URL du backdrop (auto [img])",
                "rating": "Note TMDB (ex: 7.5)",
                "rating_10": "Note sur 10 (ex: 7.5/10)",
                "genres": "Genres separes par virgule",
                "overview": "Synopsis/description",
                "tagline": "Slogan du film",
                "runtime": "Duree (ex: 1h et 53min)",
                "country": "Pays d'origine",
                "director": "Realisateur(s)",
                "tmdb_id": "ID TMDB",
                "imdb_id": "ID IMDB",
                "tmdb_url": "Lien fiche TMDB (auto [url])",
                "trailer_url": "Lien bande-annonce YouTube (auto [url])",
            },
            "cast": {
                "cast_names": "Liste des acteurs (6 premiers)",
                "cast_1_card": "Acteur 1 - Photo + Nom (inline)",
                "cast_1_name": "Acteur 1 - Nom",
                "cast_1_character": "Acteur 1 - Personnage",
                "cast_2_card": "Acteur 2 - Photo + Nom (inline)",
                "cast_2_name": "Acteur 2 - Nom",
                "cast_2_character": "Acteur 2 - Personnage",
                "cast_3_card": "Acteur 3 - Photo + Nom (inline)",
                "cast_3_name": "Acteur 3 - Nom",
                "cast_3_character": "Acteur 3 - Personnage",
                "cast_4_card": "Acteur 4 - Photo + Nom (inline)",
                "cast_4_name": "Acteur 4 - Nom",
                "cast_4_character": "Acteur 4 - Personnage",
                "cast_5_card": "Acteur 5 - Photo + Nom (inline)",
                "cast_5_name": "Acteur 5 - Nom",
                "cast_5_character": "Acteur 5 - Personnage",
                "cast_6_card": "Acteur 6 - Photo + Nom (inline)",
                "cast_6_name": "Acteur 6 - Nom",
                "cast_6_character": "Acteur 6 - Personnage",
            },
            "mediainfo": {
                "quality": "Qualite (ex: 1080p BluRay)",
                "format": "Format conteneur (ex: MKV)",
                "video_codec": "Codec video (ex: HEVC/H.265)",
                "video_bitrate": "Debit video (ex: 15.0 Mb/s)",
                "hdr": "Type HDR (ex: Dolby Vision / HDR10)",
                "resolution": "Resolution (ex: 1920x1080)",
                "duration": "Duree MediaInfo",
                "audio_list": "Liste des pistes audio (texte)",
                "audio_table": "Tableau BBCode des pistes audio",
                "languages": "Langues disponibles",
                "subtitles": "Sous-titres disponibles (texte)",
                "subtitles_table": "Tableau BBCode des sous-titres",
                "file_size": "Taille du fichier",
                "file_count": "Nombre de fichiers",
                "source": "Source/Release",
                "release_name": "Nom complet de la release",
                "release_team": "Nom de la team (ex: ROMKENT)",
            }
        }
