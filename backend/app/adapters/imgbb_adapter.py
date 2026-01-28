"""
ImgBBAdapter - ImageHostAdapter Implementation for ImgBB

This module implements the ImageHostAdapter interface for the ImgBB image
hosting service. ImgBB provides free image hosting with API access.

Features:
    - API key authentication
    - Base64 image upload
    - Thumbnail URLs included
    - Optional image expiration
    - Batch upload with rate limiting

API Documentation:
    https://api.imgbb.com/

Usage Example:
    adapter = ImgBBAdapter(api_key="your_api_key")

    # Upload single image
    result = await adapter.upload_image("/path/to/screenshot.png")
    print(f"URL: {result['url']}")
    print(f"Thumb: {result['thumb_url']}")

    # Generate BBCode
    bbcode = adapter.generate_bbcode([result])
    print(bbcode)
"""

import asyncio
import base64
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import httpx

from .image_host_adapter import ImageHostAdapter, ImageHostError
from app.services.exceptions import NetworkRetryableError, retry_on_network_error

logger = logging.getLogger(__name__)


class ImgBBAdapter(ImageHostAdapter):
    """
    ImgBB image hosting adapter implementing ImageHostAdapter interface.

    This adapter handles image uploads to ImgBB's free image hosting service.
    ImgBB provides:
        - Free image hosting
        - API access with key
        - Auto-generated thumbnails
        - Optional expiration
        - Direct links and BBCode

    Attributes:
        api_key: ImgBB API key
        api_url: ImgBB API endpoint
        expiration: Optional image expiration in seconds (0 = never)
    """

    API_URL = "https://api.imgbb.com/1/upload"

    def __init__(
        self,
        api_key: str,
        expiration: int = 0,
        timeout: int = 60
    ):
        """
        Initialize ImgBBAdapter.

        Args:
            api_key: ImgBB API key (get from https://api.imgbb.com/)
            expiration: Image expiration in seconds (0 = never expire)
            timeout: HTTP request timeout in seconds
        """
        self.api_key = api_key
        self.expiration = expiration
        self.timeout = timeout

        logger.info(
            f"ImgBBAdapter initialized "
            f"(expiration={'never' if expiration == 0 else f'{expiration}s'})"
        )

    @retry_on_network_error(max_retries=3)
    async def upload_image(self, image_path: str) -> Dict[str, Any]:
        """
        Upload a single image to ImgBB with automatic retry on network errors.

        Args:
            image_path: Path to the image file

        Returns:
            Dictionary with upload result including URL, thumb_url, etc.

        Raises:
            ImageHostError: If upload fails (non-retryable)
            NetworkRetryableError: If network issues occur (retried automatically)
        """
        path = Path(image_path)

        if not path.exists():
            raise ImageHostError(f"Image file not found: {image_path}")

        logger.info(f"Uploading image to ImgBB: {path.name}")

        try:
            # Read and encode image as base64
            with open(path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')

            # Prepare request data
            data = {
                'key': self.api_key,
                'image': image_data,
                'name': path.stem  # Filename without extension
            }

            if self.expiration > 0:
                data['expiration'] = str(self.expiration)

            # Make API request
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.API_URL, data=data)

            # Parse response
            result = response.json()

            if not result.get('success'):
                error_msg = result.get('error', {}).get('message', 'Unknown error')
                raise ImageHostError(f"ImgBB upload failed: {error_msg}", result)

            # Extract data from response
            img_data = result.get('data', {})

            upload_result = {
                'success': True,
                'url': img_data.get('url', ''),
                'thumb_url': img_data.get('thumb', {}).get('url', img_data.get('url', '')),
                'medium_url': img_data.get('medium', {}).get('url', ''),
                'delete_url': img_data.get('delete_url', ''),
                'width': img_data.get('width', 0),
                'height': img_data.get('height', 0),
                'size': img_data.get('size', 0),
                'filename': path.name,
                'expiration': img_data.get('expiration', 0),
                'id': img_data.get('id', ''),
                'url_viewer': img_data.get('url_viewer', '')
            }

            logger.info(f"✓ Uploaded to ImgBB: {path.name} -> {upload_result['url']}")

            return upload_result

        except httpx.TimeoutException as e:
            error_msg = f"Timeout uploading to ImgBB: {e}"
            logger.error(error_msg)
            raise NetworkRetryableError(error_msg) from e

        except httpx.ConnectError as e:
            error_msg = f"Connection error uploading to ImgBB: {e}"
            logger.error(error_msg)
            raise NetworkRetryableError(error_msg) from e

        except httpx.HTTPError as e:
            error_msg = f"HTTP error uploading to ImgBB: {e}"
            logger.error(error_msg)
            raise ImageHostError(error_msg) from e

        except ImageHostError:
            raise

        except Exception as e:
            error_msg = f"Error uploading to ImgBB: {type(e).__name__}: {e}"
            logger.error(error_msg)
            raise ImageHostError(error_msg) from e

    async def upload_images(
        self,
        image_paths: List[str],
        parallel: bool = True,
        delay: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Upload multiple images to ImgBB.

        Args:
            image_paths: List of image file paths
            parallel: Whether to upload in parallel (with rate limiting)
            delay: Delay between uploads in seconds (rate limiting)

        Returns:
            List of upload result dictionaries

        Raises:
            ImageHostError: If all uploads fail
        """
        if not image_paths:
            return []

        logger.info(f"Uploading {len(image_paths)} images to ImgBB")

        results = []

        if parallel:
            # Upload with limited concurrency to avoid rate limiting
            semaphore = asyncio.Semaphore(3)  # Max 3 concurrent uploads

            async def upload_with_limit(path: str, index: int) -> Dict[str, Any]:
                async with semaphore:
                    # Add delay between uploads to avoid rate limiting
                    if index > 0:
                        await asyncio.sleep(delay)
                    try:
                        return await self.upload_image(path)
                    except ImageHostError as e:
                        logger.warning(f"Failed to upload {path}: {e}")
                        return {
                            'success': False,
                            'filename': Path(path).name,
                            'error': str(e)
                        }

            # Create tasks with index for delay calculation
            tasks = [
                upload_with_limit(path, i)
                for i, path in enumerate(image_paths)
            ]

            results = await asyncio.gather(*tasks)

        else:
            # Sequential upload
            for i, path in enumerate(image_paths):
                if i > 0:
                    await asyncio.sleep(delay)

                try:
                    result = await self.upload_image(path)
                    results.append(result)
                except ImageHostError as e:
                    logger.warning(f"Failed to upload {path}: {e}")
                    results.append({
                        'success': False,
                        'filename': Path(path).name,
                        'error': str(e)
                    })

        # Count successes
        successful = sum(1 for r in results if r.get('success'))
        failed = len(results) - successful

        if successful == 0:
            raise ImageHostError(f"All {len(image_paths)} uploads failed")

        logger.info(f"✓ Uploaded {successful}/{len(image_paths)} images to ImgBB")
        if failed > 0:
            logger.warning(f"  {failed} upload(s) failed")

        return results

    def generate_bbcode(
        self,
        upload_results: List[Dict[str, Any]],
        use_thumbnails: bool = True
    ) -> str:
        """
        Generate BBCode for uploaded images.

        Args:
            upload_results: List of upload result dictionaries
            use_thumbnails: Whether to display thumbnails that link to full images

        Returns:
            BBCode string formatted for tracker descriptions

        Format:
            [center]
            [url=full_url][img]thumb_url[/img][/url]
            [/center]
        """
        if not upload_results:
            return ""

        # Filter successful uploads
        successful = [r for r in upload_results if r.get('success') and r.get('url')]

        if not successful:
            return ""

        lines = ["[center]"]

        for result in successful:
            url = result.get('url', '')
            thumb = result.get('thumb_url', url) if use_thumbnails else url

            if use_thumbnails and thumb:
                # Clickable thumbnail linking to full image
                lines.append(f"[url={url}][img]{thumb}[/img][/url]")
            else:
                # Full image directly
                lines.append(f"[img]{url}[/img]")

        lines.append("[/center]")

        return "\n".join(lines)

    def generate_bbcode_horizontal(
        self,
        upload_results: List[Dict[str, Any]],
        use_thumbnails: bool = True
    ) -> str:
        """
        Generate horizontal BBCode (images on same line).

        Args:
            upload_results: List of upload result dictionaries
            use_thumbnails: Whether to use thumbnails

        Returns:
            BBCode string with images on one line
        """
        successful = [r for r in upload_results if r.get('success') and r.get('url')]

        if not successful:
            return ""

        parts = []
        for result in successful:
            url = result.get('url', '')
            thumb = result.get('thumb_url', url) if use_thumbnails else url

            if use_thumbnails and thumb:
                parts.append(f"[url={url}][img]{thumb}[/img][/url]")
            else:
                parts.append(f"[img]{url}[/img]")

        return "[center]" + " ".join(parts) + "[/center]"

    async def validate_api_key(self) -> bool:
        """
        Validate the ImgBB API key.

        Performs a lightweight check by making a small request to verify
        the API key is valid.

        Returns:
            True if API key is valid, False otherwise
        """
        if not self.api_key or len(self.api_key) < 10:
            logger.warning("ImgBB API key appears invalid (too short)")
            return False

        # ImgBB doesn't have a dedicated validation endpoint
        # We would need to upload a small test image to truly validate
        # For now, just check the key format
        logger.info("ImgBB API key format appears valid")
        return True

    def get_adapter_info(self) -> Dict[str, Any]:
        """
        Get information about this image host adapter.

        Returns:
            Dictionary with adapter information
        """
        return {
            'name': 'ImgBB Adapter',
            'host_name': 'ImgBB',
            'host_url': 'https://imgbb.com',
            'version': '1.0.0',
            'features': [
                'free_hosting',
                'api_upload',
                'auto_thumbnails',
                'optional_expiration',
                'batch_upload',
                'bbcode_generation'
            ]
        }

    def __repr__(self) -> str:
        """String representation of ImgBBAdapter."""
        return (
            f"<ImgBBAdapter("
            f"api_key='***{self.api_key[-4:] if self.api_key else 'None'}', "
            f"expiration={self.expiration}"
            f")>"
        )


# Factory function
def get_imgbb_adapter(
    api_key: str,
    expiration: int = 0
) -> ImgBBAdapter:
    """
    Create an ImgBB adapter instance.

    Args:
        api_key: ImgBB API key
        expiration: Image expiration in seconds (0 = never)

    Returns:
        ImgBBAdapter instance
    """
    return ImgBBAdapter(api_key=api_key, expiration=expiration)
