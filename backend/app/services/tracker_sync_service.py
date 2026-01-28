"""
Tracker Sync Service for Seedarr v2.0

This service handles synchronization of metadata (categories, tags) from the tracker API
to the local database. It should be called:
- At application startup
- When testing tracker connection in Settings

Key Features:
    - Syncs categories from tracker API
    - Syncs tags (grouped and ungrouped) from tracker API
    - Graceful handling of missing/null data
    - Logs sync results for debugging
"""

import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from app.models.categories import Categories
from app.models.tags import Tags
from app.models.settings import Settings
from app.services.cloudflare_session_manager import CloudflareSessionManager
from app.services.lacale_client import LaCaleClient
from app.services.exceptions import TrackerAPIError, NetworkRetryableError

logger = logging.getLogger(__name__)


class TrackerSyncService:
    """
    Service for synchronizing tracker metadata to local database.

    This service fetches categories and tags from the tracker API and stores
    them in the database for use during uploads.
    """

    def __init__(self, db: Session, settings: Optional[Settings] = None):
        """
        Initialize TrackerSyncService.

        Args:
            db: SQLAlchemy database session
            settings: Optional Settings object (will be fetched if not provided)
        """
        self.db = db
        self.settings = settings or Settings.get_settings(db)

        if not self.settings:
            raise TrackerAPIError("No settings configured")

        if not self.settings.tracker_url or not self.settings.tracker_passkey:
            raise TrackerAPIError("Tracker URL and passkey must be configured")

        self.session_manager = CloudflareSessionManager(self.settings.flaresolverr_url)
        self.client = LaCaleClient(self.settings.tracker_url, self.settings.tracker_passkey)

    async def sync_all(self) -> Dict[str, Any]:
        """
        Sync all metadata from tracker (categories and tags).

        Returns:
            Dictionary with sync results:
                {
                    'success': bool,
                    'categories_synced': int,
                    'tags_synced': int,
                    'message': str
                }
        """
        logger.info("Starting tracker metadata sync...")

        result = {
            'success': False,
            'categories_synced': 0,
            'tags_synced': 0,
            'message': ''
        }

        try:
            # Get authenticated session
            logger.debug("Getting authenticated session via FlareSolverr...")
            session = await self.session_manager.get_session(self.settings.tracker_url)

            if not session:
                result['message'] = "Failed to get authenticated session"
                logger.error(result['message'])
                return result

            # Fetch metadata from tracker
            logger.debug("Fetching metadata from tracker API...")
            metadata = await self.client.get_metadata(session)

            if not metadata:
                result['message'] = "Tracker returned empty metadata"
                logger.warning(result['message'])
                return result

            # Sync categories
            categories_count = await self._sync_categories(metadata.get('categories', []))
            result['categories_synced'] = categories_count

            # Sync tags (from groups and ungrouped)
            tags_count = await self._sync_tags(metadata)
            result['tags_synced'] = tags_count

            result['success'] = True
            result['message'] = f"Synced {categories_count} categories and {tags_count} tags"
            logger.info(f"✓ {result['message']}")

            return result

        except (TrackerAPIError, NetworkRetryableError) as e:
            result['message'] = str(e)
            logger.error(f"Tracker sync failed: {e}")
            return result

        except Exception as e:
            result['message'] = f"Unexpected error: {type(e).__name__}: {e}"
            logger.error(f"Tracker sync failed: {result['message']}", exc_info=True)
            return result

    async def _sync_categories(self, categories_data: list) -> int:
        """
        Sync categories to database.

        Args:
            categories_data: List of category dicts from API

        Returns:
            Number of categories synced
        """
        count = 0

        for cat in categories_data:
            if not cat:
                continue

            try:
                Categories.upsert(
                    db=self.db,
                    category_id=str(cat.get('id', '')),
                    name=cat.get('name', 'Unknown'),
                    slug=cat.get('slug', '')
                )
                count += 1
            except Exception as e:
                logger.warning(f"Failed to sync category {cat.get('name')}: {e}")

        logger.debug(f"Synced {count} categories")
        return count

    async def _sync_tags(self, metadata: dict) -> int:
        """
        Sync tags to database from grouped and ungrouped sources.

        Args:
            metadata: Full metadata dict from API

        Returns:
            Number of tags synced
        """
        count = 0

        # Sync tags from tag groups
        for group in metadata.get('tagGroups', []):
            if not group:
                continue

            group_name = group.get('name', '')
            group_tags = group.get('tags') or []  # Handle null

            for tag in group_tags:
                if not tag:
                    continue

                try:
                    Tags.upsert(
                        db=self.db,
                        tag_id=str(tag.get('id', '')),
                        label=tag.get('name', 'Unknown'),
                        category=group_name
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to sync tag {tag.get('name')}: {e}")

        # Sync ungrouped tags
        for tag in metadata.get('ungroupedTags', []):
            if not tag:
                continue

            try:
                Tags.upsert(
                    db=self.db,
                    tag_id=str(tag.get('id', '')),
                    label=tag.get('name', 'Unknown'),
                    category='Ungrouped'
                )
                count += 1
            except Exception as e:
                logger.warning(f"Failed to sync ungrouped tag {tag.get('name')}: {e}")

        logger.debug(f"Synced {count} tags")
        return count


async def sync_tracker_metadata(db: Session) -> Dict[str, Any]:
    """
    Convenience function to sync tracker metadata.

    Args:
        db: SQLAlchemy database session

    Returns:
        Sync result dictionary
    """
    try:
        service = TrackerSyncService(db)
        return await service.sync_all()
    except TrackerAPIError as e:
        logger.warning(f"Cannot sync tracker metadata: {e}")
        return {
            'success': False,
            'categories_synced': 0,
            'tags_synced': 0,
            'message': str(e)
        }
    except Exception as e:
        logger.error(f"Tracker sync error: {e}", exc_info=True)
        return {
            'success': False,
            'categories_synced': 0,
            'tags_synced': 0,
            'message': str(e)
        }
