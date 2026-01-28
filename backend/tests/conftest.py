"""
Pytest configuration for backend tests.

This file configures pytest for the backend test suite, including
fixtures and test discovery settings.
"""

import sys
from pathlib import Path

# Add backend directory to Python path for imports
backend_root = Path(__file__).parent.parent
sys.path.insert(0, str(backend_root))


def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as an async test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
    config.addinivalue_line(
        "markers", "e2e: mark test as an end-to-end test"
    )
