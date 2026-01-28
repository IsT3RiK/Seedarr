"""
ImageHostAdapter Abstract Base Class for Seedarr v2.1

This module defines the ImageHostAdapter abstract base class (ABC) that establishes
the contract interface for all image hosting implementations. This abstraction allows
the pipeline to work with any image hosting service without knowing service-specific details.

Architecture Pattern:
    - Pipeline depends only on ImageHostAdapter interface
    - Concrete adapters (ImgBBAdapter, etc.) implement the interface
    - Image host selection configurable via Settings
    - Easy to add new image host support by implementing this interface

Contract Methods:
    - upload_image(): Upload a single image
    - upload_images(): Upload multiple images (batch)
    - generate_bbcode(): Generate BBCode for uploaded images

Usage Example:
    adapter: ImageHostAdapter = get_image_host_adapter()

    # Upload single image
    result = await adapter.upload_image("/path/to/screenshot.png")
    # result = {'url': 'https://...', 'thumb_url': 'https://...', 'delete_url': '...'}

    # Upload batch
    results = await adapter.upload_images(["/path/to/screen1.png", "/path/to/screen2.png"])

    # Generate BBCode
    bbcode = adapter.generate_bbcode(results)
    # [url=full_url][img]thumb_url[/img][/url]
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional


class ImageHostAdapter(ABC):
    """
    Abstract base class defining the contract for image hosting adapters.

    All image host implementations must inherit from this class and implement
    all abstract methods. This ensures consistent interface across different
    image hosting backends.

    The adapter handles all host-specific logic:
        - Authentication (API keys, etc.)
        - Image upload formatting
        - Response parsing
        - BBCode generation
        - Error handling
    """

    @abstractmethod
    async def upload_image(self, image_path: str) -> Dict[str, Any]:
        """
        Upload a single image to the hosting service.

        Args:
            image_path: Path to the image file to upload

        Returns:
            Dictionary with upload result:
                {
                    'success': bool,
                    'url': str,           # Direct link to full image
                    'thumb_url': str,     # Thumbnail URL (if available)
                    'delete_url': str,    # Deletion URL (if available)
                    'width': int,         # Image width (if available)
                    'height': int,        # Image height (if available)
                    'size': int,          # File size in bytes
                    'filename': str,      # Original filename
                    'expiration': int     # Expiration time (0 = never)
                }

        Raises:
            ImageHostError: If upload fails

        Example:
            result = await adapter.upload_image("/path/to/screen.png")
            if result['success']:
                print(f"Uploaded: {result['url']}")
        """
        pass

    @abstractmethod
    async def upload_images(
        self,
        image_paths: List[str],
        parallel: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Upload multiple images to the hosting service.

        Args:
            image_paths: List of image file paths to upload
            parallel: Whether to upload in parallel (default True)

        Returns:
            List of upload result dictionaries (same format as upload_image)

        Raises:
            ImageHostError: If all uploads fail

        Example:
            results = await adapter.upload_images([
                "/path/to/screen1.png",
                "/path/to/screen2.png"
            ])
            for result in results:
                if result['success']:
                    print(f"Uploaded: {result['url']}")
        """
        pass

    @abstractmethod
    def generate_bbcode(
        self,
        upload_results: List[Dict[str, Any]],
        use_thumbnails: bool = True
    ) -> str:
        """
        Generate BBCode for uploaded images.

        Creates BBCode format suitable for tracker descriptions, with
        clickable thumbnails linking to full-size images.

        Args:
            upload_results: List of upload result dictionaries
            use_thumbnails: Whether to use thumbnail images (default True)

        Returns:
            BBCode string with all images

        Format (with thumbnails):
            [url=full_url][img]thumb_url[/img][/url]

        Format (without thumbnails):
            [img]full_url[/img]

        Example:
            bbcode = adapter.generate_bbcode(results)
            # [url=https://...][img]https://thumb...[/img][/url]
            # [url=https://...][img]https://thumb...[/img][/url]
        """
        pass

    @abstractmethod
    async def validate_api_key(self) -> bool:
        """
        Validate the API key with the hosting service.

        Returns:
            True if API key is valid, False otherwise

        Example:
            if await adapter.validate_api_key():
                print("API key is valid")
        """
        pass

    @abstractmethod
    def get_adapter_info(self) -> Dict[str, Any]:
        """
        Get information about this image host adapter.

        Returns:
            Dictionary with adapter information:
                {
                    'name': str,          # Adapter name
                    'host_name': str,     # Image host name
                    'host_url': str,      # Image host website
                    'version': str,       # Adapter version
                    'features': List[str] # Supported features
                }
        """
        pass


class ImageHostError(Exception):
    """Exception raised when image host operation fails."""

    def __init__(self, message: str, response_data: Optional[Dict] = None):
        """
        Initialize ImageHostError.

        Args:
            message: Error message
            response_data: Optional response data from the API
        """
        super().__init__(message)
        self.response_data = response_data
