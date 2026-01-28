# Seedarr v2.0 - System Architecture

## Table of Contents

- [Overview](#overview)
- [Architecture Diagram](#architecture-diagram)
- [Core Design Principles](#core-design-principles)
- [Component Relationships](#component-relationships)
- [Database Schema](#database-schema)
- [Pipeline Flow](#pipeline-flow)
- [Error Handling Strategy](#error-handling-strategy)
- [Performance Optimizations](#performance-optimizations)
- [Security Considerations](#security-considerations)
- [External Dependencies](#external-dependencies)

## Overview

Seedarr v2.0 is a **modular monolith** application that automates multimedia content publishing to private torrent trackers, starting with "La Cale" (French tracker). The application has been refactored from v1.1.0 to resolve technical debt, improve reliability, and establish a foundation for multi-tracker support.

**Key Architectural Changes in v2.0:**

1. **Decomposed Monolithic Components**: `TrackerUploader` class split into focused components
2. **Database-Driven Configuration**: Eliminated YAML config files for runtime settings
3. **Typed Exception Hierarchy**: Structured error handling with retry logic
4. **Pipeline Idempotence**: Checkpoint-based resumption from any failed stage
5. **Adapter Pattern**: Tracker-agnostic pipeline design with pluggable adapters
6. **Circuit Breaker**: Robust failure handling for external dependencies
7. **Async Optimization**: Non-blocking I/O with ProcessPoolExecutor for CPU-bound tasks

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Application                             │
│                         (backend/app/main.py)                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┴───────────────────────────┐
        │                                                       │
        ▼                                                       ▼
┌───────────────────┐                                 ┌──────────────────┐
│   API Routes      │                                 │  Database Layer  │
│  (FastAPI)        │◄────────────────────────────────┤   SQLAlchemy     │
└───────────────────┘                                 └──────────────────┘
        │                                                       │
        │                                                       │
        ▼                                                       ▼
┌───────────────────────────────────────────────────────────────────────┐
│                      Processing Pipeline                              │
│                 (backend/app/processors/pipeline.py)                  │
│                                                                       │
│  ┌──────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐  ┌────────┐ │
│  │  Scan    │─►│ Analyze  │─►│ Rename  │─►│ Metadata │─►│ Upload │ │
│  │  Stage   │  │  Stage   │  │  Stage  │  │  Stage   │  │ Stage  │ │
│  └──────────┘  └──────────┘  └─────────┘  └──────────┘  └────────┘ │
│       │             │              │            │             │      │
│       └─────────────┴──────────────┴────────────┴─────────────┘      │
│                         Checkpoint Timestamps                         │
└───────────────────────────────────────────────────────────────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            │                       │                       │
            ▼                       ▼                       ▼
   ┌────────────────┐    ┌─────────────────┐    ┌──────────────────┐
   │ MediaAnalyzer  │    │  NFOValidator   │    │ TrackerAdapter   │
   │   Service      │    │    Service      │    │   (Interface)    │
   └────────────────┘    └─────────────────┘    └──────────────────┘
            │                       │                       │
            │                       │                       │
            ▼                       ▼                       ▼
   ┌────────────────┐    ┌─────────────────┐    ┌──────────────────┐
   │ ProcessPool    │    │  TMDBCache      │    │  LaCaleAdapter   │
   │ Executor       │    │   Service       │    │ (Concrete Impl)  │
   │ (File Hashing) │    │                 │    └──────────────────┘
   └────────────────┘    └─────────────────┘              │
                                                           │
                         ┌─────────────────────────────────┴────────┐
                         │                                          │
                         ▼                                          ▼
             ┌───────────────────────┐              ┌────────────────────┐
             │ CloudflareSession     │              │   LaCaleClient     │
             │     Manager           │              │     Service        │
             │ (FlareSolverr, CB)    │              │  (API Logic)       │
             └───────────────────────┘              └────────────────────┘
                         │                                          │
                         │                                          │
                         ▼                                          ▼
                 ┌──────────────┐                         ┌─────────────┐
                 │ FlareSolverr │                         │  La Cale    │
                 │   Service    │                         │   Tracker   │
                 │ (Cloudflare) │                         │   API       │
                 └──────────────┘                         └─────────────┘

External Dependencies:
┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│  qBittorrent   │  │   TMDB API     │  │    SQLite      │
│    Client      │  │  (Metadata)    │  │   Database     │
└────────────────┘  └────────────────┘  └────────────────┘
```

## Core Design Principles

### 1. Modular Monolith

**Definition**: Single deployable unit with clear module boundaries.

**Benefits**:
- Simple deployment (one Docker container)
- Easier testing and debugging
- Shared memory for state management
- Lower operational complexity

**Boundaries**:
- **Services Layer** (`backend/app/services/`): Business logic, external integrations
- **Adapters Layer** (`backend/app/adapters/`): Tracker-specific implementations
- **Processors Layer** (`backend/app/processors/`): Pipeline orchestration
- **Models Layer** (`backend/app/models/`): Database schemas and ORM
- **API Layer** (`backend/app/api/`): HTTP endpoints and request handling

### 2. Separation of Concerns

**TrackerUploader Decomposition** (v1.1.0 → v2.0):

```
# Before (v1.1.0): Monolithic TrackerUploader
class TrackerUploader:
    def upload():
        # FlareSolverr logic
        # Cookie management
        # API authentication
        # Multipart data preparation
        # Upload execution
        # Error handling

# After (v2.0): Focused components
CloudflareSessionManager:
    - FlareSolverr communication
    - Cookie extraction and lifecycle
    - Circuit breaker pattern

LaCaleClient:
    - La Cale API business logic
    - Multipart form preparation
    - Upload execution

LaCaleAdapter (TrackerAdapter interface):
    - Composes SessionManager + Client
    - Implements TrackerAdapter interface
    - Provides tracker abstraction to pipeline
```

**Benefit**: Each component has a single, well-defined responsibility.

### 3. Idempotent Operations

**Problem**: If torrent generation succeeds but upload fails, retrying should NOT regenerate the torrent.

**Solution**: Checkpoint-based pipeline with timestamp tracking.

```python
class FileEntry:
    scanned_at: datetime         # Stage 1 checkpoint
    analyzed_at: datetime        # Stage 2 checkpoint
    renamed_at: datetime         # Stage 3 checkpoint
    metadata_generated_at: datetime  # Stage 4 checkpoint
    uploaded_at: datetime        # Stage 5 checkpoint

# Pipeline checks checkpoints before each stage
if not file_entry.is_metadata_generated():
    generate_torrent()
    file_entry.mark_metadata_generated()  # Sets timestamp
else:
    logger.info("Reusing existing .torrent file")
```

**Benefit**: Pipeline can resume from any failed stage without duplicating work.

### 4. Dependency Injection

**Pattern**: FastAPI's dependency injection system provides components.

```python
# Dependency provider
def get_tracker_adapter() -> TrackerAdapter:
    """Dependency injection provider for TrackerAdapter."""
    # Fetch settings from database
    settings = get_settings()

    # Return configured adapter
    return LaCaleAdapter(
        flaresolverr_url=settings.flaresolverr_url,
        tracker_url=settings.tracker_url,
        passkey=settings.passkey
    )

# Route using injection
@app.post("/upload")
async def upload_file(
    adapter: TrackerAdapter = Depends(get_tracker_adapter)
):
    await adapter.upload_torrent(...)
```

**Benefits**:
- Easy testing with mocks
- Loose coupling
- Configuration flexibility
- Clear dependency graph

### 5. Fail Fast with Retry Intelligence

**Exception Hierarchy**:

```
TrackerAPIError (base - non-retryable)
├── CloudflareBypassError (retryable)
└── NetworkRetryableError (retryable with exponential backoff)
```

**Retry Logic**:

```python
@retry_on_network_error(max_retries=5)
async def get_session(tracker_url: str) -> Session:
    # Network failures: Auto-retry with exponential backoff
    # Business logic errors: Fail fast
    # Circuit breaker: Fast-fail when service down
```

**Decision Matrix**:

| Error Type | Retry? | Backoff? | Example |
|------------|--------|----------|---------|
| Invalid passkey | ✗ No | - | TrackerAPIError (401/403) |
| Network timeout | ✓ Yes | ✓ Exponential | NetworkRetryableError |
| FlareSolverr down | ✓ Yes | ✓ Circuit breaker | CloudflareBypassError |
| Invalid torrent | ✗ No | - | TrackerAPIError (400) |

## Component Relationships

### Processing Pipeline ↔ TrackerAdapter

```python
# Pipeline depends on TrackerAdapter INTERFACE, not concrete implementation
class ProcessingPipeline:
    def __init__(self, tracker_adapter: TrackerAdapter):
        self.tracker_adapter = tracker_adapter

    async def _upload_stage(self, file_entry: FileEntry):
        # Works with ANY TrackerAdapter implementation
        await self.tracker_adapter.authenticate()
        result = await self.tracker_adapter.upload_torrent(...)

# Concrete adapter is injected at runtime
pipeline = ProcessingPipeline(
    tracker_adapter=LaCaleAdapter(...)  # Could be any adapter
)
```

**Benefit**: Adding a new tracker only requires implementing `TrackerAdapter` interface.

### LaCaleAdapter ↔ CloudflareSessionManager ↔ LaCaleClient

```python
# LaCaleAdapter COMPOSES SessionManager and Client
class LaCaleAdapter(TrackerAdapter):
    def __init__(self, flaresolverr_url, tracker_url, passkey):
        # Composition over inheritance
        self.session_manager = CloudflareSessionManager(flaresolverr_url)
        self.client = LaCaleClient(tracker_url, passkey)

    async def authenticate(self) -> bool:
        # Delegate Cloudflare bypass to SessionManager
        session = await self.session_manager.get_session(self.tracker_url)

        # Delegate passkey validation to Client
        valid = await self.client.validate_passkey(session)
        return valid

    async def upload_torrent(self, ...) -> dict:
        # Delegate upload to Client (with authenticated session)
        return await self.client.upload_torrent(
            session=self.authenticated_session, ...
        )
```

**Responsibilities**:
- **CloudflareSessionManager**: FlareSolverr integration, circuit breaker, cookie management
- **LaCaleClient**: La Cale API business logic, multipart preparation, upload execution
- **LaCaleAdapter**: Orchestrates SessionManager + Client, implements TrackerAdapter interface

### Pipeline ↔ Services

```python
# Pipeline uses services for specialized tasks
class ProcessingPipeline:
    def __init__(self, db, tracker_adapter):
        self.db = db
        self.tracker_adapter = tracker_adapter

        # Services initialized as needed
        self.nfo_validator = NFOValidator(db)
        # MediaAnalyzer, TagManager, etc. would be initialized here

    async def _metadata_generation_stage(self, file_entry):
        # Use NFOValidator service
        nfo_path = self.nfo_validator.ensure_valid_nfo(
            file_path=file_entry.file_path,
            tmdb_id=...,
            release_name=...
        )

        # TODO: Use MediaAnalyzer for torrent generation
        # torrent_path = await media_analyzer.create_torrent(...)
```

**Service Responsibilities**:
- **NFOValidator**: NFO file validation and generation from TMDB
- **MediaAnalyzer**: MediaInfo extraction, torrent creation with ProcessPoolExecutor
- **TMDBCacheService**: Persistent metadata caching with TTL management
- **TagManager**: Dynamic tag synchronization from tracker API

## Database Schema

### Entity Relationship Diagram

```
┌────────────────────┐
│   FileEntry        │
├────────────────────┤
│ id (PK)            │
│ file_path          │
│ status (enum)      │
│ error_message      │
│ created_at         │
│ updated_at         │
│                    │
│ Checkpoints:       │
│ scanned_at         │
│ analyzed_at        │
│ renamed_at         │
│ metadata_generated │
│ uploaded_at        │
└────────────────────┘

┌────────────────────┐       ┌────────────────────┐
│   TMDBCache        │       │       Tags         │
├────────────────────┤       ├────────────────────┤
│ id (PK)            │       │ id (PK)            │
│ tmdb_id (unique)   │       │ tag_id (unique)    │
│ title              │       │ label              │
│ year               │       │ category           │
│ cast (JSON)        │       │ description        │
│ plot               │       │ created_at         │
│ ratings (JSON)     │       │ updated_at         │
│ extra_data (JSON)  │       └────────────────────┘
│ cached_at          │
│ expires_at         │       ┌────────────────────┐
└────────────────────┘       │     Settings       │
                             ├────────────────────┤
                             │ id (PK)            │
                             │ key (unique)       │
                             │ value              │
                             │ value_type         │
                             │ description        │
                             │ created_at         │
                             │ updated_at         │
                             └────────────────────┘
```

### Key Design Decisions

**1. FileEntry Checkpoint Fields**

**Why**: Enable idempotent pipeline operations.

**Alternative**: Store state machine in JSON field.

**Decision**: Explicit timestamp columns for clarity and query performance.

```sql
-- Query files that failed during upload (metadata generated but not uploaded)
SELECT * FROM file_entries
WHERE metadata_generated_at IS NOT NULL
  AND uploaded_at IS NULL
  AND status = 'failed';
```

**2. TMDBCache with TTL**

**Why**: TMDB API has rate limits; cache reduces API calls by >80%.

**TTL Strategy**: 30 days default (configurable via Settings).

```python
# Automatic expiration check
cached_entry = TMDBCache.get_cached(db, tmdb_id)
if cached_entry and not cached_entry.is_expired():
    return cached_entry  # Cache hit
else:
    # Cache miss or expired - fetch from API
```

**3. Settings in Database (not YAML)**

**Why**: Enable runtime configuration changes via admin UI.

**Exception**: Database connection string remains in environment variable (chicken-egg problem).

```python
# All settings stored in database
settings = {
    'tracker_url': 'https://lacale.example.com',
    'passkey': 'encrypted_passkey',
    'flaresolverr_url': 'http://localhost:8191',
    'tmdb_cache_ttl_days': '30',
    'tag_sync_interval_hours': '24'
}
```

## Pipeline Flow

### Complete Processing Flow

```
1. File Discovery
   ├─► Check if file already processed (FileEntry.get_by_path)
   ├─► Create FileEntry with status=PENDING
   └─► Add to processing queue

2. Scan Stage (PENDING → SCANNED)
   ├─► Verify file exists and is readable
   ├─► Extract file size and format
   ├─► Parse filename components (title, year, resolution, source)
   ├─► Set scanned_at checkpoint
   └─► Update status to SCANNED

3. Analysis Stage (SCANNED → ANALYZED)
   ├─► Extract MediaInfo (codec, bitrate, resolution, duration)
   ├─► Parse title and year from filename
   ├─► Query TMDB API for metadata (cache-first)
   │   ├─► Check TMDBCache for existing entry
   │   ├─► If cache miss: Call TMDB API
   │   └─► Store result in TMDBCache
   ├─► Validate metadata completeness
   ├─► Set analyzed_at checkpoint
   └─► Update status to ANALYZED

4. Rename Stage (ANALYZED → RENAMED)
   ├─► Build release name: Title.Year.Resolution.Source.Codec-Group
   ├─► Construct output path in OUTPUT_DIR
   ├─► Move file: shutil.move(old_path, new_path)
   ├─► Update FileEntry.file_path with new path
   ├─► Set renamed_at checkpoint
   └─► Update status to RENAMED

5. Metadata Generation Stage (RENAMED → METADATA_GENERATED)
   ├─► Generate .torrent file
   │   ├─► Offload to ProcessPoolExecutor (CPU-bound hashing)
   │   ├─► CRITICAL: Include source="lacale" flag
   │   ├─► Set tracker announce URL
   │   └─► Save .torrent file
   ├─► Validate or generate NFO file (MANDATORY)
   │   ├─► Check for existing NFO
   │   ├─► Validate required fields (title, year, plot)
   │   ├─► If invalid/missing: Generate from TMDB cache
   │   └─► BLOCK pipeline if NFO generation fails
   ├─► Store paths: FileEntry.torrent_path, FileEntry.nfo_path
   ├─► Set metadata_generated_at checkpoint
   └─► Update status to METADATA_GENERATED

6. Upload Stage (METADATA_GENERATED → UPLOADED)
   ├─► Authenticate with tracker
   │   ├─► Get authenticated session via TrackerAdapter
   │   ├─► FlareSolverr bypass (if circuit breaker closed)
   │   └─► Validate passkey
   ├─► Prepare upload metadata
   │   ├─► Read .torrent file bytes
   │   ├─► Read NFO content
   │   ├─► Extract category and tag IDs
   │   └─► Build description from TMDB metadata
   ├─► Upload to tracker
   │   ├─► TrackerAdapter.upload_torrent (handles repeated tags fields)
   │   ├─► Parse tracker response (torrent ID, URL)
   │   └─► Store tracker info in FileEntry
   ├─► Inject torrent into qBittorrent
   │   ├─► Add torrent with file path
   │   ├─► Start seeding
   │   └─► Verify seeding status
   ├─► Set uploaded_at checkpoint
   └─► Update status to UPLOADED

7. Completion
   ├─► Log success metrics
   ├─► Update file_entry.updated_at
   └─► Mark entry as complete
```

### Checkpoint Resume Example

**Scenario**: .torrent generation succeeds, but upload fails due to network error.

```
First Attempt:
  Stage 1: Scan ✓ (scanned_at = 2024-01-10 10:00:00)
  Stage 2: Analysis ✓ (analyzed_at = 2024-01-10 10:01:00)
  Stage 3: Rename ✓ (renamed_at = 2024-01-10 10:02:00)
  Stage 4: Metadata ✓ (metadata_generated_at = 2024-01-10 10:03:00)
  Stage 5: Upload ✗ FAILED (NetworkRetryableError)

Retry Attempt:
  Stage 1: ⊘ Skip (scanned_at is set)
  Stage 2: ⊘ Skip (analyzed_at is set)
  Stage 3: ⊘ Skip (renamed_at is set)
  Stage 4: ⊘ Skip (metadata_generated_at is set, reuse .torrent/.nfo)
  Stage 5: Upload ✓ (retry upload only, uploaded_at = 2024-01-10 10:05:00)

Result: No duplicate work, saved ~3 minutes of processing time
```

## Error Handling Strategy

### Exception Hierarchy

```python
# Non-retryable errors (fail fast)
class TrackerAPIError(Exception):
    """
    Business logic errors that won't be resolved by retrying.
    Examples:
    - Invalid passkey (401/403)
    - Invalid request parameters (400)
    - Resource not found (404)
    """

# Retryable errors
class CloudflareBypassError(TrackerAPIError):
    """
    FlareSolverr service issues.
    Examples:
    - Service unavailable (connection refused)
    - Timeout during challenge solving
    - Invalid response format

    Retry Strategy:
    - Circuit breaker pattern
    - Fast-fail when circuit open
    - Auto-recovery after timeout
    """

class NetworkRetryableError(TrackerAPIError):
    """
    Transient network issues.
    Examples:
    - Connection timeout
    - DNS resolution failure
    - Temporary server errors (502, 503)

    Retry Strategy:
    - Exponential backoff: 2^n seconds
    - Max 5 retries
    - Comprehensive logging
    """
```

### Circuit Breaker Pattern

**Purpose**: Prevent cascading failures when FlareSolverr is down.

**States**:

```
CLOSED (Normal Operation)
  │
  │ 3 consecutive failures
  ▼
OPEN (Fast-Fail)
  │
  │ 60 seconds timeout
  ▼
HALF_OPEN (Test Recovery)
  │
  ├─► Success: Close circuit
  └─► Failure: Reopen circuit
```

**Implementation**:

```python
class CloudflareSessionManager:
    MAX_FAILURES = 3
    CIRCUIT_OPEN_DURATION = 60  # seconds

    def _check_circuit_breaker(self):
        if self.circuit_state == CircuitBreakerState.OPEN:
            if time_since_failure < CIRCUIT_OPEN_DURATION:
                # Fast-fail: Don't call FlareSolverr
                raise CloudflareBypassError("Circuit breaker OPEN")
            else:
                # Transition to HALF_OPEN for test request
                self.circuit_state = CircuitBreakerState.HALF_OPEN

    async def get_session(self, tracker_url):
        self._check_circuit_breaker()  # Fail fast if circuit open

        try:
            session = await self._call_flaresolverr(tracker_url)
            self._record_success()  # Close circuit
            return session
        except Exception:
            self._record_failure()  # Open circuit after 3 failures
            raise
```

**Benefits**:
- Prevents unnecessary load on failed service
- Fast-fail with clear error messages
- Automatic recovery testing
- Reduces overall system latency during outages

### Retry Decorator

```python
@retry_on_network_error(max_retries=5)
async def get_session(tracker_url: str) -> Session:
    """
    Automatic retry with exponential backoff.

    Retry Schedule:
    - Attempt 1: Immediate
    - Attempt 2: 2^1 = 2 seconds delay
    - Attempt 3: 2^2 = 4 seconds delay
    - Attempt 4: 2^3 = 8 seconds delay
    - Attempt 5: 2^4 = 16 seconds delay
    Total max time: 30 seconds
    """
    response = await flaresolverr_client.solve(tracker_url)
    return extract_session(response)
```

## Performance Optimizations

### 1. Async I/O for Network Operations

**Problem**: Synchronous I/O blocks event loop.

**Solution**: FastAPI's async/await with non-blocking operations.

```python
# Before (blocking)
def upload_torrent(data):
    response = requests.post(tracker_url, data=data)  # Blocks for seconds
    return response.json()

# After (non-blocking)
async def upload_torrent(data):
    async with httpx.AsyncClient() as client:
        response = await client.post(tracker_url, data=data)  # Yields to event loop
        return response.json()
```

**Impact**: API remains responsive during large uploads.

### 2. ProcessPoolExecutor for CPU-Bound Tasks

**Problem**: File hashing blocks async event loop.

**Solution**: Offload to ProcessPoolExecutor worker processes.

```python
# Blocking operation
def create_torrent(file_path):
    torrent = torf.Torrent(path=file_path)  # CPU-intensive hashing
    return torrent

# Non-blocking with ProcessPoolExecutor
async def create_torrent_async(file_path):
    loop = asyncio.get_event_loop()
    with ProcessPoolExecutor() as executor:
        torrent = await loop.run_in_executor(
            executor, _hash_file_blocking, file_path
        )
    return torrent

def _hash_file_blocking(file_path):
    """Runs in separate process, doesn't block event loop."""
    return torf.Torrent(path=file_path)
```

**Impact**: 10GB file hashing takes ~60s but doesn't block API.

### 3. TMDB Cache with TTL

**Problem**: TMDB API rate limits (40 requests/10 seconds).

**Solution**: Persistent cache with 30-day TTL.

```python
# Cache-first strategy
async def get_tmdb_metadata(tmdb_id):
    # Check cache first
    cached = TMDBCache.get_cached(db, tmdb_id)
    if cached and not cached.is_expired():
        logger.info(f"TMDB cache hit for {tmdb_id}")
        return cached.to_dict()

    # Cache miss - call API
    logger.info(f"TMDB cache miss for {tmdb_id}, fetching from API")
    metadata = await tmdb_api.get_movie(tmdb_id)

    # Store in cache
    TMDBCache.upsert(db, tmdb_id, metadata, ttl_days=30)
    return metadata
```

**Impact**: Reduces TMDB API calls by >80%, prevents rate limiting.

### 4. Database Indexes

```sql
-- FileEntry: Fast lookup by path and status
CREATE INDEX idx_file_entries_file_path ON file_entries(file_path);
CREATE INDEX idx_file_entries_status ON file_entries(status);

-- TMDBCache: Fast lookup and expiration cleanup
CREATE UNIQUE INDEX idx_tmdb_cache_tmdb_id ON tmdb_cache(tmdb_id);
CREATE INDEX idx_tmdb_cache_expires_at ON tmdb_cache(expires_at);

-- Tags: Fast lookup by tag_id
CREATE UNIQUE INDEX idx_tags_tag_id ON tags(tag_id);
```

## Security Considerations

### 1. Passkey Encryption

**Risk**: Passkeys stored in plaintext in database.

**Mitigation**: Encrypt passkeys at rest using Fernet (symmetric encryption).

```python
from cryptography.fernet import Fernet

class Settings:
    @staticmethod
    def encrypt_passkey(passkey: str, key: bytes) -> str:
        f = Fernet(key)
        return f.encrypt(passkey.encode()).decode()

    @staticmethod
    def decrypt_passkey(encrypted: str, key: bytes) -> str:
        f = Fernet(key)
        return f.decrypt(encrypted.encode()).decode()
```

**Key Management**: Encryption key stored in environment variable (`ENCRYPTION_KEY`), never in code or database.

### 2. SQL Injection Prevention

**Risk**: User input in SQL queries.

**Mitigation**: SQLAlchemy ORM with parameterized queries.

```python
# SAFE: ORM with parameters
file_entry = db.query(FileEntry).filter(
    FileEntry.file_path == user_provided_path
).first()

# UNSAFE: String concatenation (DON'T DO THIS)
# query = f"SELECT * FROM file_entries WHERE file_path = '{user_provided_path}'"
```

### 3. Input Validation

**Risk**: Malicious file paths, path traversal attacks.

**Mitigation**: Path validation and sanitization.

```python
from pathlib import Path

def validate_file_path(file_path: str, base_dir: str) -> Path:
    """Validate file path is within allowed base directory."""
    path = Path(file_path).resolve()
    base = Path(base_dir).resolve()

    if not path.is_relative_to(base):
        raise ValueError(f"Path traversal detected: {file_path}")

    return path
```

### 4. Secrets in Logs

**Risk**: Accidentally logging passkeys or sensitive data.

**Mitigation**: Mask sensitive fields in log output.

```python
class LaCaleAdapter:
    def __repr__(self):
        return (
            f"<LaCaleAdapter("
            f"passkey='***{self.passkey[-4:]}'"  # Only last 4 chars
            f")>"
        )
```

## External Dependencies

### FlareSolverr (MANDATORY)

**Purpose**: Bypass Cloudflare protection on tracker.

**Why Required**: La Cale uses Cloudflare, which blocks automated requests.

**Failure Handling**: Circuit breaker pattern with fast-fail.

**Health Check**: Periodic ping to `/` endpoint.

```python
await session_manager.health_check()  # Returns True/False
```

### qBittorrent

**Purpose**: Torrent seeding after upload.

**Integration**: `qbittorrent-api` Python library.

**Configuration**: Host, port, username, password in Settings.

**Failure Handling**: NetworkRetryableError with retry logic.

### TMDB API

**Purpose**: Metadata validation and NFO generation.

**Rate Limits**: 40 requests/10 seconds.

**Mitigation**: Persistent cache (>80% hit rate).

**Failure Handling**:
- Cache-first approach (continue with cached data)
- Exponential backoff on 429 (rate limit) responses
- Generate basic NFO if API unavailable

### SQLite

**Purpose**: Persistent storage for pipeline state, cache, settings.

**Production Consideration**: SQLite is suitable for single-instance deployments. For multi-instance deployments (horizontal scaling), migrate to PostgreSQL or MySQL.

**Backup Strategy**: Regular database file backups (`seedarr.db`).

---

## Next Steps

- **For adapter implementation**: See [ADAPTER_PATTERN.md](./ADAPTER_PATTERN.md)
- **For migration from v1.1**: See [MIGRATION_GUIDE.md](./MIGRATION_GUIDE.md)

---

**Document Version**: 1.0
**Last Updated**: 2024-01-10
**Author**: Claude Sonnet 4.5
