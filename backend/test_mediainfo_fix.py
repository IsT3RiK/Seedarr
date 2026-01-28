"""
Test script to verify MediaInfo extraction fix
"""
import asyncio
from pathlib import Path
from app.database import SessionLocal
from app.models.file_entry import FileEntry, Status
from app.services.nfo_generator import get_nfo_generator

async def test_fix():
    db = SessionLocal()

    # Get the Deep Impact file entry
    entry = db.query(FileEntry).filter(
        FileEntry.release_name.like('%Deep Impact%')
    ).first()

    if not entry:
        print('File not found in database')
        db.close()
        return

    print(f'Found: {entry.release_name}')
    print(f'Current status: {entry.status.value}')
    print(f'File path: {entry.file_path}')

    # Extract MediaInfo data
    nfo_generator = get_nfo_generator()
    media_data = await nfo_generator.extract_mediainfo(entry.file_path)

    # Build corrected mediainfo_dict (with fixed attribute names)
    mediainfo_dict = {
        'file_name': media_data.file_name,
        'format': media_data.format,
        'file_size': media_data.file_size,
        'duration': media_data.duration,
        'overall_bitrate': media_data.overall_bitrate,
        'video_tracks': [
            {
                'codec': v.format,  # FIXED: was v.codec
                'width': v.width,
                'height': v.height,
                'resolution': v.resolution,  # ADDED
                'frame_rate': v.frame_rate,
                'bit_depth': v.bit_depth,
                'hdr_format': v.hdr_format,
                'bitrate': v.bitrate
            } for v in media_data.video_tracks
        ],
        'audio_tracks': [
            {
                'codec': a.format,  # FIXED: was a.codec
                'channels': a.channels,
                'language': a.language,
                'bitrate': a.bitrate,
                'title': a.title
            } for a in media_data.audio_tracks
        ],
        'subtitle_tracks': [
            {
                'language': s.language,
                'format': s.format,
                'title': s.title
            } for s in media_data.subtitle_tracks
        ]
    }

    # Update the database entry
    entry.mediainfo_data = mediainfo_dict
    db.commit()

    print('\n✓ MediaInfo data updated successfully:')
    if mediainfo_dict.get('video_tracks'):
        video = mediainfo_dict['video_tracks'][0]
        print(f"  Video: {video['codec']} {video['width']}x{video['height']} @ {video['frame_rate']}, {video['bit_depth']}")

    if mediainfo_dict.get('audio_tracks'):
        audio = mediainfo_dict['audio_tracks'][0]
        print(f"  Audio: {audio['codec']} {audio['channels']} channels ({audio['language']})")

    print(f'\nRefresh the page at: http://localhost:8000/releases/{entry.id}')

    db.close()

if __name__ == '__main__':
    asyncio.run(test_fix())
