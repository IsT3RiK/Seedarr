"""
TrackerFactory for Seedarr v2.0

This module provides a factory for creating tracker adapters dynamically
based on tracker configuration. It manages the registry of adapter types
and instantiates the appropriate adapter for each tracker.

Architecture:
    TrackerFactory
        ├── ConfigAdapter (adapter_type: "config") - YAML-driven, handles ALL trackers
        └── GenericTrackerAdapter (adapter_type: "generic") - fallback for search-only

Usage:
    factory = TrackerFactory(db, flaresolverr_url)

    # Get adapter for a specific tracker
    tracker = Tracker.get_by_slug(db, "lacale")
    adapter = factory.get_adapter(tracker)

    # Get all enabled adapters
    adapters = factory.get_all_enabled_adapters()
    for tracker, adapter in adapters:
        await adapter.authenticate()
"""

import logging
from typing import Dict, List, Optional, Tuple, Type, TYPE_CHECKING

from .tracker_adapter import TrackerAdapter

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from ..models.tracker import Tracker

logger = logging.getLogger(__name__)


class TrackerFactory:
    """
    Factory for creating tracker adapters dynamically.

    This factory maintains a registry of adapter types and creates
    appropriate adapter instances based on tracker configuration.

    Adapter Registry:
        - "config": ConfigAdapter (config-driven, handles all trackers with YAML)
        - "generic": GenericTrackerAdapter (basic fallback for search-only)
        - "lacale": Legacy alias -> redirects to ConfigAdapter
        - "c411": Legacy alias -> redirects to ConfigAdapter

    Example:
        >>> factory = TrackerFactory(db, flaresolverr_url="http://localhost:8191")
        >>>
        >>> # Get adapter for specific tracker
        >>> tracker = Tracker.get_by_slug(db, "lacale")
        >>> adapter = factory.get_adapter(tracker)
        >>> await adapter.authenticate()
        >>>
        >>> # Get all enabled adapters
        >>> for tracker, adapter in factory.get_all_enabled_adapters():
        ...     await adapter.authenticate()
        ...     result = await adapter.upload_torrent(...)
    """

    # Registry mapping adapter_type to adapter class
    # Adapters are registered lazily to avoid circular imports
    _REGISTRY: Dict[str, Type[TrackerAdapter]] = {}
    _registry_initialized = False

    def __init__(
        self,
        db: 'Session',
        flaresolverr_url: Optional[str] = None,
        flaresolverr_timeout: int = 60000
    ):
        """
        Initialize TrackerFactory.

        Args:
            db: SQLAlchemy database session
            flaresolverr_url: FlareSolverr service URL (for Cloudflare bypass)
            flaresolverr_timeout: FlareSolverr request timeout in ms
        """
        self.db = db
        self.flaresolverr_url = flaresolverr_url
        self.flaresolverr_timeout = flaresolverr_timeout

        # Initialize registry if not done
        self._ensure_registry()

        # Cache of instantiated adapters (keyed by tracker_id)
        self._adapter_cache: Dict[int, TrackerAdapter] = {}

    @classmethod
    def _ensure_registry(cls) -> None:
        """Ensure adapter registry is initialized."""
        if cls._registry_initialized:
            return

        # Import and register ConfigAdapter (config-driven generic adapter)
        try:
            from .config_adapter import ConfigAdapter
            cls._REGISTRY['config'] = ConfigAdapter
            # Legacy aliases - redirect to ConfigAdapter
            cls._REGISTRY['lacale'] = ConfigAdapter
            cls._REGISTRY['c411'] = ConfigAdapter
        except ImportError:
            logger.warning("ConfigAdapter not available")

        # Generic adapter as fallback
        try:
            from .generic_adapter import GenericTrackerAdapter
            cls._REGISTRY['generic'] = GenericTrackerAdapter
        except ImportError:
            logger.warning("GenericTrackerAdapter not available")

        cls._registry_initialized = True
        logger.info(f"Tracker adapter registry initialized: {list(cls._REGISTRY.keys())}")

    @classmethod
    def register_adapter(cls, adapter_type: str, adapter_class: Type[TrackerAdapter]) -> None:
        """
        Register a new adapter type.

        Args:
            adapter_type: Adapter type identifier (e.g., "mytracker")
            adapter_class: Adapter class implementing TrackerAdapter

        Example:
            >>> TrackerFactory.register_adapter("mytracker", MyTrackerAdapter)
        """
        cls._REGISTRY[adapter_type] = adapter_class
        logger.info(f"Registered tracker adapter: {adapter_type} -> {adapter_class.__name__}")

    def get_adapter(self, tracker: 'Tracker') -> TrackerAdapter:
        """
        Get adapter instance for a tracker.

        Creates a new adapter instance if not cached, or returns
        the cached instance for the tracker.

        Adapter selection logic:
        1. If a YAML config file exists for tracker.slug -> use ConfigAdapter
        2. If tracker.adapter_type is a legacy type (lacale, c411) -> use ConfigAdapter
        3. Fallback to "generic" if no adapter_type set

        Args:
            tracker: Tracker model instance

        Returns:
            TrackerAdapter instance configured for the tracker

        Raises:
            ValueError: If adapter_type is not registered
        """
        # Check cache first
        if tracker.id in self._adapter_cache:
            return self._adapter_cache[tracker.id]

        # Determine adapter type (always prefer ConfigAdapter if YAML exists)
        adapter_type = self._determine_adapter_type(tracker)

        if adapter_type not in self._REGISTRY:
            raise ValueError(
                f"Unknown adapter type: {adapter_type}. "
                f"Available types: {list(self._REGISTRY.keys())}"
            )

        adapter_class = self._REGISTRY[adapter_type]

        # Create adapter based on type
        adapter = self._create_adapter(adapter_class, tracker)

        # Cache the adapter
        self._adapter_cache[tracker.id] = adapter

        logger.info(
            f"Created {adapter_type} adapter for tracker: {tracker.name} "
            f"(id={tracker.id})"
        )

        return adapter

    def _determine_adapter_type(self, tracker: 'Tracker') -> str:
        """
        Determine which adapter type to use for a tracker.

        Priority:
        1. If tracker.slug has a YAML config file -> "config"
        2. If tracker.adapter_type is a known type (lacale, c411) -> "config" (legacy redirect)
        3. tracker.adapter_type if set
        4. "generic" as fallback

        Args:
            tracker: Tracker model instance

        Returns:
            Adapter type string
        """
        # Check if YAML config exists for this tracker
        if tracker.slug:
            from .tracker_config_loader import get_config_loader
            try:
                config_loader = get_config_loader()
                config = config_loader.load(tracker.slug, use_cache=True)
                if config:
                    logger.info(
                        f"Found YAML config for tracker '{tracker.slug}', "
                        f"using ConfigAdapter"
                    )
                    return "config"
            except FileNotFoundError:
                pass  # No config file, continue to next check
            except Exception as e:
                logger.warning(f"Error loading config for {tracker.slug}: {e}")

        # Legacy adapter types -> redirect to config
        adapter_type = tracker.adapter_type or "generic"
        if adapter_type in ("lacale", "c411"):
            logger.info(
                f"Legacy adapter_type '{adapter_type}' for {tracker.name}, "
                f"redirecting to ConfigAdapter"
            )
            return "config"

        return adapter_type

    def _create_adapter(
        self,
        adapter_class: Type[TrackerAdapter],
        tracker: 'Tracker'
    ) -> TrackerAdapter:
        """
        Create adapter instance with tracker-specific configuration.

        Args:
            adapter_class: Adapter class to instantiate
            tracker: Tracker model with configuration

        Returns:
            Configured adapter instance
        """
        # ConfigAdapter - config-driven generic adapter
        if hasattr(adapter_class, '__name__') and 'ConfigAdapter' in adapter_class.__name__:
            from .config_adapter import ConfigAdapter
            from .tracker_config_loader import get_config_loader

            # Load tracker config
            config_loader = get_config_loader()
            tracker_config = config_loader.load_from_tracker(tracker)

            if not tracker_config:
                # Try loading by slug
                try:
                    tracker_config = config_loader.load(tracker.slug)
                except FileNotFoundError:
                    raise ValueError(
                        f"No configuration found for tracker {tracker.name}. "
                        f"Create a config file at config_schemas/{tracker.slug}.yaml"
                    )

            return ConfigAdapter(
                config=tracker_config,
                tracker_url=tracker.tracker_url,
                api_key=tracker.api_key,
                passkey=tracker.passkey,
                flaresolverr_url=self.flaresolverr_url,
                flaresolverr_timeout=self.flaresolverr_timeout,
                default_category_id=tracker.default_category_id,
                default_subcategory_id=getattr(tracker, 'default_subcategory_id', None)
            )

        # Generic adapter
        try:
            from .generic_adapter import GenericTrackerAdapter
            return GenericTrackerAdapter(
                tracker_url=tracker.tracker_url,
                passkey=tracker.passkey or "",
                api_key=tracker.api_key
            )
        except ImportError:
            raise ValueError(
                f"GenericTrackerAdapter not available for {tracker.name}"
            )

    def get_all_enabled_adapters(self) -> List[Tuple['Tracker', TrackerAdapter]]:
        """
        Get adapters for all enabled trackers.

        Returns:
            List of (Tracker, TrackerAdapter) tuples for enabled trackers
        """
        from ..models.tracker import Tracker

        trackers = Tracker.get_enabled(self.db)
        adapters = []

        for tracker in trackers:
            try:
                adapter = self.get_adapter(tracker)
                adapters.append((tracker, adapter))
            except Exception as e:
                logger.error(
                    f"Failed to create adapter for {tracker.name}: {e}"
                )

        logger.info(
            f"Created {len(adapters)} adapter(s) for enabled trackers"
        )

        return adapters

    def get_upload_enabled_adapters(self) -> List[Tuple['Tracker', TrackerAdapter]]:
        """
        Get adapters for trackers with upload enabled.

        Returns:
            List of (Tracker, TrackerAdapter) tuples for upload-enabled trackers
        """
        from ..models.tracker import Tracker

        trackers = Tracker.get_upload_enabled(self.db)
        adapters = []

        for tracker in trackers:
            try:
                adapter = self.get_adapter(tracker)
                adapters.append((tracker, adapter))
            except Exception as e:
                logger.error(
                    f"Failed to create adapter for {tracker.name}: {e}"
                )

        logger.info(
            f"Created {len(adapters)} adapter(s) for upload-enabled trackers"
        )

        return adapters

    def clear_cache(self) -> None:
        """Clear the adapter cache."""
        self._adapter_cache.clear()
        logger.debug("Adapter cache cleared")

    def get_cached_adapter(self, tracker_id: int) -> Optional[TrackerAdapter]:
        """
        Get cached adapter by tracker ID.

        Args:
            tracker_id: Tracker ID

        Returns:
            Cached adapter or None if not cached
        """
        return self._adapter_cache.get(tracker_id)


# Singleton factory instance
_factory_instance: Optional[TrackerFactory] = None


def get_tracker_factory(
    db: 'Session',
    flaresolverr_url: Optional[str] = None
) -> TrackerFactory:
    """
    Get or create the TrackerFactory instance.

    Args:
        db: SQLAlchemy database session
        flaresolverr_url: FlareSolverr service URL

    Returns:
        TrackerFactory instance
    """
    global _factory_instance
    if _factory_instance is None:
        _factory_instance = TrackerFactory(db, flaresolverr_url)
    return _factory_instance


def reset_tracker_factory() -> None:
    """Reset the singleton factory instance."""
    global _factory_instance
    if _factory_instance:
        _factory_instance.clear_cache()
    _factory_instance = None
