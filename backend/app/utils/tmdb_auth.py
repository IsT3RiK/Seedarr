"""
TMDB Authentication Utility Module

This module provides utilities for detecting and handling TMDB API authentication
methods (v3 API key vs v4 Bearer token).

TMDB supports two authentication methods:
    - v3 API Key: 32-character alphanumeric string passed as query parameter
    - v4 Bearer Token: JWT format token passed as Authorization header

Functions:
    detect_tmdb_credential_type(api_key: str) -> str
        Detect whether credential is v3 API key or v4 Bearer token

    format_tmdb_request(api_key: str) -> tuple[dict, dict]
        Format request parameters and headers based on credential type
"""

from typing import Tuple, Dict


def detect_tmdb_credential_type(api_key: str) -> str:
    """
    Detect whether credential is v3 API key or v4 Bearer token.

    TMDB v3 API keys are 32-character alphanumeric strings (e.g., df667ef7a7f9009def29e0bd78725f3d).
    TMDB v4 Bearer tokens are JWT format strings starting with 'eyJ' (base64 encoded JSON header).

    Args:
        api_key: The TMDB credential to detect

    Returns:
        str: 'v3' for API key authentication, 'v4' for Bearer token authentication

    Raises:
        ValueError: If credential format is invalid (not v3 or v4)

    Example:
        >>> detect_tmdb_credential_type('df667ef7a7f9009def29e0bd78725f3d')
        'v3'
        >>> detect_tmdb_credential_type('eyJhbGciOiJIUzI1NiJ9...')
        'v4'
    """
    if not api_key or not isinstance(api_key, str):
        raise ValueError(
            "Invalid TMDB credential format. Expected v3 API key (32 alphanumeric) "
            "or v4 Bearer token (JWT)"
        )

    # v4 Bearer tokens are JWT format, always start with 'eyJ' (base64 encoded '{"alg":')
    if api_key.startswith('eyJ'):
        return 'v4'

    # v3 API keys are exactly 32 alphanumeric characters
    elif len(api_key) == 32 and api_key.isalnum():
        return 'v3'

    else:
        raise ValueError(
            "Invalid TMDB credential format. Expected v3 API key (32 alphanumeric) "
            "or v4 Bearer token (JWT)"
        )


def format_tmdb_request(api_key: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Format request parameters and headers based on credential type.

    v3 API keys are passed as query parameter: ?api_key=<key>
    v4 Bearer tokens are passed as Authorization header: Authorization: Bearer <token>

    Args:
        api_key: The TMDB credential to format

    Returns:
        tuple: (params_dict, headers_dict)
            - params_dict: Query parameters to add to request URL
            - headers_dict: HTTP headers to add to request

    Raises:
        ValueError: If credential format is invalid

    Example:
        >>> format_tmdb_request('df667ef7a7f9009def29e0bd78725f3d')
        ({'api_key': 'df667ef7a7f9009def29e0bd78725f3d'}, {})

        >>> format_tmdb_request('eyJhbGciOiJIUzI1NiJ9...')
        ({}, {'Authorization': 'Bearer eyJhbGciOiJIUzI1NiJ9...'})
    """
    credential_type = detect_tmdb_credential_type(api_key)

    if credential_type == 'v3':
        # v3 authentication: pass API key as query parameter
        return {'api_key': api_key}, {}

    elif credential_type == 'v4':
        # v4 authentication: pass Bearer token as Authorization header
        return {}, {'Authorization': f'Bearer {api_key}'}

    else:
        # This should never happen if detect_tmdb_credential_type works correctly
        raise ValueError(f"Unknown credential type: {credential_type}")
