"""
Naming Template Model for Seedarr v2.0

This module defines the NamingTemplate model for storing customizable
release name templates that can be assigned to trackers.

Template Variables:
    Titre:
    {titre} - Normalized title (dots instead of spaces)
    {titre_fr} - French title if available
    {titre_en} - English/original title
    {titre_lower} - Title in lowercase
    {titre_fr_lower} - French title in lowercase
    {titre_en_lower} - English title in lowercase

    Media:
    {annee} - Release year
    {langue} - Language (FRENCH, MULTi, etc.)
    {vff} - VFF/VFQ indicator (when French audio present)
    {resolution} - Video resolution (1080p, 2160p, etc.)
    {source} - Source (WEB, BluRay, etc.)
    {quality} - Quality indicator (HDLight, REMUX, etc.)

    Audio:
    {codec_audio} - Audio codec (AAC, DTS-HD.MA, etc.)
    {codec_audio_full} - Audio codec with channels (AAC.2.0, DTS.5.1)
    {audio_channels} - Audio channels only (2.0, 5.1, 7.1)

    Video:
    {codec_video} - Video codec (x264, x265, etc.)
    {hdr} - HDR format if present (HDR10, DV, etc.)

    Serie:
    {saison} - Season number for series (S01)
    {episode} - Episode number for series (E05)

    Release:
    {group} - Release group name

Example Templates:
    Standard: {titre}.{annee}.{langue}.{resolution}.{source}.{codec_audio}.{codec_video}-{group}
    C411: {titre_fr_lower}.{annee}.{langue}.{vff}.{resolution}.{quality}.{source}.{codec_audio_full}.{codec_video}-{group}
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import Session

from .base import Base


class NamingTemplate(Base):
    """
    Naming template for customizable release name formatting.

    Attributes:
        id: Primary key
        name: Unique template name
        description: Optional description
        template: Template string with {variables}
        is_default: Whether this is the default template
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    __tablename__ = 'naming_templates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(500), nullable=True)
    template = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<NamingTemplate(id={self.id}, name='{self.name}', is_default={self.is_default})>"

    @classmethod
    def get_default(cls, db: Session) -> Optional["NamingTemplate"]:
        """Get the default template."""
        return db.query(cls).filter(cls.is_default == True).first()

    @classmethod
    def get_all(cls, db: Session) -> List["NamingTemplate"]:
        """Get all templates ordered by name."""
        return db.query(cls).order_by(cls.name).all()

    @classmethod
    def get_by_id(cls, db: Session, template_id: int) -> Optional["NamingTemplate"]:
        """Get a template by ID."""
        return db.query(cls).filter(cls.id == template_id).first()

    @classmethod
    def get_by_name(cls, db: Session, name: str) -> Optional["NamingTemplate"]:
        """Get a template by name."""
        return db.query(cls).filter(cls.name == name).first()

    def set_as_default(self, db: Session) -> None:
        """Set this template as the default (unsets other defaults)."""
        # Unset all other defaults
        db.query(NamingTemplate).filter(
            NamingTemplate.id != self.id
        ).update({NamingTemplate.is_default: False})

        # Set this as default
        self.is_default = True
        db.commit()

    @classmethod
    def get_available_variables(cls) -> dict:
        """Return the list of available template variables with descriptions."""
        return {
            "titre": {
                "titre": "Titre normalise (points au lieu d'espaces)",
                "titre_fr": "Titre francais",
                "titre_en": "Titre anglais/original",
                "titre_lower": "Titre en minuscules",
                "titre_fr_lower": "Titre francais en minuscules",
                "titre_en_lower": "Titre anglais en minuscules",
            },
            "media": {
                "annee": "Annee de sortie",
                "langue": "Langue audio (FRENCH, MULTi, etc.)",
                "vff": "Indicateur VFF/VFQ (si audio francais)",
                "resolution": "Resolution video (1080p, 2160p)",
                "source": "Source (WEB, BluRay, HDTV)",
                "quality": "Indicateur qualite (HDLight, REMUX)",
            },
            "audio": {
                "codec_audio": "Codec audio (AAC, DTS-HD.MA)",
                "codec_audio_full": "Codec audio avec canaux (AAC.2.0)",
                "audio_channels": "Canaux audio seuls (2.0, 5.1, 7.1)",
            },
            "video": {
                "codec_video": "Codec video (x264, x265, H264)",
                "hdr": "Format HDR si present (HDR10, DV)",
            },
            "serie": {
                "saison": "Numero de saison (S01)",
                "episode": "Numero d'episode (E05)",
            },
            "release": {
                "group": "Nom du groupe de release",
            }
        }

    @classmethod
    def get_example_templates(cls) -> List[dict]:
        """Return example templates for user reference."""
        return [
            {
                "name": "Standard Scene",
                "template": "{titre}.{annee}.{langue}.{resolution}.{source}.{codec_video}-{group}",
                "description": "Format scene standard: Titre.Annee.Langue.Resolution.Source.Codec-GROUPE"
            },
            {
                "name": "Complet",
                "template": "{titre}.{annee}.{langue}.{resolution}.{source}.{codec_audio}.{codec_video}-{group}",
                "description": "Format complet avec codec audio"
            },
            {
                "name": "C411",
                "template": "{titre_fr_lower}.{annee}.{langue}.{vff}.{resolution}.{quality}.{source}.{codec_audio_full}.{codec_video}-{group}",
                "description": "Format C411: titre.annee.MULTi.VFF.1080p.HDLight.WEB.AAC.2.0.x264-GROUPE"
            },
            {
                "name": "Serie TV",
                "template": "{titre}.{saison}{episode}.{langue}.{resolution}.{source}.{codec_video}-{group}",
                "description": "Format pour series TV avec saison/episode"
            },
            {
                "name": "4K HDR",
                "template": "{titre}.{annee}.{langue}.{resolution}.{hdr}.{source}.{codec_video}-{group}",
                "description": "Format 4K avec information HDR"
            },
            {
                "name": "Minimal",
                "template": "{titre}.{annee}.{resolution}-{group}",
                "description": "Format minimal: Titre.Annee.Resolution-GROUPE"
            }
        ]
