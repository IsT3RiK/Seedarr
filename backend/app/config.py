"""
Configuration Management for Seedarr v2.0

This module centralizes all application configuration, making it easy to manage
timeouts, retry logic, circuit breaker settings, and other operational parameters
through environment variables.

All configuration values have sensible defaults and can be overridden via
environment variables for production deployment.
"""

import os
from typing import List


class Config:
    """
    Centralized configuration management using environment variables.

    All settings have sensible defaults and can be overridden via environment
    variables for production deployment. This eliminates hardcoded values and
    makes the application production-ready.
    """

    # =============================================================================
    # APPLICATION SETTINGS
    # =============================================================================
    APP_VERSION = "2.0.0"
    APP_TITLE = "Seedarr v2.0"
    APP_DESCRIPTION = "Automated multimedia content publishing to private trackers"
    APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT = int(os.getenv("APP_PORT", "8000"))

    # =============================================================================
    # DEVELOPMENT MODE
    # =============================================================================
    DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"

    # =============================================================================
    # CORS CONFIGURATION
    # =============================================================================
    # In production, set CORS_ORIGINS to comma-separated list of allowed origins
    # Example: CORS_ORIGINS=https://app.example.com,https://api.example.com
    CORS_ORIGINS_STR = os.getenv("CORS_ORIGINS", "")
    CORS_ORIGINS: List[str] = (
        CORS_ORIGINS_STR.split(",") if CORS_ORIGINS_STR else ["http://localhost:8000"]
    )
    # Allow wildcard only in development mode
    if DEV_MODE:
        CORS_ORIGINS = ["*"]

    # =============================================================================
    # DATABASE CONFIGURATION
    # =============================================================================
    # Use relative path that works from both project root and backend directory
    # When in backend/, ./data/ works. When in project root, ./backend/data/ works
    _db_path = "./data/seedarr.db" if os.path.exists("./data") else "./backend/data/seedarr.db"
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{_db_path}"
    )

    # =============================================================================
    # EXTERNAL SERVICES
    # =============================================================================
    FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191")
    QBITTORRENT_HOST = os.getenv("QBITTORRENT_HOST", "")
    QBITTORRENT_USERNAME = os.getenv("QBITTORRENT_USERNAME", "")
    QBITTORRENT_PASSWORD = os.getenv("QBITTORRENT_PASSWORD", "")

    TRACKER_URL = os.getenv("TRACKER_URL", "")
    TRACKER_PASSKEY = os.getenv("TRACKER_PASSKEY", "")

    TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")

    # =============================================================================
    # REQUEST TIMEOUTS (seconds)
    # =============================================================================
    # General API request timeout
    API_REQUEST_TIMEOUT = int(os.getenv("API_REQUEST_TIMEOUT", "30"))

    # FlareSolverr timeout (milliseconds)
    FLARESOLVERR_TIMEOUT = int(os.getenv("FLARESOLVERR_TIMEOUT", "60000"))

    # TMDB API timeout
    TMDB_API_TIMEOUT = int(os.getenv("TMDB_API_TIMEOUT", "10"))

    # Service health check timeout
    HEALTH_CHECK_TIMEOUT = int(os.getenv("HEALTH_CHECK_TIMEOUT", "5"))

    # qBittorrent API timeout
    QBITTORRENT_TIMEOUT = int(os.getenv("QBITTORRENT_TIMEOUT", "10"))

    # =============================================================================
    # CIRCUIT BREAKER CONFIGURATION
    # =============================================================================
    # Number of consecutive failures before opening circuit
    CIRCUIT_BREAKER_MAX_FAILURES = int(os.getenv("CIRCUIT_BREAKER_MAX_FAILURES", "3"))

    # Duration to keep circuit open (seconds)
    CIRCUIT_BREAKER_OPEN_DURATION = int(os.getenv("CIRCUIT_BREAKER_OPEN_DURATION", "60"))

    # =============================================================================
    # RETRY CONFIGURATION
    # =============================================================================
    # Maximum number of retries for network requests
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))

    # Exponential backoff base multiplier
    RETRY_EXPONENTIAL_BASE = float(os.getenv("RETRY_EXPONENTIAL_BASE", "2"))

    # =============================================================================
    # CACHE CONFIGURATION
    # =============================================================================
    # TMDB cache TTL (days)
    TMDB_CACHE_TTL_DAYS = int(os.getenv("TMDB_CACHE_TTL_DAYS", "30"))

    # Tag cache TTL (days)
    TAG_CACHE_TTL_DAYS = int(os.getenv("TAG_CACHE_TTL_DAYS", "7"))

    # =============================================================================
    # TIMEZONE
    # =============================================================================
    TIMEZONE = os.getenv("TZ", "UTC")

    # =============================================================================
    # PATHS
    # =============================================================================
    TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "backend/templates")
    STATIC_DIR = os.getenv("STATIC_DIR", "backend/static")

    @classmethod
    def validate(cls) -> bool:
        """
        Validate critical configuration values.

        Returns:
            True if configuration is valid, False otherwise
        """
        # Check for required configuration
        if not cls.DATABASE_URL:
            return False

        # Validate numeric values
        if cls.APP_PORT < 1 or cls.APP_PORT > 65535:
            return False

        return True

    @classmethod
    def get_summary(cls) -> dict:
        """
        Get configuration summary for logging/debugging.

        Returns:
            Dictionary with non-sensitive configuration values
        """
        return {
            "app_version": cls.APP_VERSION,
            "dev_mode": cls.DEV_MODE,
            "debug": cls.DEBUG,
            "app_host": cls.APP_HOST,
            "app_port": cls.APP_PORT,
            "cors_origins": cls.CORS_ORIGINS if not cls.DEV_MODE else ["*"],
            "database_url": cls.DATABASE_URL.split("@")[-1] if "@" in cls.DATABASE_URL else "sqlite",
            "flaresolverr_configured": bool(cls.FLARESOLVERR_URL),
            "qbittorrent_configured": bool(cls.QBITTORRENT_HOST),
            "tracker_configured": bool(cls.TRACKER_URL and cls.TRACKER_PASSKEY),
            "tmdb_configured": bool(cls.TMDB_API_KEY),
            "api_timeout": cls.API_REQUEST_TIMEOUT,
            "circuit_breaker_max_failures": cls.CIRCUIT_BREAKER_MAX_FAILURES,
            "circuit_breaker_open_duration": cls.CIRCUIT_BREAKER_OPEN_DURATION,
            "max_retries": cls.MAX_RETRIES,
            "timezone": cls.TIMEZONE,
        }


# Singleton instance
config = Config()
