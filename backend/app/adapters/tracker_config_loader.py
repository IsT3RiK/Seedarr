"""
Tracker Configuration Loader for Seedarr v2.0

This module handles loading and validating tracker configuration files (YAML/JSON).
It provides a unified way to load tracker-specific settings without hardcoding them.

Usage:
    loader = TrackerConfigLoader()

    # Load from file
    config = loader.load("c411")  # Loads from config_schemas/c411.yaml

    # Load from database (Tracker model)
    config = loader.load_from_tracker(tracker)

    # Validate a config
    is_valid, errors = loader.validate(config)
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Union

logger = logging.getLogger(__name__)

# Try to import yaml, fall back to json-only if not available
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    logger.warning("PyYAML not installed. Only JSON configs will be supported.")


class ConfigValidationError(Exception):
    """Raised when tracker configuration validation fails."""

    def __init__(self, message: str, errors: List[str] = None):
        super().__init__(message)
        self.errors = errors or []


class TrackerConfigLoader:
    """
    Loads and validates tracker configuration files.

    Supports both YAML and JSON formats. Configurations define:
    - Authentication method (bearer, passkey, cookie)
    - API endpoints
    - Upload field mappings
    - Options/metadata mappings
    - Category mappings
    """

    # Default config directory (relative to this file)
    DEFAULT_CONFIG_DIR = Path(__file__).parent / "config_schemas"

    # Required config sections
    REQUIRED_SECTIONS = ["tracker", "auth", "endpoints", "upload"]

    # Required tracker fields
    REQUIRED_TRACKER_FIELDS = ["name", "slug"]

    # Valid auth types
    VALID_AUTH_TYPES = ["bearer", "passkey", "cookie", "api_key", "none"]

    # Valid upload field types
    VALID_FIELD_TYPES = ["file", "string", "json", "boolean", "repeated", "number"]

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Initialize TrackerConfigLoader.

        Args:
            config_dir: Directory containing config files.
                        Defaults to config_schemas/ next to this file.
        """
        self.config_dir = Path(config_dir) if config_dir else self.DEFAULT_CONFIG_DIR
        self._cache: Dict[str, Dict[str, Any]] = {}

        logger.debug(f"TrackerConfigLoader initialized with config_dir: {self.config_dir}")

    def get_available_configs(self) -> List[str]:
        """
        Get list of available tracker configurations.

        Returns:
            List of config slugs (filenames without extension)
        """
        configs = []

        if not self.config_dir.exists():
            logger.warning(f"Config directory does not exist: {self.config_dir}")
            return configs

        # Find all YAML and JSON files
        for file_path in self.config_dir.iterdir():
            if file_path.suffix in ('.yaml', '.yml', '.json'):
                configs.append(file_path.stem)

        return sorted(configs)

    def load(self, slug: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        Load a tracker configuration by slug.

        Args:
            slug: Tracker slug (e.g., "c411", "lacale")
            use_cache: Whether to use cached config if available

        Returns:
            Configuration dictionary

        Raises:
            FileNotFoundError: If config file not found
            ConfigValidationError: If config is invalid
        """
        # Check cache
        if use_cache and slug in self._cache:
            logger.debug(f"Returning cached config for {slug}")
            return self._cache[slug]

        # Find config file
        config_path = self._find_config_file(slug)

        if not config_path:
            raise FileNotFoundError(
                f"No configuration file found for tracker '{slug}'. "
                f"Looked in: {self.config_dir}"
            )

        # Load file
        config = self._load_file(config_path)

        # Validate
        is_valid, errors = self.validate(config)
        if not is_valid:
            raise ConfigValidationError(
                f"Invalid configuration for tracker '{slug}'",
                errors=errors
            )

        # Cache and return
        self._cache[slug] = config
        logger.info(f"Loaded and validated config for tracker: {slug}")

        return config

    def load_from_dict(self, config_dict: Dict[str, Any], validate: bool = True) -> Dict[str, Any]:
        """
        Load configuration from a dictionary (e.g., from database JSON field).

        Args:
            config_dict: Configuration dictionary
            validate: Whether to validate the config

        Returns:
            Configuration dictionary

        Raises:
            ConfigValidationError: If config is invalid and validate=True
        """
        if validate:
            is_valid, errors = self.validate(config_dict)
            if not is_valid:
                raise ConfigValidationError(
                    "Invalid configuration dictionary",
                    errors=errors
                )

        return config_dict

    def load_from_tracker(self, tracker: Any) -> Optional[Dict[str, Any]]:
        """
        Load configuration for a Tracker model instance.

        Tries in order:
        1. tracker.upload_config (JSON stored in DB)
        2. Config file by tracker.slug

        Args:
            tracker: Tracker model instance

        Returns:
            Configuration dictionary or None if not found
        """
        # First, try upload_config from database
        if hasattr(tracker, 'upload_config') and tracker.upload_config:
            try:
                if isinstance(tracker.upload_config, str):
                    config = json.loads(tracker.upload_config)
                else:
                    config = tracker.upload_config

                if config:
                    logger.debug(f"Loading config from tracker.upload_config for {tracker.slug}")
                    return self.load_from_dict(config)
            except (json.JSONDecodeError, ConfigValidationError) as e:
                logger.warning(f"Invalid upload_config for tracker {tracker.slug}: {e}")

        # Fallback to file-based config
        slug = getattr(tracker, 'slug', None) or getattr(tracker, 'adapter_type', None)
        if slug:
            try:
                return self.load(slug)
            except FileNotFoundError:
                logger.debug(f"No config file found for tracker slug: {slug}")
            except ConfigValidationError as e:
                logger.warning(f"Invalid config file for tracker {slug}: {e}")

        return None

    def validate(self, config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate a tracker configuration.

        Args:
            config: Configuration dictionary to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Check required sections
        for section in self.REQUIRED_SECTIONS:
            if section not in config:
                errors.append(f"Missing required section: '{section}'")

        if errors:
            return False, errors

        # Validate tracker section
        tracker = config.get("tracker", {})
        for field in self.REQUIRED_TRACKER_FIELDS:
            if field not in tracker:
                errors.append(f"Missing required tracker field: '{field}'")

        # Validate auth section
        auth = config.get("auth", {})
        auth_type = auth.get("type")
        if auth_type and auth_type not in self.VALID_AUTH_TYPES:
            errors.append(f"Invalid auth type: '{auth_type}'. Valid types: {self.VALID_AUTH_TYPES}")

        # Validate endpoints section
        endpoints = config.get("endpoints", {})
        if not endpoints.get("upload"):
            errors.append("Missing required endpoint: 'upload'")

        # Validate upload section
        upload = config.get("upload", {})
        fields = upload.get("fields", {})

        # Torrent field is always required
        if "torrent" not in fields:
            errors.append("Missing required upload field: 'torrent'")

        # Validate field types
        for field_name, field_config in fields.items():
            if isinstance(field_config, dict):
                field_type = field_config.get("type")
                if field_type and field_type not in self.VALID_FIELD_TYPES:
                    errors.append(
                        f"Invalid field type for '{field_name}': '{field_type}'. "
                        f"Valid types: {self.VALID_FIELD_TYPES}"
                    )

        # Validate options section (if present)
        options = config.get("options", {})
        for option_name, option_config in options.items():
            if isinstance(option_config, dict):
                if "type" not in option_config:
                    errors.append(f"Option '{option_name}' missing 'type' field")

        return len(errors) == 0, errors

    def _find_config_file(self, slug: str) -> Optional[Path]:
        """Find configuration file by slug."""
        if not self.config_dir.exists():
            return None

        # Try different extensions
        for ext in ('.yaml', '.yml', '.json'):
            config_path = self.config_dir / f"{slug}{ext}"
            if config_path.exists():
                return config_path

        return None

    def _load_file(self, path: Path) -> Dict[str, Any]:
        """Load configuration from file."""
        logger.debug(f"Loading config from: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        if path.suffix in ('.yaml', '.yml'):
            if not YAML_AVAILABLE:
                raise ImportError(
                    "PyYAML is required to load YAML configs. "
                    "Install with: pip install pyyaml"
                )
            return yaml.safe_load(content)

        elif path.suffix == '.json':
            return json.loads(content)

        else:
            raise ValueError(f"Unsupported config file format: {path.suffix}")

    def clear_cache(self, slug: Optional[str] = None):
        """
        Clear configuration cache.

        Args:
            slug: Specific slug to clear, or None to clear all
        """
        if slug:
            self._cache.pop(slug, None)
            logger.debug(f"Cleared cache for: {slug}")
        else:
            self._cache.clear()
            logger.debug("Cleared all config cache")

    def reload(self, slug: str) -> Dict[str, Any]:
        """
        Reload a configuration (bypassing cache).

        Args:
            slug: Tracker slug to reload

        Returns:
            Fresh configuration dictionary
        """
        self.clear_cache(slug)
        return self.load(slug, use_cache=False)

    def get_options_config(self, slug: str) -> Dict[str, Any]:
        """
        Get just the options configuration for a tracker.

        Convenience method for OptionsMapper integration.

        Args:
            slug: Tracker slug

        Returns:
            Options configuration dictionary
        """
        config = self.load(slug)
        return config.get("options", {})

    def get_upload_fields(self, slug: str) -> Dict[str, Any]:
        """
        Get upload field configuration for a tracker.

        Args:
            slug: Tracker slug

        Returns:
            Upload fields configuration dictionary
        """
        config = self.load(slug)
        return config.get("upload", {}).get("fields", {})

    def save(self, slug: str, config: Dict[str, Any], format: str = "yaml") -> Path:
        """
        Save a configuration to file.

        Args:
            slug: Tracker slug
            config: Configuration dictionary
            format: File format ("yaml" or "json")

        Returns:
            Path to saved file

        Raises:
            ConfigValidationError: If config is invalid
        """
        # Validate first
        is_valid, errors = self.validate(config)
        if not is_valid:
            raise ConfigValidationError(
                f"Cannot save invalid configuration for '{slug}'",
                errors=errors
            )

        # Ensure directory exists
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Determine file path
        ext = ".yaml" if format == "yaml" else ".json"
        file_path = self.config_dir / f"{slug}{ext}"

        # Save file
        with open(file_path, 'w', encoding='utf-8') as f:
            if format == "yaml":
                if not YAML_AVAILABLE:
                    raise ImportError("PyYAML required for YAML format")
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            else:
                json.dump(config, f, indent=2, ensure_ascii=False)

        # Update cache
        self._cache[slug] = config

        logger.info(f"Saved configuration to: {file_path}")
        return file_path


# Singleton instance
_loader_instance: Optional[TrackerConfigLoader] = None


def get_config_loader(config_dir: Optional[Path] = None) -> TrackerConfigLoader:
    """
    Get or create the singleton TrackerConfigLoader instance.

    Args:
        config_dir: Optional config directory (only used on first call)

    Returns:
        TrackerConfigLoader instance
    """
    global _loader_instance

    if _loader_instance is None:
        _loader_instance = TrackerConfigLoader(config_dir)

    return _loader_instance


def load_tracker_config(slug: str) -> Dict[str, Any]:
    """
    Convenience function to load a tracker config.

    Args:
        slug: Tracker slug

    Returns:
        Configuration dictionary
    """
    return get_config_loader().load(slug)
