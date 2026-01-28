"""
NFO Template Model for Seedarr v2.0

This module defines the NFOTemplate model for storing customizable
NFO file templates with MediaInfo variables.

Template Variables:
    General:
        {{release_name}} - Release name
        {{media_type}} - Type (Movies, Series)
        {{file_name}} - Original filename
        {{format}} - Container format (MKV, MP4)
        {{file_size}} - File size
        {{duration}} - Duration
        {{overall_bitrate}} - Overall bitrate

    Video:
        {{video_format}} - Video codec format
        {{video_profile}} - Codec profile
        {{video_bitrate}} - Video bitrate
        {{resolution}} - Resolution (ex: 1920x1080)
        {{resolution_label}} - Resolution label (1080p, 2160p)
        {{frame_rate}} - Frame rate
        {{bit_depth}} - Bit depth
        {{hdr_format}} - HDR format

    Audio (for each track):
        {{audio_list}} - Formatted list of all audio tracks
        {{audio_format}} - Audio codec
        {{audio_channels}} - Channel count
        {{audio_bitrate}} - Audio bitrate
        {{audio_language}} - Language

    Subtitles:
        {{subtitle_list}} - Formatted list of all subtitles

    Technical Summary:
        {{source}} - Source (BluRay, WEB, etc.)
        {{video_codec}} - Simplified video codec
        {{audio_codec}} - Simplified audio codec
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import Session

from .base import Base


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

{{#video_tracks}}
-------------------------------------------------------------------------------
                                 VIDEO INFO
-------------------------------------------------------------------------------
Format...............: {{video_format}}
{{#video_profile}}Format Profile.......: {{video_profile}}{{/video_profile}}
{{#video_bitrate}}Bitrate..............: {{video_bitrate}}{{/video_bitrate}}
{{#resolution}}Resolution...........: {{resolution}}{{/resolution}}
{{#frame_rate}}Frame Rate...........: {{frame_rate}}{{/frame_rate}}
{{#bit_depth}}Bit Depth............: {{bit_depth}}{{/bit_depth}}
{{#hdr_format}}HDR Format...........: {{hdr_format}}{{/hdr_format}}
{{/video_tracks}}

{{#audio_tracks}}
-------------------------------------------------------------------------------
                                 AUDIO INFO
-------------------------------------------------------------------------------
{{audio_list}}
{{/audio_tracks}}

{{#subtitle_tracks}}
-------------------------------------------------------------------------------
                                   SUBTITLES
-------------------------------------------------------------------------------
{{subtitle_list}}
{{/subtitle_tracks}}

-------------------------------------------------------------------------------
                             Partager & Preserver
-------------------------------------------------------------------------------
"""


class NFOTemplate(Base):
    """
    NFO template for customizable technical information files.

    Attributes:
        id: Primary key
        name: Unique template name
        description: Optional description
        content: Template content with {{variables}}
        is_default: Whether this is the default template
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    __tablename__ = 'nfo_templates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(500), nullable=True)
    content = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<NFOTemplate(id={self.id}, name='{self.name}', is_default={self.is_default})>"

    @classmethod
    def get_default(cls, db: Session) -> Optional["NFOTemplate"]:
        """Get the default template."""
        return db.query(cls).filter(cls.is_default == True).first()

    @classmethod
    def get_all(cls, db: Session) -> List["NFOTemplate"]:
        """Get all templates ordered by name."""
        return db.query(cls).order_by(cls.name).all()

    @classmethod
    def get_by_id(cls, db: Session, template_id: int) -> Optional["NFOTemplate"]:
        """Get a template by ID."""
        return db.query(cls).filter(cls.id == template_id).first()

    @classmethod
    def get_by_name(cls, db: Session, name: str) -> Optional["NFOTemplate"]:
        """Get a template by name."""
        return db.query(cls).filter(cls.name == name).first()

    def set_as_default(self, db: Session) -> None:
        """Set this template as the default (unsets other defaults)."""
        # Unset all other defaults
        db.query(NFOTemplate).filter(
            NFOTemplate.id != self.id
        ).update({NFOTemplate.is_default: False})

        # Set this as default
        self.is_default = True
        db.commit()

    @classmethod
    def get_default_template_content(cls) -> str:
        """Get the default NFO template content."""
        return DEFAULT_NFO_TEMPLATE

    @classmethod
    def get_available_variables(cls) -> dict:
        """Return the list of available template variables with descriptions."""
        return {
            "general": {
                "release_name": "Nom de la release",
                "media_type": "Type de media (Movies, Series)",
                "file_name": "Nom du fichier original",
                "format": "Format conteneur (MKV, MP4)",
                "file_size": "Taille du fichier",
                "duration": "Duree",
                "overall_bitrate": "Debit global",
            },
            "video": {
                "video_format": "Format/codec video",
                "video_profile": "Profil du codec",
                "video_bitrate": "Debit video",
                "resolution": "Resolution (ex: 1920x1080)",
                "resolution_label": "Label resolution (1080p, 2160p)",
                "frame_rate": "Frequence d'images",
                "bit_depth": "Profondeur de bits",
                "hdr_format": "Format HDR",
            },
            "audio": {
                "audio_list": "Liste formatee des pistes audio",
                "audio_format": "Format/codec audio",
                "audio_channels": "Nombre de canaux",
                "audio_bitrate": "Debit audio",
                "audio_language": "Langue audio",
            },
            "subtitles": {
                "subtitle_list": "Liste formatee des sous-titres",
            },
            "summary": {
                "source": "Source (BluRay, WEB, HDTV)",
                "video_codec": "Codec video simplifie (H264, HEVC)",
                "audio_codec": "Codec audio simplifie (AAC, DTS)",
            },
            "conditionals": {
                "{{#video_tracks}}...{{/video_tracks}}": "Bloc affiche si pistes video",
                "{{#audio_tracks}}...{{/audio_tracks}}": "Bloc affiche si pistes audio",
                "{{#subtitle_tracks}}...{{/subtitle_tracks}}": "Bloc affiche si sous-titres",
                "{{#hdr_format}}...{{/hdr_format}}": "Bloc affiche si HDR",
            }
        }
