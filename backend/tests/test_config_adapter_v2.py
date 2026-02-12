"""
Tests for Config-Driven Adapter v2.0

Run with: pytest backend/tests/test_config_adapter_v2.py -v
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import json

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.adapters.config_adapter import ConfigAdapter
from app.adapters.tracker_config_loader import TrackerConfigLoader, get_config_loader


class TestTrackerConfigLoader:
    """Test configuration loading and validation."""

    def test_load_torr9_config(self):
        """Test loading torr9.yaml configuration."""
        loader = TrackerConfigLoader()
        config = loader.load("torr9")

        assert config is not None
        assert config["tracker"]["name"] == "Torr9"
        assert config["tracker"]["slug"] == "torr9"
        assert config["auth"]["type"] == "bearer"
        assert "mappings" in config
        assert "workflow" in config

    def test_load_c411_config(self):
        """Test loading c411.yaml configuration."""
        loader = TrackerConfigLoader()
        config = loader.load("c411")

        assert config is not None
        assert config["tracker"]["name"] == "C411"
        assert config["auth"]["type"] == "bearer"
        assert "mappings" in config
        assert "options" in config  # Legacy options for OptionsMapper

    def test_load_lacale_config(self):
        """Test loading lacale.yaml configuration."""
        loader = TrackerConfigLoader()
        config = loader.load("lacale")

        assert config is not None
        assert config["tracker"]["name"] == "La Cale"
        assert config["auth"]["type"] == "passkey"
        assert config["cloudflare"]["enabled"] is True
        assert "mappings" in config

    def test_validate_mappings_section(self):
        """Test validation of mappings section."""
        loader = TrackerConfigLoader()

        # Valid mappings
        valid_config = {
            "tracker": {"name": "Test", "slug": "test"},
            "auth": {"type": "bearer"},
            "endpoints": {"upload": "/upload"},
            "upload": {"fields": {"torrent": {"type": "file"}}},
            "mappings": {
                "resolution": {
                    "input_field": "resolution",
                    "output_field": "resolution_id",
                    "values": {"1080p": "2"}
                }
            }
        }
        is_valid, errors = loader.validate(valid_config)
        assert is_valid, f"Validation failed: {errors}"

        # Invalid mappings (missing input_field)
        invalid_config = {
            "tracker": {"name": "Test", "slug": "test"},
            "auth": {"type": "bearer"},
            "endpoints": {"upload": "/upload"},
            "upload": {"fields": {"torrent": {"type": "file"}}},
            "mappings": {
                "resolution": {
                    "output_field": "resolution_id",
                    "values": {"1080p": "2"}
                }
            }
        }
        is_valid, errors = loader.validate(invalid_config)
        assert not is_valid
        assert any("input_field" in e for e in errors)

    def test_validate_workflow_section(self):
        """Test validation of workflow section."""
        loader = TrackerConfigLoader()

        # Valid workflow
        valid_config = {
            "tracker": {"name": "Test", "slug": "test"},
            "auth": {"type": "bearer"},
            "endpoints": {"upload": "/upload"},
            "workflow": [
                {
                    "name": "upload",
                    "method": "POST",
                    "url": "{tracker_url}/upload",
                    "type": "multipart",
                    "fields": {}
                }
            ]
        }
        is_valid, errors = loader.validate(valid_config)
        assert is_valid, f"Validation failed: {errors}"

        # Invalid workflow (missing url)
        invalid_config = {
            "tracker": {"name": "Test", "slug": "test"},
            "auth": {"type": "bearer"},
            "endpoints": {"upload": "/upload"},
            "workflow": [
                {
                    "name": "upload",
                    "method": "POST",
                    "type": "multipart"
                }
            ]
        }
        is_valid, errors = loader.validate(invalid_config)
        assert not is_valid
        assert any("url" in e for e in errors)

    def test_validate_dynamic_sources(self):
        """Test validation of dynamic_sources section."""
        loader = TrackerConfigLoader()

        # Valid dynamic sources
        valid_config = {
            "tracker": {"name": "Test", "slug": "test"},
            "auth": {"type": "bearer"},
            "endpoints": {"upload": "/upload"},
            "upload": {"fields": {"torrent": {"type": "file"}}},
            "dynamic_sources": {
                "categories": {
                    "endpoint": "/api/categories",
                    "response": {
                        "id_field": "id",
                        "name_field": "name"
                    }
                }
            }
        }
        is_valid, errors = loader.validate(valid_config)
        assert is_valid, f"Validation failed: {errors}"


class TestConfigAdapterMappings:
    """Test mappings resolution."""

    def setup_method(self):
        """Setup test config."""
        self.config = {
            "tracker": {"name": "Test", "slug": "test"},
            "auth": {"type": "bearer"},
            "endpoints": {"upload": "/upload"},
            "mappings": {
                "resolution": {
                    "input_field": "resolution",
                    "output_field": "resolution_id",
                    "default": "2",
                    "values": {
                        "2160p": "1",
                        "4k": "1",
                        "1080p": "2",
                        "720p": "5"
                    },
                    "fallback": "10"
                },
                "category": {
                    "input_field": "media_type",
                    "output_field": "category",
                    "values": {
                        "movie": "Films",
                        "tv": "Séries"
                    },
                    "fallback": "Films"
                },
                "language": {
                    "input_field": "languages",
                    "output_field": "language_ids",
                    "multi": True,
                    "values": {
                        "french": "1",
                        "english": "2",
                        "multi": "3"
                    },
                    "fallback": "1"
                }
            },
            "upload": {"fields": {"torrent": {"type": "file"}}}
        }
        self.adapter = ConfigAdapter(
            config=self.config,
            tracker_url="https://test.com",
            api_key="test_key"
        )

    def test_resolve_single_value_mapping(self):
        """Test resolving single value mappings."""
        # Mock file_entry
        file_entry = MagicMock()
        file_entry.resolution = "1080p"
        file_entry.media_type = "movie"

        resolved = self.adapter._resolve_all_mappings(file_entry, {})

        assert resolved["resolution_id"] == "2"
        assert resolved["category"] == "Films"

    def test_resolve_mapping_with_fallback(self):
        """Test fallback when no match found."""
        file_entry = MagicMock()
        file_entry.resolution = "unknown_resolution"
        file_entry.media_type = None

        resolved = self.adapter._resolve_all_mappings(file_entry, {})

        assert resolved["resolution_id"] == "10"  # fallback
        assert resolved["category"] is None  # default is None

    def test_resolve_mapping_case_insensitive(self):
        """Test case-insensitive matching."""
        file_entry = MagicMock()
        file_entry.resolution = "1080P"  # uppercase
        file_entry.media_type = "MOVIE"  # uppercase

        resolved = self.adapter._resolve_all_mappings(file_entry, {})

        assert resolved["resolution_id"] == "2"
        assert resolved["category"] == "Films"

    def test_resolve_multi_value_mapping(self):
        """Test multi-value mappings (like languages)."""
        file_entry = MagicMock()
        file_entry.resolution = None
        file_entry.media_type = None
        file_entry.languages = ["french", "english"]

        resolved = self.adapter._resolve_all_mappings(file_entry, {})

        assert resolved["language_ids"] == ["1", "2"]

    def test_resolve_from_kwargs(self):
        """Test resolving from kwargs when file_entry doesn't have the field."""
        file_entry = MagicMock(spec=[])  # No attributes

        resolved = self.adapter._resolve_all_mappings(
            file_entry,
            {"resolution": "720p", "media_type": "tv"}
        )

        assert resolved["resolution_id"] == "5"
        assert resolved["category"] == "Séries"


class TestConfigAdapterInterpolation:
    """Test variable interpolation."""

    def setup_method(self):
        self.config = {
            "tracker": {"name": "Test", "slug": "test"},
            "auth": {"type": "bearer"},
            "endpoints": {"upload": "/upload"},
            "upload": {"fields": {"torrent": {"type": "file"}}}
        }
        self.adapter = ConfigAdapter(
            config=self.config,
            tracker_url="https://test.com",
            api_key="test_key"
        )

    def test_interpolate_simple(self):
        """Test simple variable interpolation."""
        template = "{tracker_url}/api/upload"
        context = {"tracker_url": "https://example.com"}

        result = self.adapter._interpolate(template, context)

        assert result == "https://example.com/api/upload"

    def test_interpolate_multiple_vars(self):
        """Test multiple variable interpolation."""
        template = "{tracker_url}/torrent/{torrent_id}"
        context = {
            "tracker_url": "https://example.com",
            "torrent_id": "12345"
        }

        result = self.adapter._interpolate(template, context)

        assert result == "https://example.com/torrent/12345"

    def test_interpolate_with_passkey(self):
        """Test interpolation with passkey."""
        template = "{tracker_url}/upload?passkey={passkey}"
        context = {
            "tracker_url": "https://example.com",
            "passkey": "abc123"
        }

        result = self.adapter._interpolate(template, context)

        assert result == "https://example.com/upload?passkey=abc123"

    def test_interpolate_missing_var(self):
        """Test interpolation with missing variable (kept as-is)."""
        template = "{tracker_url}/upload?key={missing}"
        context = {"tracker_url": "https://example.com"}

        result = self.adapter._interpolate(template, context)

        assert result == "https://example.com/upload?key={missing}"


class TestConfigAdapterWorkflow:
    """Test workflow execution."""

    def setup_method(self):
        self.config = {
            "tracker": {"name": "Test", "slug": "test"},
            "auth": {"type": "bearer"},
            "endpoints": {"upload": "/upload"},
            "workflow": [
                {
                    "name": "upload",
                    "method": "POST",
                    "url": "{tracker_url}/api/upload",
                    "type": "multipart",
                    "fields": {
                        "torrent": {
                            "source": "torrent_data",
                            "type": "file",
                            "filename": "{release_name}.torrent",
                            "name": "torrent_file"
                        },
                        "title": {
                            "source": "release_name",
                            "type": "string",
                            "name": "title"
                        }
                    }
                }
            ],
            "response": {
                "success_field": "success",
                "torrent_id_field": "data.id"
            }
        }
        self.adapter = ConfigAdapter(
            config=self.config,
            tracker_url="https://test.com",
            api_key="test_key"
        )

    def test_build_request_body_multipart(self):
        """Test building multipart request body."""
        step = self.config["workflow"][0]
        context = {
            "torrent_data": b"torrent content",
            "release_name": "Test.Movie.2024.1080p"
        }

        body = self.adapter._build_request_body(step, context, "multipart")

        assert "files" in body
        assert "data" in body
        assert "torrent_file" in body["files"]
        assert body["files"]["torrent_file"][0] == "Test.Movie.2024.1080p.torrent"
        assert ("title", "Test.Movie.2024.1080p") in body["data"]

    @pytest.mark.asyncio
    async def test_execute_workflow_step(self):
        """Test executing a workflow step."""
        step = self.config["workflow"][0]
        context = {
            "tracker_url": "https://test.com",
            "torrent_data": b"torrent content",
            "release_name": "Test.Movie.2024.1080p"
        }

        # Mock the HTTP client
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True, "data": {"id": "123"}}
        mock_response.status_code = 200
        mock_response.cookies = {}
        mock_response.headers = {}

        with patch.object(self.adapter, '_get_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.headers = {}
            mock_get_client.return_value = mock_client

            result = await self.adapter._execute_step(step, context)

            assert result["response"] == mock_response
            assert result["step_name"] == "upload"


class TestConfigAdapterIntegration:
    """Integration tests with real config files."""

    def test_torr9_adapter_creation(self):
        """Test creating adapter from torr9 config."""
        loader = TrackerConfigLoader()
        config = loader.load("torr9")

        adapter = ConfigAdapter(
            config=config,
            tracker_url="https://torr9.example.com",
            api_key="test_api_key"
        )

        assert adapter.tracker_name == "Torr9"
        assert adapter.auth_type == "bearer"
        assert not adapter.requires_cloudflare

        # Test mappings resolution
        file_entry = MagicMock()
        file_entry.resolution = "1080p"
        file_entry.source = "web-dl"
        file_entry.media_type = "movie"

        resolved = adapter._resolve_all_mappings(file_entry, {})

        assert resolved["resolution_id"] == "2"
        assert resolved["type_id"] == "2"
        assert resolved["category"] == "Films"

    def test_c411_adapter_creation(self):
        """Test creating adapter from c411 config."""
        loader = TrackerConfigLoader()
        config = loader.load("c411")

        adapter = ConfigAdapter(
            config=config,
            tracker_url="https://c411.example.com",
            api_key="test_api_key"
        )

        assert adapter.tracker_name == "C411"
        assert adapter.auth_type == "bearer"

        # Test legacy options are preserved
        assert "options" in config
        assert "language" in config["options"]
        assert "quality" in config["options"]

    def test_lacale_adapter_creation(self):
        """Test creating adapter from lacale config."""
        loader = TrackerConfigLoader()
        config = loader.load("lacale")

        adapter = ConfigAdapter(
            config=config,
            tracker_url="https://lacale.example.com",
            passkey="test_passkey_12345"
        )

        assert adapter.tracker_name == "La Cale"
        assert adapter.auth_type == "passkey"
        assert adapter.requires_cloudflare

    def test_adapter_info(self):
        """Test get_adapter_info returns correct features."""
        loader = TrackerConfigLoader()
        config = loader.load("torr9")

        adapter = ConfigAdapter(
            config=config,
            tracker_url="https://torr9.example.com",
            api_key="test_api_key"
        )

        info = adapter.get_adapter_info()

        assert info["version"] == "2.0.0"
        assert "config_driven" in info["features"]
        assert "workflow" in info["features"]
        assert "mappings" in info["features"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
