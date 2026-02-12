"""
TMDB Cache Service for Seedarr v2.0

This service implements cache-first lookup strategy for TMDB API responses,
significantly reducing API calls and improving performance through persistent
database caching.

Key Features:
    - Cache-first lookup (check database before API call)
    - Persistent storage (survives application restart)
    - Automatic cache population on API fetch
    - Configurable TTL (default 30 days from Settings)
    - Automatic expiration handling

Performance Benefits:
    - Expected cache hit rate: >90% for repeated lookups
    - Expected reduction in TMDB API calls: >80%
    - Fast lookups via indexed database queries

Usage Example:
    >>> from app.database import SessionLocal
    >>> from app.services.tmdb_cache_service import TMDBCacheService
    >>>
    >>> db = SessionLocal()
    >>> cache_service = TMDBCacheService(db)
    >>>
    >>> # Cache-first lookup (checks cache, then API if needed)
    >>> metadata = await cache_service.get_metadata("12345")
    >>> print(metadata['title'])  # Returns cached or fresh data
"""

import logging
import os
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from app.models.tmdb_cache import TMDBCache
from app.models.settings import Settings
from app.services.exceptions import TrackerAPIError, NetworkRetryableError, retry_on_network_error
from app.services.rate_limiter import rate_limited
from app.utils.tmdb_auth import detect_tmdb_credential_type, format_tmdb_request

logger = logging.getLogger(__name__)


class TMDBCacheService:
    """
    Service for cache-first TMDB metadata lookups with persistent storage.

    This service implements a cache-first strategy where the database is always
    checked before making API calls. If data is cached and not expired, it's
    returned immediately. Otherwise, data is fetched from TMDB API and cached.

    Architecture:
        1. Check cache via TMDBCache.get_cached() (auto-expires stale entries)
        2. If cache hit: return cached data (fast path)
        3. If cache miss: fetch from TMDB API
        4. Store/update cache via TMDBCache.upsert()
        5. Return fresh data

    Thread Safety:
        This service is thread-safe when using SQLAlchemy session management
        with proper connection pooling. Each request should use its own
        database session.

    Example:
        >>> cache_service = TMDBCacheService(db)
        >>> metadata = await cache_service.get_metadata("550")
        >>> print(metadata['title'])  # "Fight Club"
    """

    def __init__(self, db: Session):
        """
        Initialize TMDB cache service.

        Args:
            db: SQLAlchemy database session for cache operations
        """
        self.db = db
        self._api_key: Optional[str] = None

    def _get_api_key(self) -> str:
        """
        Get TMDB API key from settings or environment.

        Returns:
            TMDB API key

        Raises:
            TrackerAPIError: If API key not configured
        """
        if self._api_key:
            return self._api_key

        # Try to get from Settings model (database-first approach)
        try:
            settings = Settings.get_settings(self.db)
            if settings and settings.tmdb_api_key:
                self._api_key = settings.tmdb_api_key
                return self._api_key
        except Exception as e:
            logger.debug(f"Could not get API key from Settings: {e}")

        # Fallback to environment variable
        api_key = os.environ.get('TMDB_API_KEY')
        if api_key:
            self._api_key = api_key
            return self._api_key

        raise TrackerAPIError(
            "TMDB API key not configured. Please set in Settings or TMDB_API_KEY "
            "environment variable."
        )

    def _get_cache_ttl_days(self) -> int:
        """
        Get cache TTL from settings.

        Returns:
            Cache TTL in days (default: 30)
        """
        try:
            settings = Settings.get_settings(self.db)
            if settings and settings.tmdb_cache_ttl_days:
                return settings.tmdb_cache_ttl_days
        except Exception as e:
            logger.debug(f"Could not get cache TTL from Settings: {e}")

        # Default to 30 days if not configured
        return 30

    async def get_metadata(
        self,
        tmdb_id: str,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Get TMDB metadata with cache-first lookup.

        This is the primary method for retrieving TMDB metadata. It implements
        cache-first strategy:
        1. Check cache (unless force_refresh=True)
        2. Return cached data if found and not expired
        3. Fetch from API if cache miss or force_refresh
        4. Store in cache for future requests
        5. Return metadata

        Args:
            tmdb_id: TMDB movie/TV show ID
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            Dictionary with metadata fields:
                - tmdb_id: TMDB ID
                - title: Movie/TV show title
                - year: Release/first air year
                - cast: List of cast members
                - plot: Plot summary/overview
                - ratings: Rating information (vote_average, vote_count)
                - cached_at: When data was cached (ISO format)
                - expires_at: When cache expires (ISO format)

        Raises:
            TrackerAPIError: If metadata fetch fails
            NetworkRetryableError: If API request fails (retryable)

        Example:
            >>> metadata = await cache_service.get_metadata("550")
            >>> print(f"{metadata['title']} ({metadata['year']})")
            >>> # Output: "Fight Club (1999)"
        """
        logger.debug(f"get_metadata called for tmdb_id={tmdb_id}, force_refresh={force_refresh}")

        # Step 1: Check cache (unless force refresh requested)
        if not force_refresh:
            cache_entry = TMDBCache.get_cached(self.db, tmdb_id)
            if cache_entry:
                logger.info(f"✓ Cache HIT for tmdb_id={tmdb_id}")
                return cache_entry.to_dict()
            else:
                logger.info(f"✗ Cache MISS for tmdb_id={tmdb_id}")

        # Step 2: Fetch from TMDB API
        logger.info(f"Fetching fresh metadata from TMDB API for tmdb_id={tmdb_id}")
        metadata = await self._fetch_from_api(tmdb_id)

        # Step 3: Store in cache
        ttl_days = self._get_cache_ttl_days()
        cache_entry = TMDBCache.upsert(
            db=self.db,
            tmdb_id=tmdb_id,
            title=metadata['title'],
            year=metadata.get('year'),
            cast=metadata.get('cast', []),
            plot=metadata.get('plot'),
            ratings=metadata.get('ratings', {}),
            extra_data=metadata.get('extra_data', {}),
            ttl_days=ttl_days
        )

        logger.info(f"✓ Cached metadata for tmdb_id={tmdb_id} (TTL: {ttl_days} days)")

        # Step 4: Return fresh data
        return cache_entry.to_dict()

    @rate_limited(service="tmdb", tokens=1)
    @retry_on_network_error(max_retries=3)
    async def _fetch_from_api(self, tmdb_id: str) -> Dict[str, Any]:
        """
        Fetch metadata from TMDB API with automatic retry on network errors.

        This method is called when cache miss occurs or force_refresh is requested.
        It makes HTTP request to TMDB API and parses the response.

        Supports both TMDB authentication methods:
        - v3 API Key: Passed as query parameter ?api_key=<key>
        - v4 Bearer Token: Passed as Authorization header

        Args:
            tmdb_id: TMDB movie/TV show ID

        Returns:
            Dictionary with metadata fields

        Raises:
            NetworkRetryableError: If API request fails (network/timeout) - retried automatically
            TrackerAPIError: If API returns invalid response (non-retryable)

        Note:
            This method uses @retry_on_network_error decorator for automatic
            retry with exponential backoff on transient failures.
        """
        import asyncio
        import requests  # Import here to avoid loading if not needed

        api_key = self._get_api_key()

        # Detect credential type and format request accordingly
        try:
            credential_type = detect_tmdb_credential_type(api_key)
            params, headers = format_tmdb_request(api_key)
            logger.debug(f"Using TMDB {credential_type} authentication for tmdb_id={tmdb_id}")
        except ValueError as e:
            raise TrackerAPIError(f"Invalid TMDB credential: {e}")

        # TMDB API endpoint for movie details
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"

        # Add language and append_to_response to params (works for both v3 and v4)
        params['language'] = 'fr-FR'  # French language for metadata
        params['append_to_response'] = 'credits'  # Include cast information

        try:
            # Use asyncio.to_thread to avoid blocking event loop
            response = await asyncio.to_thread(
                requests.get,
                url,
                params=params,
                headers=headers,
                timeout=10  # 10 second timeout
            )

            # Check HTTP status
            if response.status_code == 404:
                raise TrackerAPIError(f"TMDB movie not found: tmdb_id={tmdb_id}")
            elif response.status_code == 401:
                raise TrackerAPIError("TMDB API authentication failed. Check API key.")
            elif response.status_code == 429:
                # Rate limiting - this is retryable
                raise NetworkRetryableError(
                    f"TMDB API rate limit exceeded for tmdb_id={tmdb_id}"
                )
            elif response.status_code >= 500:
                # Server error - retryable
                raise NetworkRetryableError(
                    f"TMDB API server error (HTTP {response.status_code})"
                )
            elif response.status_code != 200:
                raise TrackerAPIError(
                    f"TMDB API returned HTTP {response.status_code} for tmdb_id={tmdb_id}"
                )

            # Parse JSON response
            data = response.json()

            # Extract metadata
            metadata = {
                'tmdb_id': str(tmdb_id),
                'title': data.get('title', 'Unknown'),
                'year': None,
                'cast': [],
                'plot': data.get('overview', ''),
                'ratings': {},
                'extra_data': {}
            }

            # Extract year from release_date (format: "YYYY-MM-DD")
            release_date = data.get('release_date', '')
            if release_date and len(release_date) >= 4:
                try:
                    metadata['year'] = int(release_date[:4])
                except ValueError:
                    logger.warning(f"Could not parse year from release_date: {release_date}")

            # Extract cast (limit to top 10) with profile photos
            if 'credits' in data and 'cast' in data['credits']:
                cast_list = data['credits']['cast'][:10]
                metadata['cast'] = [
                    {
                        'name': actor.get('name'),
                        'character': actor.get('character'),
                        'profile_path': actor.get('profile_path')
                    }
                    for actor in cast_list
                ]

            # Extract ratings
            if 'vote_average' in data:
                metadata['ratings'] = {
                    'vote_average': data['vote_average'],
                    'vote_count': data.get('vote_count', 0)
                }

            # Store additional metadata for future use
            metadata['extra_data'] = {
                'original_title': data.get('original_title'),
                'original_language': data.get('original_language'),
                'runtime': data.get('runtime'),
                'release_date': data.get('release_date', ''),
                'production_countries': data.get('production_countries', []),
                'imdb_id': data.get('imdb_id', ''),
                # Store full genre objects with ID and name for C411 compatibility
                'genres': [{'id': g.get('id'), 'name': g.get('name')} for g in data.get('genres', [])],
                'poster_path': data.get('poster_path'),
                'backdrop_path': data.get('backdrop_path')
            }

            logger.debug(f"Successfully fetched metadata for tmdb_id={tmdb_id}: {metadata['title']}")
            return metadata

        except requests.exceptions.Timeout:
            raise NetworkRetryableError(f"TMDB API timeout for tmdb_id={tmdb_id}")
        except requests.exceptions.ConnectionError:
            raise NetworkRetryableError(f"TMDB API connection error for tmdb_id={tmdb_id}")
        except requests.exceptions.RequestException as e:
            # Other request errors (retryable)
            raise NetworkRetryableError(f"TMDB API request failed: {e}")
        except Exception as e:
            # Unexpected errors (non-retryable)
            raise TrackerAPIError(f"Unexpected error fetching TMDB metadata: {e}")

    def invalidate_cache(self, tmdb_id: str) -> bool:
        """
        Invalidate (delete) cache entry for specific TMDB ID.

        This is useful for forcing refresh of stale or incorrect data.

        Args:
            tmdb_id: TMDB movie/TV show ID to invalidate

        Returns:
            True if cache entry was deleted, False if not found

        Example:
            >>> cache_service.invalidate_cache("550")
            >>> # Next get_metadata("550") will fetch fresh data
        """
        cache_entry = self.db.query(TMDBCache).filter(
            TMDBCache.tmdb_id == str(tmdb_id)
        ).first()

        if cache_entry:
            self.db.delete(cache_entry)
            self.db.commit()
            logger.info(f"✓ Invalidated cache for tmdb_id={tmdb_id}")
            return True
        else:
            logger.debug(f"No cache entry found to invalidate for tmdb_id={tmdb_id}")
            return False

    def cleanup_expired(self) -> int:
        """
        Delete all expired cache entries.

        This can be called periodically (e.g., daily background task) to clean
        up stale cache entries and reduce database size.

        Returns:
            Number of expired entries deleted

        Example:
            >>> deleted_count = cache_service.cleanup_expired()
            >>> logger.info(f"Cleaned up {deleted_count} expired cache entries")
        """
        deleted_count = TMDBCache.cleanup_expired(self.db)
        logger.info(f"✓ Cleaned up {deleted_count} expired TMDB cache entries")
        return deleted_count

    async def search_by_title(
        self,
        title: str,
        year: Optional[int] = None,
        content_type: str = "movie"
    ) -> Optional[Dict[str, Any]]:
        """
        Search TMDB by title and optionally year.

        This method searches TMDB for a movie or TV show by title,
        then fetches and caches the full metadata.

        Args:
            title: Movie or TV show title to search for
            year: Optional release year to narrow results
            content_type: "movie" or "tv" (default: "movie")

        Returns:
            Dictionary with metadata if found, None otherwise

        Example:
            >>> result = await cache_service.search_by_title("R.I.P.D.", 2013)
            >>> print(result['tmdb_id'])  # "49009"
        """
        import asyncio
        import requests

        api_key = self._get_api_key()

        # Detect credential type and format request
        try:
            params, headers = format_tmdb_request(api_key)
        except ValueError as e:
            raise TrackerAPIError(f"Invalid TMDB credential: {e}")

        # TMDB search endpoint
        search_type = "tv" if content_type.lower() in ["tv", "series", "show"] else "movie"
        url = f"https://api.themoviedb.org/3/search/{search_type}"

        # Add search parameters
        params['query'] = title
        params['language'] = 'fr-FR'  # French language for search results
        if year:
            params['year' if search_type == "movie" else 'first_air_date_year'] = year

        logger.info(f"Searching TMDB for: '{title}' ({year or 'any year'}), type={search_type}")

        try:
            response = await asyncio.to_thread(
                requests.get,
                url,
                params=params,
                headers=headers,
                timeout=10
            )

            if response.status_code != 200:
                logger.warning(f"TMDB search failed with HTTP {response.status_code}")
                return None

            data = response.json()
            results = data.get('results', [])

            if not results:
                logger.warning(f"No TMDB results found for: '{title}'")
                return None

            # Take the first result (best match)
            best_match = results[0]
            tmdb_id = str(best_match.get('id'))

            logger.info(f"Found TMDB match: {best_match.get('title', best_match.get('name'))} (ID: {tmdb_id})")

            # Fetch full metadata and cache it
            return await self.get_metadata(tmdb_id)

        except requests.exceptions.RequestException as e:
            logger.error(f"TMDB search request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in TMDB search: {e}")
            return None

    async def search_and_get_metadata(
        self,
        filename: str,
        is_tv_show: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Parse filename, extract title/year, and fetch TMDB metadata.

        This is a convenience method that handles the full workflow:
        1. Parse filename to extract title and year
        2. Search TMDB for the content
        3. Return full metadata including poster URL

        Args:
            filename: Movie/TV show filename to parse
            is_tv_show: Whether the content is a TV show

        Returns:
            Dictionary with metadata including:
                - tmdb_id: TMDB ID
                - title: Title
                - year: Release year
                - plot: Plot summary
                - poster_url: Full poster URL
                - backdrop_url: Full backdrop URL
                - genres: List of genres

        Example:
            >>> result = await cache_service.search_and_get_metadata(
            ...     "R.I.P.D. (2013) MULTi VFF 2160p.mkv"
            ... )
        """
        import re
        from pathlib import Path

        # filename can be a full path or just a filename
        file_path = Path(filename)
        name_without_ext = file_path.stem

        # Extract title and year from filename
        # Pattern: "Title (Year)" or "Title.Year." or "Title 2013" or "Title_(Year)_"
        patterns = [
            r'^(.+?)\s*\((\d{4})\)',   # Title (2013) or Title_(2013)
            r'^(.+?)\.(\d{4})\.',      # Title.2013.
            r'^(.+?)_(\d{4})_',        # Title_2013_
            r'^(.+?)\s+(\d{4})\s',     # Title 2013
        ]

        title = None
        year = None

        for pattern in patterns:
            match = re.match(pattern, name_without_ext)
            if match:
                title = match.group(1).replace('.', ' ').replace('_', ' ').strip()
                year = int(match.group(2))
                break

        if not title:
            # Fallback: use the part before common release markers
            title = re.split(r'[\.\s](2160p|1080p|720p|480p|BluRay|WEB|HDTV|MULTi|FRENCH|TRUEFRENCH|VFF|VOSTFR|REMUX)', name_without_ext, flags=re.IGNORECASE)[0]
            title = title.replace('.', ' ').replace('_', ' ').strip()

        if not title:
            logger.warning(f"Could not extract title from filename: {filename}")
            return None

        # If no year found from filename, try to extract from parent folder name
        # e.g. "Cloud 9, l'ultime figure (2014)" -> year=2014
        if not year:
            try:
                parent_name = file_path.parent.name
                if parent_name and parent_name != '.':
                    folder_year_match = re.search(r'\((\d{4})\)', parent_name)
                    if folder_year_match:
                        year = int(folder_year_match.group(1))
                        logger.info(f"Year extracted from parent folder: {year}")
            except Exception:
                pass

        logger.info(f"Extracted from filename: title='{title}', year={year}")

        # Search TMDB
        content_type = "tv" if is_tv_show else "movie"
        result = await self.search_by_title(title, year, content_type)

        if result:
            # Add poster and backdrop URLs
            extra_data = result.get('extra_data', {})

            # TMDB image base URL
            image_base = "https://image.tmdb.org/t/p/w500"

            if 'poster_path' in extra_data and extra_data['poster_path']:
                result['poster_url'] = f"{image_base}{extra_data['poster_path']}"
            else:
                result['poster_url'] = None

            if 'backdrop_path' in extra_data and extra_data['backdrop_path']:
                result['backdrop_url'] = f"https://image.tmdb.org/t/p/original{extra_data['backdrop_path']}"
            else:
                result['backdrop_url'] = None

            result['genres'] = extra_data.get('genres', [])

        return result

    async def search_movies_autocomplete(
        self,
        query: str,
        limit: int = 8,
        year: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Search TMDB and return multiple results for autocomplete.

        Unlike search_by_title(), this method returns a list of search results
        without fetching full metadata, optimized for autocomplete UIs.

        Args:
            query: Movie title to search for (without year)
            limit: Maximum number of results to return (default: 8)
            year: Optional release year to filter results

        Returns:
            List of dictionaries with basic movie info:
                - tmdb_id: TMDB movie ID
                - title: Movie title
                - original_title: Original title
                - year: Release year
                - poster_path: Poster path (relative, prepend base URL)

        Example:
            >>> results = await cache_service.search_movies_autocomplete("Harry Potter")
            >>> for movie in results:
            ...     print(f"{movie['title']} ({movie['year']}) - ID: {movie['tmdb_id']}")
        """
        import asyncio
        import requests

        if not query or len(query) < 2:
            logger.warning("Search query too short (min 2 chars)")
            return []

        api_key = self._get_api_key()

        # Detect credential type and format request
        try:
            params, headers = format_tmdb_request(api_key)
        except ValueError as e:
            raise TrackerAPIError(f"Invalid TMDB credential: {e}")

        # TMDB search endpoint
        url = "https://api.themoviedb.org/3/search/movie"

        # Add search parameters
        params['query'] = query
        params['language'] = 'fr-FR'  # French language for search results
        if year:
            params['year'] = year  # Use TMDB's year filter parameter

        logger.info(f"Searching TMDB autocomplete for: '{query}'" + (f" (year={year})" if year else ""))

        try:
            response = await asyncio.to_thread(
                requests.get,
                url,
                params=params,
                headers=headers,
                timeout=10
            )

            if response.status_code != 200:
                logger.warning(f"TMDB search failed with HTTP {response.status_code}")
                return []

            data = response.json()
            results = data.get('results', [])

            if not results:
                logger.info(f"No TMDB results found for: '{query}'" + (f" (year={year})" if year else ""))
                return []

            # Convert results to simplified format
            autocomplete_results = []
            for movie in results[:limit]:
                # Extract year from release_date
                release_date = movie.get('release_date', '')
                movie_year = None
                if release_date and len(release_date) >= 4:
                    try:
                        movie_year = int(release_date[:4])
                    except ValueError:
                        pass

                autocomplete_results.append({
                    'tmdb_id': str(movie.get('id')),
                    'title': movie.get('title', 'Unknown'),
                    'original_title': movie.get('original_title', ''),
                    'year': movie_year,
                    'poster_path': movie.get('poster_path', ''),
                })

            logger.info(f"Found {len(autocomplete_results)} TMDB results for '{query}'")
            return autocomplete_results

        except requests.exceptions.RequestException as e:
            logger.error(f"TMDB search request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in TMDB autocomplete search: {e}")
            return []

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics for monitoring.

        Returns:
            Dictionary with cache statistics:
                - total_entries: Total number of cached entries
                - expired_entries: Number of expired entries
                - valid_entries: Number of valid (non-expired) entries

        Example:
            >>> stats = cache_service.get_cache_stats()
            >>> logger.info(f"Cache: {stats['valid_entries']} valid, "
            ...             f"{stats['expired_entries']} expired")
        """
        from datetime import datetime

        total = self.db.query(TMDBCache).count()
        expired = self.db.query(TMDBCache).filter(
            TMDBCache.expires_at <= datetime.utcnow()
        ).count()

        stats = {
            'total_entries': total,
            'expired_entries': expired,
            'valid_entries': total - expired
        }

        logger.debug(f"Cache stats: {stats}")
        return stats


def get_tmdb_service(db: Session) -> TMDBCacheService:
    """
    Factory function to create a TMDBCacheService instance.

    Args:
        db: SQLAlchemy database session

    Returns:
        TMDBCacheService instance
    """
    return TMDBCacheService(db)
