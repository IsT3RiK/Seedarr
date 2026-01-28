"""
Tracker Adapters for Seedarr v2.0

This package provides an abstraction layer for tracker-specific implementations,
enabling the application to support multiple trackers through a common interface.

The adapter pattern isolates tracker-specific logic (authentication, API formats,
tag handling) from the core pipeline, making it easy to add support for new
trackers without modifying the pipeline code.

Available Adapters:
    - TrackerAdapter: Abstract base class defining the adapter contract
    - LaCaleAdapter: Concrete implementation for "La Cale" tracker
    - C411Adapter: Concrete implementation for "C411" tracker
    - ConfigAdapter: Config-driven generic adapter (YAML/JSON configuration)
    - GenericTrackerAdapter: Fallback adapter for testing/placeholder

Supporting Classes:
    - TrackerFactory: Factory for creating adapters based on configuration
    - TrackerConfigLoader: Loads and validates tracker YAML/JSON configs

Architecture:
    Pipeline → TrackerFactory → TrackerAdapter (interface)
                                      ├── LaCaleAdapter (legacy, Cloudflare)
                                      ├── C411Adapter (legacy, API key)
                                      ├── ConfigAdapter (config-driven, universal)
                                      └── GenericTrackerAdapter (fallback)

This design allows tracker selection via configuration and ensures pipeline
remains tracker-agnostic. New trackers can be added via YAML configuration
without writing any Python code.
"""

from .tracker_adapter import TrackerAdapter
from .lacale_adapter import LaCaleAdapter
from .c411_adapter import C411Adapter
from .config_adapter import ConfigAdapter
from .generic_adapter import GenericTrackerAdapter
from .tracker_factory import TrackerFactory, get_tracker_factory
from .tracker_config_loader import TrackerConfigLoader, get_config_loader, load_tracker_config

__all__ = [
    'TrackerAdapter',
    'LaCaleAdapter',
    'C411Adapter',
    'ConfigAdapter',
    'GenericTrackerAdapter',
    'TrackerFactory',
    'get_tracker_factory',
    'TrackerConfigLoader',
    'get_config_loader',
    'load_tracker_config'
]
