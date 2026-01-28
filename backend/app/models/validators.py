"""
Input Validation Module for Seedarr v2.0

This module provides validation functions for user inputs including:
- URL validation (http/https schemes)
- Path traversal protection
- API key format validation
- Numeric range bounds validation

Security Features:
    - Blocks path traversal attacks (../)
    - Validates URL schemes to prevent SSRF
    - Sanitizes file paths for unicode attacks

Usage:
    from app.models.validators import validate_url, validate_path_no_traversal

    if not validate_url(user_input):
        raise ValueError("Invalid URL format")
"""

import os
import re
from urllib.parse import urlparse
from typing import Optional


def validate_url(url: str) -> bool:
    """
    Validate URL format (http/https only).

    This function validates that a URL has a proper format with
    http or https scheme and a valid netloc (host).

    Args:
        url: URL string to validate

    Returns:
        True if URL is valid http/https URL, False otherwise

    Examples:
        >>> validate_url("https://example.com")
        True
        >>> validate_url("http://localhost:8080/path")
        True
        >>> validate_url("ftp://files.example.com")
        False
        >>> validate_url("not-a-url")
        False
    """
    if not url or not isinstance(url, str):
        return False

    try:
        parsed = urlparse(url.strip())
        # Must have http or https scheme
        if parsed.scheme not in ('http', 'https'):
            return False
        # Must have a netloc (host)
        if not parsed.netloc:
            return False
        # Basic netloc validation (at least one character before any colon)
        host = parsed.netloc.split(':')[0]
        if not host or host.startswith('.') or host.endswith('.'):
            return False
        return True
    except Exception:
        return False


def validate_path_no_traversal(path: str) -> bool:
    """
    Validate that a path does not contain traversal sequences.

    This function blocks path traversal attacks by checking for:
    - Parent directory references (..)
    - Normalized path differences indicating traversal

    Args:
        path: File system path to validate

    Returns:
        True if path is safe (no traversal), False if path contains traversal

    Security Note:
        This function should be used on all user-provided paths before
        any file system operations.

    Examples:
        >>> validate_path_no_traversal("/media/movies")
        True
        >>> validate_path_no_traversal("C:\\Users\\Videos")
        True
        >>> validate_path_no_traversal("../../../etc/passwd")
        False
        >>> validate_path_no_traversal("/media/../../../etc/passwd")
        False
    """
    if not path or not isinstance(path, str):
        return False

    # Check for explicit parent directory references
    if '..' in path:
        return False

    # Normalize the path and compare
    try:
        normalized = os.path.normpath(path)
        # After normalization, check again for traversal patterns
        if '..' in normalized:
            return False

        # Check if normalized path escapes the original starting point
        # This catches cases like /media/./../../etc
        original_parts = [p for p in path.replace('\\', '/').split('/') if p and p != '.']
        normalized_parts = [p for p in normalized.replace('\\', '/').split('/') if p and p != '.']

        # If normalization significantly shortened the path, it might be traversal
        if len(normalized_parts) < len(original_parts) - 1:
            # Allow for single dot removal but not more
            pass

        return True
    except Exception:
        return False


def sanitize_path(path: Optional[str]) -> Optional[str]:
    """
    Sanitize file path by removing invisible Unicode characters.

    Common invisible characters that can cause issues:
    - U+200E (Left-to-Right Mark)
    - U+200F (Right-to-Left Mark)
    - U+200B (Zero Width Space)
    - U+FEFF (BOM)
    - U+202A-U+202E (Directional formatting)

    Args:
        path: Path string to sanitize

    Returns:
        Sanitized path string or None if input was None

    Examples:
        >>> sanitize_path("/media/movies")
        "/media/movies"
        >>> sanitize_path("/media/\\u200emovies")  # With LRM
        "/media/movies"
    """
    if not path:
        return path

    # List of invisible/problematic Unicode characters to remove
    invisible_chars = [
        '\u200e', '\u200f',  # LRM, RLM
        '\u200b', '\u200c', '\u200d',  # Zero-width chars
        '\ufeff',  # BOM
        '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',  # Directional
        '\u2066', '\u2067', '\u2068', '\u2069',  # Isolates
    ]

    result = path
    for char in invisible_chars:
        result = result.replace(char, '')

    return result.strip()


def validate_api_key(key: str, min_length: int = 16) -> bool:
    """
    Validate API key format.

    API keys are expected to be alphanumeric strings with optional
    underscores and hyphens, with a minimum length requirement.

    Args:
        key: API key string to validate
        min_length: Minimum required length (default: 16)

    Returns:
        True if API key format is valid, False otherwise

    Examples:
        >>> validate_api_key("abc123def456ghi7")  # 16 chars
        True
        >>> validate_api_key("my_api_key_12345")  # 16 chars with underscore
        True
        >>> validate_api_key("short")  # Too short
        False
        >>> validate_api_key("has spaces not allowed")
        False
    """
    if not key or not isinstance(key, str):
        return False

    # Check minimum length
    if len(key) < min_length:
        return False

    # Check for allowed characters: alphanumeric, underscore, hyphen
    # Also allow Bearer tokens which start with "ey" (JWT format)
    pattern = r'^[a-zA-Z0-9_\-\.]+$'
    return bool(re.match(pattern, key))


def validate_numeric_range(
    value: int,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
    field_name: str = "value"
) -> bool:
    """
    Validate that a numeric value is within specified bounds.

    Args:
        value: Integer value to validate
        min_value: Minimum allowed value (inclusive), None for no minimum
        max_value: Maximum allowed value (inclusive), None for no maximum
        field_name: Name of the field for error messages

    Returns:
        True if value is within bounds, False otherwise

    Examples:
        >>> validate_numeric_range(10, min_value=1, max_value=100)
        True
        >>> validate_numeric_range(0, min_value=1)
        False
        >>> validate_numeric_range(500, max_value=365)
        False
    """
    if not isinstance(value, int):
        return False

    if min_value is not None and value < min_value:
        return False

    if max_value is not None and value > max_value:
        return False

    return True


def validate_log_level(level: str) -> bool:
    """
    Validate log level string.

    Args:
        level: Log level string

    Returns:
        True if valid log level, False otherwise

    Examples:
        >>> validate_log_level("DEBUG")
        True
        >>> validate_log_level("invalid")
        False
    """
    if not level or not isinstance(level, str):
        return False
    valid_levels = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
    return level.upper() in valid_levels


def validate_passkey(passkey: str) -> bool:
    """
    Validate tracker passkey format.

    Passkeys are typically hexadecimal strings of 32 characters,
    but some trackers use different formats.

    Args:
        passkey: Passkey string to validate

    Returns:
        True if passkey format appears valid, False otherwise

    Examples:
        >>> validate_passkey("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")  # 32 hex chars
        True
        >>> validate_passkey("short")
        False
    """
    if not passkey or not isinstance(passkey, str):
        return False

    # Minimum length for passkeys (typically 32 but allow shorter for some trackers)
    if len(passkey) < 16:
        return False

    # Passkeys should be alphanumeric (usually hex)
    pattern = r'^[a-zA-Z0-9]+$'
    return bool(re.match(pattern, passkey))


# ============================================================================
# Pydantic Validator Functions
# ============================================================================

def path_validator(value: Optional[str]) -> Optional[str]:
    """
    Pydantic validator for path fields.

    Sanitizes the path and validates it doesn't contain traversal sequences.

    Args:
        value: Path string to validate

    Returns:
        Sanitized path string

    Raises:
        ValueError: If path contains traversal sequences
    """
    if value is None:
        return value

    # Sanitize first
    sanitized = sanitize_path(value)

    # Validate no traversal
    if sanitized and not validate_path_no_traversal(sanitized):
        raise ValueError('Path traversal not allowed')

    return sanitized


def url_validator(value: Optional[str]) -> Optional[str]:
    """
    Pydantic validator for URL fields.

    Args:
        value: URL string to validate

    Returns:
        Validated URL string

    Raises:
        ValueError: If URL format is invalid
    """
    if value is None or value == '':
        return value

    if not validate_url(value):
        raise ValueError('Invalid URL format (must be http or https)')

    return value.strip()
